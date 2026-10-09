"""The web app factory, the analysis page rendered from analysis_summary, and GET /api/status.

The summary is written the way the worker writes it (conftest refresh_summary
-> query_data.refresh_summary); the last pull time through
load_data.advance_watermark(). Everything is read back from the real test
database.
"""

import socket
from datetime import datetime, timezone

import psycopg2
import pytest
from bs4 import BeautifulSoup
from flask import Flask

import load_data
from app import create_app
from app import db as app_db
from app.config import ConfigError
from conftest import make_record

pytestmark = pytest.mark.web


def _rules(app):
    return {rule.rule: rule.methods for rule in app.url_map.iter_rules()}


def _soup(response):
    return BeautifulSoup(response.data, "html.parser")


def _text(response):
    return _soup(response).get_text(" ", strip=True)


def _db_now(db_conn):
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT now()")
        return cur.fetchone()[0]


def _pull(db_conn, last_seen=1000):
    with db_conn, db_conn.cursor() as cur:
        load_data.advance_watermark(cur, last_seen)


def _stored_times(db_conn):
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT computed_at FROM analysis_summary")
        computed = cur.fetchone()
        cur.execute("SELECT updated_at FROM ingestion_watermarks")
        pulled = cur.fetchone()
    return (computed[0] if computed else None), (pulled[0] if pulled else None)


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def test_create_app_registers_routes(app):
    assert isinstance(app, Flask)
    rules = _rules(app)
    assert "GET" in rules["/"]
    assert "GET" in rules["/analysis"]
    assert "POST" in rules["/pull-data"] and "GET" not in rules["/pull-data"]
    assert "POST" in rules["/update-analysis"] and "GET" not in rules["/update-analysis"]
    assert "GET" in rules["/api/status"]
    assert "GET" in rules["/api/applicants"]
    assert "/pull-status" not in rules


def test_create_app_reads_database_url_from_environment(monkeypatch, test_database_url):
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    assert create_app().config["DATABASE_URL"] == test_database_url


def test_create_app_without_database_url_is_config_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ConfigError, match="DATABASE_URL is not set"):
        create_app()


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


INITIALIZING_MESSAGE = (
    "The database is being initialised. The first start loads about 60,000 rows "
    "and takes around a minute. This page refreshes automatically."
)


def _refresh_seconds(soup):
    """The <meta http-equiv="refresh"> delay, or None when the page doesn't refresh itself."""
    meta = soup.find("meta", attrs={"http-equiv": "refresh"})
    return None if meta is None else meta["content"]


def _assert_initializing_page(response):
    assert response.status_code == 503
    soup = _soup(response)
    assert soup.select_one('[data-testid="db-initializing"]').get_text(" ", strip=True) == INITIALIZING_MESSAGE
    assert _refresh_seconds(soup) == "5"
    assert soup.select('[data-testid="db-unavailable"]') == []
    assert soup.select("section.question") == []
    assert len(soup.select('[data-testid="pull-data-btn"]')) == 1
    assert len(soup.select('[data-testid="update-analysis-btn"]')) == 1
    assert b"Traceback" not in response.data


def test_first_start_without_a_summary_is_the_initializing_page(client, caplog):
    # Tables exist (the `tables` fixture), but the worker hasn't stored the first summary.
    response = client.get("/analysis")

    _assert_initializing_page(response)
    assert "Analysis page: database initializing (no analysis summary yet)" in caplog.text


def test_page_renders_stored_summary(client, seed, refresh_summary):
    seed([make_record(0), make_record(1, **{"Semester and Year": "Fall 2025"})])
    refresh_summary()

    response = client.get("/analysis")

    assert response.status_code == 200
    soup = _soup(response)
    assert soup.select('[data-testid="db-initializing"]') == []
    assert _refresh_seconds(soup) is None  # a normal page never reloads itself
    text = _text(response)
    assert "Fall 2026 applicant count: 1" in text
    assert text.count("Answer:") == len(soup.select("section.question")) == 11


def test_page_shows_snapshot_not_live_rows(client, seed, refresh_summary):
    seed([make_record(0)])
    refresh_summary()
    seed([make_record(1), make_record(2)])  # newer rows: shown only after the next recompute

    assert "Fall 2026 applicant count: 1" in _text(client.get("/"))
    refresh_summary()
    assert "Fall 2026 applicant count: 3" in _text(client.get("/"))


def test_root_renders_same_page(client, refresh_summary):
    refresh_summary()
    root = _soup(client.get("/"))
    analysis = _soup(client.get("/analysis"))

    assert root.find("main") == analysis.find("main")
    assert root.find("div", class_="actions") == analysis.find("div", class_="actions")


def test_timestamps_show_last_update_and_last_pull(client, db_conn, refresh_summary):
    refresh_summary()
    _pull(db_conn)
    computed_at, pulled_at = _stored_times(db_conn)

    soup = _soup(client.get("/"))

    updated = soup.select_one('[data-testid="analysis-updated"]').get_text(strip=True)
    pulled = soup.select_one('[data-testid="data-pulled"]').get_text(strip=True)
    assert updated == f"Analysis last updated: {computed_at.astimezone(timezone.utc):%Y-%m-%d %H:%M:%S} UTC"
    assert pulled == f"Data last updated: {pulled_at.astimezone(timezone.utc):%Y-%m-%d %H:%M:%S} UTC"
    stamps = soup.select_one('[data-testid="timestamps"]')
    assert stamps["data-computed-at"] == _iso(computed_at)
    assert stamps["data-pulled-at"] == _iso(pulled_at)
    assert stamps["data-status-url"] == "/api/status"


def test_page_has_hidden_queued_banner_and_polling_script(client):
    soup = _soup(client.get("/"))

    banner = soup.select_one('[data-testid="queued-banner"]')
    assert banner.has_attr("hidden") and banner.get_text(strip=True) == ""
    script = soup.find("script").get_text()
    assert "POLL_INTERVAL_MS = 2000" in script
    assert "MAX_WAIT_MS = 60000" in script
    assert "Request queued:" in script
    assert "window.location.reload()" in script
    assert "status.computed_at" in script and "status.watermark_updated_at" in script


def test_subtitle_describes_worker_snapshot(client):
    subtitle = _soup(client.get("/analysis")).select_one("p.subtitle").get_text(" ", strip=True)

    assert subtitle == "SQL query results from the applicants table, computed by the worker at the last Update Analysis."


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------


def test_status_before_anything_ran(client):
    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.get_json() == {"computed_at": None, "watermark_updated_at": None, "row_count": None}


def test_status_reports_both_timestamps_and_row_count(client, db_conn, seed, refresh_summary):
    before = _db_now(db_conn)
    seed([make_record(0), make_record(1)])
    refresh_summary()
    _pull(db_conn)
    computed_at, pulled_at = _stored_times(db_conn)

    body = client.get("/api/status").get_json()

    assert body == {
        "computed_at": _iso(computed_at),
        "watermark_updated_at": _iso(pulled_at),
        "row_count": 2,
    }
    assert datetime.fromisoformat(body["computed_at"]) >= before


def test_status_changes_when_the_worker_recomputes(client, seed, refresh_summary):
    refresh_summary()
    first = client.get("/api/status").get_json()
    seed([make_record(0)])

    refresh_summary()
    second = client.get("/api/status").get_json()

    assert second["computed_at"] > first["computed_at"]
    assert (first["row_count"], second["row_count"]) == (0, 1)
    assert second["watermark_updated_at"] is None


# ---------------------------------------------------------------------------
# Database failures: 503 with a message, never a traceback
# ---------------------------------------------------------------------------


def _closed_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def down_client(make_app):
    return make_app(f"postgresql://nobody@127.0.0.1:{_closed_port()}/unreachable_test").test_client()


@pytest.mark.parametrize("path", ["/", "/analysis"])
def test_page_is_503_when_database_down(down_client, caplog, path):
    response = down_client.get(path)

    assert response.status_code == 503
    soup = _soup(response)
    message = soup.select_one('[data-testid="db-unavailable"]').get_text(" ", strip=True)
    assert message.startswith("Database unavailable: the analysis could not be loaded.")
    assert soup.select("section.question") == []
    assert soup.select('[data-testid="timestamps"]') == []
    assert len(soup.select('[data-testid="pull-data-btn"]')) == 1
    assert b"Traceback" not in response.data
    assert soup.select('[data-testid="db-initializing"]') == []
    assert _refresh_seconds(soup) is None  # a down database keeps its own message, no auto-refresh
    assert "Analysis page: database unavailable" in caplog.text
    assert any(record.exc_info for record in caplog.records)  # logged with its traceback


def test_status_is_503_when_database_down(down_client, caplog):
    response = down_client.get("/api/status")

    assert response.status_code == 503
    assert response.get_json() == {"error": "database unavailable"}
    assert "Status: database unavailable" in caplog.text


def test_tables_missing_is_the_initializing_page(make_app, caplog):
    # conftest drops analysis_summary and ingestion_watermarks before each test;
    # this app is built without the `tables` fixture, so they don't exist.
    client = make_app().test_client()

    _assert_initializing_page(client.get("/"))
    status = client.get("/api/status")
    assert status.status_code == 503 and status.get_json() == {"error": "database initializing"}
    assert "Analysis page: database initializing" in caplog.text
    assert not any(record.exc_info for record in caplog.records)  # expected: a warning, no traceback


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ('connection to server at "db" (172.18.0.2), port 5432 failed: FATAL:  '
         'password authentication failed for user "gradcafe_web"', "database initializing"),
        ('connection to server on socket "/tmp/.s.PGSQL.5432" failed: FATAL:  '
         'role "gradcafe_web" does not exist', "database initializing"),
        ('connection to server at "127.0.0.1", port 1 failed: Connection refused', "database unavailable"),
        ('connection to server at "db", port 5432 failed: FATAL:  database "gradcafe" does not exist',
         "database unavailable"),
    ],
    ids=["password-refused", "role-missing", "refused", "database-missing"],
)
def test_login_refusal_means_initializing_other_connection_errors_unavailable(message, reason):
    assert app_db.db_error_message(psycopg2.OperationalError(message)) == reason


def test_unexpected_error_with_debug_off_has_no_traceback(make_app, db_conn, tables):
    with db_conn, db_conn.cursor() as cur:  # a summary the template can't render: q3 is not a dict
        load_data.store_summary(cur, {"q3": 5}, 0)
    app = create_app({"DATABASE_URL": make_app().config["DATABASE_URL"]})
    assert app.debug is False and app.testing is False

    response = app.test_client().get("/")

    assert response.status_code == 500
    assert b"Internal Server Error" in response.data
    assert b"Traceback" not in response.data
