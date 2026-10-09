"""Rows written by Pull Data, dedup on URL, and the row/analysis query functions."""

import pytest

import load_data
from models import make_session_factory
from orm_queries import get_analysis, get_applicants

pytestmark = pytest.mark.db

MODULE3_FIELDS = {
    "p_id", "program", "comments", "date_added", "url", "status", "term",
    "us_or_international", "gpa", "gre", "gre_v", "gre_aw", "degree",
    "llm_generated_program", "llm_generated_university",
}
LLM_FIELDS = {"llm_generated_program", "llm_generated_university"}

ANALYSIS_KEYS = {
    "q1_count", "q2_num", "q2_denom", "q2_pct", "q3", "q4_avg", "q4_n",
    "q5_num", "q5_denom", "q5_pct", "q6_avg", "q6_n", "q7_count", "q8_count",
    "q9_count", "q8_q9_diff", "custom1_contaminated", "custom1_total",
    "custom1_pct", "custom2_rows",
}


@pytest.fixture
def session(test_database_url):
    with make_session_factory(test_database_url)() as session:
        yield session


def test_pull_inserts_rows_with_required_fields(client, row_count, fetch_rows, fake_records):
    assert row_count() == 0

    response = client.post("/pull-data")
    assert response.status_code == 200

    rows = fetch_rows()
    assert len(rows) == len(fake_records)
    assert {row["url"] for row in rows} == {r["URL"] for r in fake_records}
    for row in rows:
        for field in MODULE3_FIELDS - LLM_FIELDS:
            assert row[field] is not None, field
        # Pull Data never runs the LLM-cleaning step (Module 3 design).
        for field in LLM_FIELDS:
            assert row[field] is None, field


def test_pull_twice_idempotent(client, row_count, fake_records):
    first = client.post("/pull-data")
    assert first.get_json()["inserted"] == len(fake_records)
    count_after_first = row_count()

    second = client.post("/pull-data")
    assert second.status_code == 200
    assert second.get_json()["inserted"] == 0
    assert row_count() == count_after_first


def test_load_rows_duplicates_skipped_not_failed(seed, fake_records):
    assert seed(fake_records) == (3, 0, [])
    assert seed(fake_records) == (0, 3, [])


def test_get_applicants_keys(seed, session, fake_records):
    seed(fake_records)

    rows = get_applicants(session)
    assert len(rows) == 3
    for row in rows:
        assert set(row.keys()) == MODULE3_FIELDS
    assert [row["url"] for row in rows] == [r["URL"] for r in fake_records]
    assert len(get_applicants(session, limit=2)) == 2


def test_get_analysis_keys(seed, session, fake_records):
    seed(fake_records)

    analysis = get_analysis(session)
    assert set(analysis.keys()) == ANALYSIS_KEYS
    assert analysis["q1_count"] == 3
