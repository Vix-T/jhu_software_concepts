"""End-to-end: queue -> recompute -> status -> render, and overlapping pulls.

The web side is real (Flask app, publisher.py, read queries); only
pika.BlockingConnection is faked. The worker's recompute step is the real
query_data.refresh_summary() it will run for each recompute_analytics task.
"""

import re

import pytest
from bs4 import BeautifulSoup
from conftest import FakeScraper, make_record, make_records

import pull_data

pytestmark = pytest.mark.integration


def _page_text(client):
    response = client.get("/analysis")
    assert response.status_code == 200
    return BeautifulSoup(response.data, "html.parser").get_text(" ", strip=True)


def test_queue_recompute_then_render(client, broker, seed, refresh_summary, real_loader, row_count):
    assert "Analysis not computed yet" in _page_text(client)
    assert client.get("/api/status").get_json()["computed_at"] is None

    records = [
        make_record(0, **{"Semester and Year": "Fall 2026", "International/American": "International"}),
        make_record(1, **{"Semester and Year": "Fall 2026", "International/American": "American"}),
        make_record(2, **{"Semester and Year": "Fall 2026", "International/American": "American"}),
        make_record(3, **{"Semester and Year": "Fall 2025", "Applicant Status": "Rejected"}),
    ]
    assert pull_data.run_pull(FakeScraper(records), real_loader)["inserted"] == 4
    assert row_count() == 4

    update = client.post("/update-analysis")
    assert update.status_code == 202
    assert update.get_json() == {"status": "queued", "task": "recompute_analytics"}
    assert [body["kind"] for body in broker.bodies()] == ["recompute_analytics"]
    assert "Analysis not computed yet" in _page_text(client)  # nothing changes until the worker runs

    refresh_summary()  # what the worker does for a recompute_analytics message

    status = client.get("/api/status").get_json()
    assert status["computed_at"] is not None and status["row_count"] == 4
    after = _page_text(client)
    assert "Fall 2026 applicant count: 3" in after
    assert "Percent international: 25.00%" in after
    assert "Fall 2025 acceptance percentage: 0.00%" in after
    percentages = re.findall(r"\d+(?:\.\d+)?%", after)
    assert percentages
    assert all(re.fullmatch(r"\d+\.\d{2}%", p) for p in percentages), percentages


def test_overlapping_pulls_count_unique(real_loader, row_count):
    batch_a = make_records(4, start=0)
    batch_b = make_records(4, start=2)  # shares records 2 and 3 with batch A
    unique_urls = {r["URL"] for r in batch_a + batch_b}

    first = pull_data.run_pull(FakeScraper(batch_a), real_loader)
    second = pull_data.run_pull(FakeScraper(batch_b), real_loader)

    assert (first["inserted"], first["skipped"]) == (4, 0)
    assert (second["inserted"], second["skipped"]) == (2, 2)
    assert row_count() == len(unique_urls) == 6
