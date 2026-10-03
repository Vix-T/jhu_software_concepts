"""Last pull outcome: GET /pull-status and the page banner, for no result, success, and failure."""

import pytest
from bs4 import BeautifulSoup
from conftest import FakeScraper

import pull_data
from flask_app import create_app
from busy_state import InMemoryBusyState

pytestmark = pytest.mark.buttons


def _banner(client):
    """The last-pull banner's text as a browser renders it (whitespace collapsed)."""
    soup = BeautifulSoup(client.get("/analysis").data, "html.parser")
    return " ".join(soup.select_one('[data-testid="last-pull"]').get_text().split())


def test_no_pull_yet(client):
    response = client.get("/pull-status")

    assert response.status_code == 200
    assert response.get_json() == {"running": False, "last_result": None}
    assert _banner(client) == "No pull has run yet."


def test_after_successful_pull(client, fake_records):
    assert client.post("/pull-data").status_code == 200

    status = client.get("/pull-status").get_json()
    assert status["running"] is False
    last = status["last_result"]
    assert {k: last[k] for k in ("ok", "inserted", "skipped", "failed", "error")} == {
        "ok": True, "inserted": 3, "skipped": 0, "failed": 0, "error": None,
    }
    assert _banner(client) == f"Last pull succeeded: 3 new rows (finished {last['finished_at']})"


def test_after_failed_pull(make_app, loader_spy):
    client = make_app(scraper=FakeScraper(raises=RuntimeError("Chrome session lost")), loader=loader_spy).test_client()
    assert client.post("/pull-data").status_code == 500

    last = client.get("/pull-status").get_json()["last_result"]
    assert last["ok"] is False
    assert last["error"] == "Chrome session lost"
    assert last["inserted"] == 0
    assert _banner(client) == f"Last pull failed: Chrome session lost (finished {last['finished_at']})"


def test_status_reports_running_and_subprocess_result(client, busy_state, pull_result_path):
    # A pull subprocess wrote this result file; the app only reads it.
    pull_data.write_pull_result(str(pull_result_path), pull_data.pull_result(run={"inserted": 7}))
    assert busy_state.try_acquire()

    status = client.get("/pull-status").get_json()

    assert status["running"] is True
    assert status["last_result"]["inserted"] == 7
    assert _banner(client).startswith("Last pull succeeded: 7 new rows")


def test_configured_result_path(test_database_url, tmp_path, fake_records, pull_result_path):
    configured = tmp_path / "configured.json"
    client = create_app(
        {"DB_URL": test_database_url, "TESTING": True, "PULL_RESULT_PATH": str(configured)},
        scraper=FakeScraper(fake_records),
        busy_state=InMemoryBusyState(),
    ).test_client()

    assert client.post("/pull-data").status_code == 200

    assert pull_data.read_pull_result(str(configured))["inserted"] == 3
    assert not pull_result_path.exists()  # the environment default was not used
    assert client.get("/pull-status").get_json()["last_result"]["inserted"] == 3


def test_failure_banner_does_not_repeat_prefix(client, pull_result_path):
    # The pull subprocess records its full CLI message, which starts with "PULL FAILED:".
    pull_data.write_pull_result(
        str(pull_result_path), pull_data.pull_result(error=pull_data.CLOUDFLARE_PRECONDITION_MESSAGE)
    )

    banner = _banner(client)

    assert banner.startswith("Last pull failed: could not attach to Chrome for scraping. This scraper requires")
    assert "PULL FAILED" not in banner
    # Display-only: the recorded result keeps the full message.
    assert pull_data.read_pull_result(str(pull_result_path))["error"] == pull_data.CLOUDFLARE_PRECONDITION_MESSAGE
    assert client.get("/pull-status").get_json()["last_result"]["error"].startswith("PULL FAILED:")
