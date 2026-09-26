"""load_data.py: parsing helpers, load_rows, and the CLI (JSON file -> test database).

Uses the test database only: conftest points DATABASE_URL at
TEST_DATABASE_URL for the whole run, which is what load_data.main()
connects to.
"""

import json
import os
import runpy
import sys
from datetime import date

import pytest
from conftest import make_record

import load_data

pytestmark = pytest.mark.db

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


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


def test_parse_float():
    assert load_data.parse_float("3.5") == 3.5
    assert load_data.parse_float(None) is None
    assert load_data.parse_float("n/a") is None


def test_parse_date():
    assert load_data.parse_date("Sep 08, 2026") == date(2026, 9, 8)
    assert load_data.parse_date("") is None
    assert load_data.parse_date(None) is None
    assert load_data.parse_date("2026-09-08") is None


def test_build_program_combinations():
    assert load_data.build_program({"University": " MIT ", "Program Name": " Physics "}) == "MIT, Physics"
    assert load_data.build_program({"University": "MIT", "Program Name": ""}) == "MIT"
    assert load_data.build_program({"University": None, "Program Name": "Physics"}) == "Physics"
    assert load_data.build_program({}) is None


def test_database_error_on_one_record_keeps_the_rest(db_conn, fetch_rows):
    # psycopg2 can't adapt a dict for the comments column: that insert fails
    # inside its savepoint, and the records before and after it still load.
    records = [make_record(0), make_record(1, Comments={"nested": "object"}), make_record(2)]

    inserted, skipped, failed = load_data.load_rows(records, db_conn)

    assert (inserted, skipped) == (2, 0)
    [(index, reason)] = failed
    assert index == 1
    assert "can't adapt type 'dict'" in reason
    assert [row["url"] for row in fetch_rows()] == [records[0]["URL"], records[2]["URL"]]


def test_cli_loads_file_named_on_command_line(tmp_path, monkeypatch, capsys, fetch_rows):
    good = make_record(0)
    no_url = make_record(1)
    del no_url["URL"]
    path = _write(tmp_path, [good, no_url])
    monkeypatch.setattr(sys, "argv", ["load_data.py", path])

    runpy.run_path(os.path.join(SRC_DIR, "load_data.py"), run_name="__main__")

    assert [row["url"] for row in fetch_rows()] == [good["URL"]]
    assert capsys.readouterr().out == (
        "Load summary:\n"
        "  Inserted:           1\n"
        "  Skipped duplicates: 0\n"
        "  Failed to parse:    1\n"
        "  Failure details (first 10):\n"
        "    record[1]: missing URL (required as natural key)\n"
    )
