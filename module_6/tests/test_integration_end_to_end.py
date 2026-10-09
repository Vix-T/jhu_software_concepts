"""End-to-end: button -> published message -> worker -> stored data -> page and status.

The web side is real (Flask app, publisher.py, read queries) and so is the
worker (consumer.process_message and its handlers, on the real test
database). Only pika.BlockingConnection is faked: the body the web publishes
is handed to the worker as-is. The scrape handler reads the pull fixture
pages through FakeDriver instead of attaching to Chrome.
"""

import functools
import json
import re

import pytest
from bs4 import BeautifulSoup
from conftest import PULL_PAGE_URLS, DriverFactory, deliver_method, make_record, pull_pages, result_url

import consumer
import load_data
from etl import incremental_scraper

pytestmark = pytest.mark.integration


def _page_text(client):
    response = client.get("/analysis")
    assert response.status_code == 200
    return BeautifulSoup(response.data, "html.parser").get_text(" ", strip=True)


def _worker_tasks():
    browser = incremental_scraper.BrowserSettings(
        driver_factory=DriverFactory(pull_pages()), start_url=PULL_PAGE_URLS[0], delay_seconds=0
    )
    return {**consumer.TASKS, "scrape_new_data": functools.partial(consumer.handle_scrape_new_data, browser=browser)}


def _run_worker(db_conn, channel, broker, tasks):
    """Hand every message the web published to the worker, in order."""
    for tag, message in enumerate(broker.published, start=1):
        consumer.process_message(db_conn, channel, deliver_method(tag), message["body"], tasks)
    broker.published.clear()


def test_update_analysis_end_to_end(client, broker, channel, db_conn, seed):
    seed([
        make_record(0, **{"Semester and Year": "Fall 2026", "International/American": "International"}),
        make_record(1, **{"Semester and Year": "Fall 2026", "International/American": "American"}),
        make_record(2, **{"Semester and Year": "Fall 2026", "International/American": "American"}),
        make_record(3, **{"Semester and Year": "Fall 2025", "Applicant Status": "Rejected"}),
    ])
    assert client.get("/analysis").status_code == 503  # no summary yet: the initialising page

    assert client.post("/update-analysis").status_code == 202
    assert client.get("/analysis").status_code == 503  # nothing changes until the worker runs
    _run_worker(db_conn, channel, broker, consumer.TASKS)

    assert broker.acks == [1]
    status = client.get("/api/status").get_json()
    assert status["computed_at"] is not None and status["row_count"] == 4
    after = _page_text(client)
    assert "Fall 2026 applicant count: 3" in after
    assert "Percent international: 25.00%" in after
    assert "Fall 2025 acceptance percentage: 0.00%" in after
    percentages = re.findall(r"\d+(?:\.\d+)?%", after)
    assert percentages
    assert all(re.fullmatch(r"\d+\.\d{2}%", p) for p in percentages), percentages


def test_pull_data_end_to_end(client, broker, channel, db_conn, tmp_path):
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps([make_record(i, URL=result_url(5004 + i)) for i in range(5)]))
    load_data.initialize_database(db_conn, str(seed_file))  # what the worker does at startup: watermark 5008
    before = client.get("/api/status").get_json()
    assert before["computed_at"] is None

    response = client.post("/pull-data")
    assert response.get_json() == {"status": "queued", "task": "scrape_new_data"}
    _run_worker(db_conn, channel, broker, _worker_tasks())

    assert broker.acks == [1] and broker.nacks == []
    after = client.get("/api/status").get_json()
    assert after["row_count"] == 7  # 5 seeded + 5010 and 5009
    assert after["computed_at"] is not None  # a successful scrape recomputes the summary
    assert after["watermark_updated_at"] > before["watermark_updated_at"]
    text = _page_text(client)
    assert "Fall 2026 applicant count: 7" in text  # all 7 rows are Fall 2026, the new ones included
    assert "Data last updated: never" not in text
    api = client.get("/api/applicants", query_string={"sort": "p_id", "order": "desc", "limit": "2"}).get_json()
    assert [row["url"] for row in api["rows"]] == [result_url(5009), result_url(5010)]


def test_second_pull_finds_nothing_new(client, broker, channel, db_conn, tables):
    tasks = _worker_tasks()
    client.post("/pull-data")
    _run_worker(db_conn, channel, broker, tasks)
    first = client.get("/api/status").get_json()

    client.post("/pull-data")
    _run_worker(db_conn, channel, broker, tasks)
    second = client.get("/api/status").get_json()

    assert broker.acks == [1, 1]
    assert first["row_count"] == second["row_count"] == 8
    assert second["watermark_updated_at"] > first["watermark_updated_at"]
