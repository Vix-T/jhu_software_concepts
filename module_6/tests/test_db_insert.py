"""Rows written by a pull, dedup on URL, and the stored analysis summary."""

import pytest

import pull_data

pytestmark = pytest.mark.db

MODULE3_FIELDS = {
    "p_id", "program", "comments", "date_added", "url", "status", "term",
    "us_or_international", "gpa", "gre", "gre_v", "gre_aw", "degree",
    "llm_generated_program", "llm_generated_university",
}
LLM_FIELDS = {"llm_generated_program", "llm_generated_university"}

# Every value the analysis page renders (the keys of query_data.summary_from_answers()).
ANALYSIS_KEYS = {
    "q1_count", "q2_num", "q2_denom", "q2_pct", "q3", "q4_avg", "q4_n",
    "q5_num", "q5_denom", "q5_pct", "q6_avg", "q6_n", "q7_count", "q8_count",
    "q9_count", "q8_q9_diff", "custom1_contaminated", "custom1_total",
    "custom1_pct", "custom2_rows",
}


def test_pull_inserts_rows_with_required_fields(scraper, real_loader, row_count, fetch_rows, fake_records):
    assert row_count() == 0

    result = pull_data.run_pull(scraper, real_loader)
    assert result["inserted"] == len(fake_records)

    rows = fetch_rows()
    assert len(rows) == len(fake_records)
    assert {row["url"] for row in rows} == {r["URL"] for r in fake_records}
    for row in rows:
        assert set(row) == MODULE3_FIELDS
        for field in MODULE3_FIELDS - LLM_FIELDS:
            assert row[field] is not None, field
        # A pull never runs the LLM-cleaning step (Module 3 design).
        for field in LLM_FIELDS:
            assert row[field] is None, field


def test_pull_twice_idempotent(scraper, real_loader, row_count, fake_records):
    first = pull_data.run_pull(scraper, real_loader)
    assert first["inserted"] == len(fake_records)
    count_after_first = row_count()

    second = pull_data.run_pull(scraper, real_loader)
    assert (second["inserted"], second["skipped"]) == (0, len(fake_records))
    assert row_count() == count_after_first


def test_load_rows_duplicates_skipped_not_failed(seed, fake_records):
    assert seed(fake_records) == (3, 0, [])
    assert seed(fake_records) == (0, 3, [])


def test_stored_summary_has_every_page_key(seed, refresh_summary, db_conn, fake_records):
    seed(fake_records)

    summary = refresh_summary()

    assert set(summary) == ANALYSIS_KEYS
    assert summary["q1_count"] == 3
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT id, results, row_count FROM analysis_summary")
        [(row_id, stored, row_count)] = cur.fetchall()
    assert (row_id, row_count) == (1, 3)
    assert list(stored) == list(summary)  # stored as JSON, in the same key order
    assert list(stored["q3"]) == ["GPA", "GRE", "GRE V", "GRE AW"]
    assert stored["q1_count"] == 3


def test_refresh_replaces_the_single_summary_row(seed, refresh_summary, db_conn, fake_records):
    refresh_summary()
    seed(fake_records)
    refresh_summary()

    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT row_count, results->>'q1_count' FROM analysis_summary")
        assert cur.fetchall() == [(3, "3")]
