"""Query builders return (composed stmt, params): LIMIT and values are always bound parameters.

stmt.as_string(conn) renders the SQL text psycopg2 will send *before*
parameters are bound, so these tests can assert that user values and fixed
filter values never appear in it.
"""

import pytest
from conftest import make_records
from psycopg2 import sql

import load_data
from etl import query_data
from app.applicant_search import build_applicants_query, escape_like
from sql_utils import MAX_LIMIT

pytestmark = pytest.mark.db

ANALYSIS_BUILDERS = {
    "q1": query_data.q1_query,
    "q2": query_data.q2_query,
    "q3_gpa": lambda: query_data.q3_query("gpa", None),
    "q3_gre": lambda: query_data.q3_query("gre", (query_data.GRE_MIN, query_data.GRE_MAX)),
    "q4": query_data.q4_query,
    "q5": query_data.q5_query,
    "q6": query_data.q6_query,
    "q7": query_data.q7_query,
    "q8": lambda: query_data.q8_q9_query("program", "program"),
    "q9": lambda: query_data.q8_q9_query("llm_generated_program", "llm_generated_university"),
    "breakdown": lambda: query_data.breakdown_query("program", "program", query_data.MIT_PATTERN),
    "custom1": query_data.custom1_query,
}


@pytest.mark.parametrize("name", sorted(ANALYSIS_BUILDERS))
def test_aggregate_builders_bind_every_value_and_limit_1(name, db_conn):
    stmt, params = ANALYSIS_BUILDERS[name]()
    text = stmt.as_string(db_conn)

    assert isinstance(stmt, sql.Composed)
    assert text.rstrip().endswith("LIMIT %s")
    assert params[-1] == 1
    assert text.count("%s") == len(params)
    assert '"applicants"' in text
    for fixed_value in ("Fall 2026", "Fall 2025", "Accepted", "PhD", "Masters", "American", "International"):
        assert fixed_value not in text
    for value in params[:-1]:
        if isinstance(value, str) and value:
            assert value not in text


def test_q8_q9_or_clause_is_joined_identifiers(db_conn):
    q8_text = query_data.q8_q9_query("program", "program")[0].as_string(db_conn)
    q9_text = query_data.q8_q9_query("llm_generated_program", "llm_generated_university")[0].as_string(db_conn)

    assert q8_text.count('"program" ~* %s') == 1 + len(query_data.Q8_Q9_UNIVERSITIES)
    assert " OR ".join(['"llm_generated_university" ~* %s'] * len(query_data.Q8_Q9_UNIVERSITIES)) in q9_text
    assert '"llm_generated_program" ~* %s' in q9_text


def test_custom2_limit_is_max_limit(db_conn):
    stmt, params = query_data.custom2_query()
    assert stmt.as_string(db_conn).rstrip().endswith("LIMIT %s")
    assert params == ["Accepted", MAX_LIMIT]


def test_insert_and_create_are_composed_without_limit(db_conn):
    insert_text = load_data.INSERT_SQL.as_string(db_conn)
    assert insert_text.startswith('INSERT INTO "applicants" ("program", "comments"')
    for column in load_data.INSERT_COLUMNS:
        assert f"%({column})s" in insert_text
    assert insert_text.endswith('ON CONFLICT ("url") DO NOTHING')
    assert "LIMIT" not in insert_text
    assert 'CREATE TABLE IF NOT EXISTS "applicants" (' in load_data.CREATE_TABLE_SQL.as_string(db_conn)
    assert load_data.SAVEPOINT_SQL.as_string(db_conn) == 'SAVEPOINT "load_row"'
    assert load_data.ROLLBACK_TO_SAVEPOINT_SQL.as_string(db_conn) == 'ROLLBACK TO SAVEPOINT "load_row"'
    assert load_data.RELEASE_SAVEPOINT_SQL.as_string(db_conn) == 'RELEASE SAVEPOINT "load_row"'


@pytest.mark.parametrize(
    "university",
    ["' OR '1'='1", "x'; DROP TABLE applicants; --", "Robert'); DELETE FROM applicants;--"],
)
def test_search_builder_never_puts_user_text_in_sql(university, db_conn):
    stmt, params = build_applicants_query(limit=5, sort="date_added", order="desc", university=university)
    text = stmt.as_string(db_conn)

    assert university not in text
    assert "DROP" not in text and "DELETE" not in text and "'1'='1" not in text
    assert '"llm_generated_university" ILIKE %s ESCAPE %s' in text
    assert 'ORDER BY "date_added" DESC NULLS LAST, "p_id" LIMIT %s' in text
    assert params == ["%" + university + "%", "\\", 5]


def test_search_builder_without_filter(db_conn):
    stmt, params = build_applicants_query()
    text = stmt.as_string(db_conn)

    assert "WHERE" not in text
    assert text.startswith('SELECT "p_id", "program", "date_added"')
    assert '"comments"' not in text and "*" not in text
    assert text.endswith('ORDER BY "p_id" ASC NULLS LAST, "p_id" LIMIT %s')
    assert params == [10]


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [("%", "\\%"), ("_", "\\_"), ("a\\b", "a\\\\b"), ("50%_off", "50\\%\\_off"), ("Stanford", "Stanford")],
)
def test_escape_like(raw, escaped):
    assert escape_like(raw) == escaped
