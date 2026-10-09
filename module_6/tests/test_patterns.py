"""University/program regex patterns, evaluated by PostgreSQL itself (\\y is PostgreSQL syntax).

Mirrors the grader's check: short and long forms of a school's name must
both count, while word boundaries still reject look-alikes.
"""

import pytest
from conftest import make_record

import query_data

pytestmark = pytest.mark.analysis

JHU_MATCHES = [
    "JHU", "Johns Hopkins", "Johns Hopkins University", "John Hopkins", "john hopkins university",
    "JHU, Computer Science", "Johns Hopkins Bloomberg School of Public Health", "University of John Hopkins",
]
JHU_REJECTS = ["JHUAPL", "Hopkins", "Hopkins Marine Station", "Johnson Hopkinsville", "St Johns Hopkinsville", "jhump"]

GEORGETOWN_MATCHES = [
    "Georgetown", "Georgetown University", "georgetown university", "Georgetown, Computer Science",
    "Georgetown University School of Foreign Service (SFS)",
]
GEORGETOWN_REJECTS = ["Georgetown College", "Georgetown College, Computer Science", "Georgetowne", "Georgetownship"]


@pytest.fixture
def pg_match(db_conn):
    def match(pattern, text):
        with db_conn, db_conn.cursor() as cur:
            cur.execute("SELECT %s ~* %s", (text, pattern))
            return cur.fetchone()[0]

    return match


@pytest.mark.parametrize("text", JHU_MATCHES)
def test_jhu_pattern_matches(pg_match, text):
    assert pg_match(query_data.JHU_PATTERN, text) is True


@pytest.mark.parametrize("text", JHU_REJECTS)
def test_jhu_pattern_rejects(pg_match, text):
    assert pg_match(query_data.JHU_PATTERN, text) is False


@pytest.mark.parametrize("text", GEORGETOWN_MATCHES)
def test_georgetown_pattern_matches(pg_match, text):
    assert pg_match(query_data.GEORGETOWN_PATTERN, text) is True


@pytest.mark.parametrize("text", GEORGETOWN_REJECTS)
def test_georgetown_pattern_rejects(pg_match, text):
    assert pg_match(query_data.GEORGETOWN_PATTERN, text) is False


def test_georgetown_is_the_q8_q9_pattern():
    assert dict(query_data.Q8_Q9_UNIVERSITIES)["Georgetown"] == query_data.GEORGETOWN_PATTERN


def _school_row(i, university, degree, **overrides):
    return make_record(i, **{"University": university, "Program Name": "Computer Science",
                             "Masters or PhD": degree, **overrides})


def test_short_and_long_forms_both_count(seed, db_conn):
    seed([
        # Q7: JHU + Masters + CS, short and long forms.
        _school_row(0, "JHU", "Masters", **{"Semester and Year": "Fall 2025"}),
        _school_row(1, "Johns Hopkins University", "Masters", **{"Semester and Year": "Fall 2025"}),
        # Q8: Fall 2026 + Accepted + PhD + CS at Georgetown, short and long forms.
        _school_row(2, "Georgetown", "PhD"),
        _school_row(3, "Georgetown University", "PhD"),
        # Look-alikes that must not count.
        _school_row(4, "Georgetown College", "PhD"),
        _school_row(5, "Hopkins Marine Station", "Masters"),
    ])

    with db_conn, db_conn.cursor() as cur:
        assert query_data.q7(cur) == 2
        assert query_data.q8(cur) == 2
