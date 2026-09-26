"""End-to-end: pull -> update -> render, and overlapping pulls."""

import re

import pytest
from bs4 import BeautifulSoup
from conftest import FakeScraper, make_record, make_records

pytestmark = pytest.mark.integration


def _page_text(client):
    response = client.get("/analysis")
    assert response.status_code == 200
    return BeautifulSoup(response.data, "html.parser").get_text(" ", strip=True)


def test_pull_update_render(make_app, real_loader, row_count):
    records = [
        make_record(0, **{"Semester and Year": "Fall 2026", "International/American": "International"}),
        make_record(1, **{"Semester and Year": "Fall 2026", "International/American": "American"}),
        make_record(2, **{"Semester and Year": "Fall 2026", "International/American": "American"}),
        make_record(3, **{"Semester and Year": "Fall 2025", "Applicant Status": "Rejected"}),
    ]
    client = make_app(scraper=FakeScraper(records), loader=real_loader).test_client()

    before = _page_text(client)
    assert "Fall 2026 applicant count: 0" in before
    assert "Percent international: N/A" in before

    pull = client.post("/pull-data")
    assert pull.status_code == 200
    assert pull.get_json() == {"ok": True, "inserted": 4}
    assert row_count() == 4

    # The page shows the cached analysis until Update Analysis runs.
    assert "Fall 2026 applicant count: 0" in _page_text(client)

    update = client.post("/update-analysis")
    assert update.status_code == 200
    assert update.get_json() == {"ok": True}

    after = _page_text(client)
    assert "Fall 2026 applicant count: 3" in after
    assert "Percent international: 25.00%" in after
    assert "Fall 2025 acceptance percentage: 0.00%" in after
    percentages = re.findall(r"\d+(?:\.\d+)?%", after)
    assert percentages
    assert all(re.fullmatch(r"\d+\.\d{2}%", p) for p in percentages), percentages


def test_overlapping_pulls_count_unique(make_app, busy_state, real_loader, row_count):
    batch_a = make_records(4, start=0)
    batch_b = make_records(4, start=2)  # shares records 2 and 3 with batch A
    unique_urls = {r["URL"] for r in batch_a + batch_b}

    first = make_app(scraper=FakeScraper(batch_a), loader=real_loader).test_client().post("/pull-data")
    second = make_app(scraper=FakeScraper(batch_b), loader=real_loader).test_client().post("/pull-data")

    assert first.get_json() == {"ok": True, "inserted": 4}
    assert second.get_json() == {"ok": True, "inserted": 2}
    assert row_count() == len(unique_urls) == 6
