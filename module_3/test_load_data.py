"""Deterministic test of load_data.py's insert/dedup behavior.

Exercises the real INSERT ... ON CONFLICT (url) DO NOTHING path against
the actual database, using a synthetic entry with a UUID-tagged URL --
independent of whether Grad Cafe happens to have any new live entries
on a given day.
"""

import json
import uuid

import pytest

import load_data
from models import Applicant, Session


def _synthetic_record(url):
    return {
        "Program Name": "Test Program",
        "University": "Test University",
        "Comments": "synthetic test entry",
        "Date Added": "Jan 01, 2026",
        "URL": url,
        "Applicant Status": "Accepted",
        "Semester and Year": "Fall 2026",
        "International/American": "American",
        "GRE Score": "320",
        "GRE V Score": "160",
        "GRE AW Score": "4.5",
        "Masters or PhD": "PhD",
        "GPA": "3.9",
        "llm-generated-program": "Test Program",
        "llm-generated-university": "Test University",
    }


@pytest.fixture
def synthetic_entry_file(tmp_path):
    url = f"https://www.thegradcafe.com/test-entry-{uuid.uuid4()}"
    path = tmp_path / "synthetic_entry.json"
    path.write_text(json.dumps([_synthetic_record(url)]))

    yield str(path), url

    session = Session()
    try:
        session.query(Applicant).filter(Applicant.url == url).delete()
        session.commit()
    finally:
        session.close()


def test_insert_then_dedup(synthetic_entry_file):
    path, url = synthetic_entry_file

    inserted, skipped, failed = load_data.main(data_file=path)
    assert (inserted, skipped, failed) == (1, 0, [])

    session = Session()
    try:
        row = session.query(Applicant).filter(Applicant.url == url).one()
        assert row.program == "Test University, Test Program"
        assert row.status == "Accepted"
        assert row.gpa == 3.9
    finally:
        session.close()

    inserted, skipped, failed = load_data.main(data_file=path)
    assert (inserted, skipped, failed) == (0, 1, [])


def test_missing_url_fails_to_parse(tmp_path):
    record = _synthetic_record(None)
    del record["URL"]
    path = tmp_path / "missing_url.json"
    path.write_text(json.dumps([record]))

    inserted, skipped, failed = load_data.main(data_file=str(path))
    assert inserted == 0
    assert skipped == 0
    assert len(failed) == 1
