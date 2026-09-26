"""Pull Data and Update Analysis: JSON contract, busy gating, and the error path."""

import pytest
from conftest import FakeScraper, make_records

pytestmark = pytest.mark.buttons


def test_pull_data_ok(client, scraper, loader_spy, fake_records):
    response = client.post("/pull-data")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "inserted": len(fake_records)}
    assert scraper.calls == 1
    assert loader_spy.calls == [(fake_records,)]


def test_update_analysis_ok(client, refresh_spy):
    response = client.post("/update-analysis")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert len(refresh_spy.calls) == 1


def test_update_analysis_busy(client, busy_state, refresh_spy):
    assert busy_state.try_acquire()
    response = client.post("/update-analysis")
    assert response.status_code == 409
    assert response.get_json() == {"busy": True}
    assert refresh_spy.calls == []


def test_pull_data_busy(client, busy_state, scraper, loader_spy, row_count):
    assert busy_state.try_acquire()
    response = client.post("/pull-data")
    assert response.status_code == 409
    assert response.get_json() == {"busy": True}
    assert scraper.calls == 0
    assert loader_spy.calls == []
    assert row_count() == 0


def test_busy_released_after_pull(client, busy_state):
    response = client.post("/pull-data")
    assert response.status_code == 200
    assert busy_state.is_busy() is False


def test_pull_scraper_raises(make_app, busy_state, loader_spy, row_count):
    scraper = FakeScraper(raises=RuntimeError("Chrome session lost"))
    client = make_app(scraper=scraper, loader=loader_spy).test_client()

    response = client.post("/pull-data")
    assert response.status_code == 500
    assert response.get_json() == {"ok": False, "error": "Chrome session lost"}
    assert loader_spy.calls == []
    assert row_count() == 0
    assert busy_state.is_busy() is False


def test_pull_loader_fails_midbatch_rolls_back(make_app, busy_state, real_loader, row_count):
    # Two valid records are inserted first; the third is not a dict, so
    # load_rows raises AttributeError mid-batch -- an unexpected error that
    # must roll back the whole batch, not just the bad record.
    records = make_records(2) + ["not a record"]
    client = make_app(scraper=FakeScraper(records), loader=real_loader).test_client()

    response = client.post("/pull-data")
    assert response.status_code == 500
    body = response.get_json()
    assert body["ok"] is False
    assert "get" in body["error"]
    assert row_count() == 0
    assert busy_state.is_busy() is False
