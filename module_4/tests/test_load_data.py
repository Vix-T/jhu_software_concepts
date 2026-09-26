"""load_data.py end to end (JSON file -> test database): insert, dedup, bad records.

Uses the test database only: conftest points DATABASE_URL at
TEST_DATABASE_URL for the whole run, which is what load_data.main()
connects to.
"""

import json

import pytest
from conftest import make_record

import load_data

pytestmark = pytest.mark.db


def _write(tmp_path, records):
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records))
    return str(path)


def test_insert_then_dedup(tmp_path, fetch_rows):
    record = make_record(0, **{"University": "Test University", "Program Name": "Test Program",
                               "GPA": "3.9", "Applicant Status": "Accepted"})
    path = _write(tmp_path, [record])

    assert load_data.main(data_file=path) == (1, 0, [])

    rows = fetch_rows()
    assert len(rows) == 1
    assert rows[0]["url"] == record["URL"]
    assert rows[0]["program"] == "Test University, Test Program"
    assert rows[0]["status"] == "Accepted"
    assert rows[0]["gpa"] == 3.9

    assert load_data.main(data_file=path) == (0, 1, [])
    assert len(fetch_rows()) == 1


def test_missing_url_fails_to_parse(tmp_path, row_count):
    record = make_record(0)
    del record["URL"]
    path = _write(tmp_path, [record])

    inserted, skipped, failed = load_data.main(data_file=path)
    assert (inserted, skipped) == (0, 0)
    assert failed == [(0, "missing URL (required as natural key)")]
    assert row_count() == 0
