"""Specific exception handling: DB down, bad settings, launch failures, crashed pulls, bad files.

Everything runs against the real test database (or a deliberately
unreachable one); only the pull subprocess and the scraper are faked.
"""

import json
import logging
import os
import socket
import subprocess
import sys

import psycopg2
import pytest
from bs4 import BeautifulSoup
from conftest import FakeScraper, make_record, make_records
from sqlalchemy.engine import make_url

import load_data
import pull_data
from busy_state import FileLockBusyState, InMemoryBusyState
from flask_app import AppDependencies, create_app

pytestmark = [pytest.mark.buttons, pytest.mark.db]


def _closed_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def unreachable_url():
    return f"postgresql://nobody@127.0.0.1:{_closed_port()}/unreachable_test"


def _down_app(unreachable_url, **kwargs):
    kwargs.setdefault("busy_state", InMemoryBusyState())
    return create_app({"DB_URL": unreachable_url, "TESTING": True}, AppDependencies(**kwargs))


# ---------------------------------------------------------------------------
# Database down: pages, Update Analysis, Pull Data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/analysis"])
def test_page_is_503_with_message_when_database_down(unreachable_url, caplog, path):
    response = _down_app(unreachable_url).test_client().get(path)

    assert response.status_code == 503
    soup = BeautifulSoup(response.data, "html.parser")
    message = soup.select_one('[data-testid="db-unavailable"]').get_text(" ", strip=True)
    assert message.startswith("Database unavailable: the analysis could not be loaded.")
    assert soup.select("section.question") == []
    assert len(soup.select('[data-testid="pull-data-btn"]')) == 1
    assert b"Traceback" not in response.data
    assert "Analysis page: database unavailable" in caplog.text


def test_update_analysis_is_503_json_when_database_down(unreachable_url, caplog):
    response = _down_app(unreachable_url).test_client().post("/update-analysis")

    assert response.status_code == 503
    assert response.get_json() == {"ok": False, "error": "database unavailable"}
    assert "Update Analysis: database unavailable" in caplog.text


def test_cached_page_still_served_after_database_goes_down(make_app, db_conn):
    client = make_app().test_client()
    assert client.get("/").status_code == 200  # snapshot computed and cached

    with db_conn, db_conn.cursor() as cur:
        cur.execute("DROP TABLE applicants")  # later DB problems don't affect the cached page

    assert client.get("/").status_code == 200


def test_in_process_pull_reports_database_down(unreachable_url, pull_result_path, caplog):
    client = _down_app(unreachable_url, scraper=FakeScraper(make_records(2))).test_client()

    response = client.post("/pull-data")

    assert response.status_code == 500
    body = response.get_json()
    assert body["ok"] is False
    assert "Connection refused" in body["error"] or "could not connect" in body["error"]
    assert json.loads(pull_result_path.read_text())["ok"] is False
    assert "Pull Data failed" in caplog.text


def test_in_process_pull_reports_missing_settings(monkeypatch, pull_result_path):
    # No DB_URL: the default loader reads the DB_* settings, and DB_HOST is
    # empty (load_dotenv never overrides a variable that is already set).
    monkeypatch.setenv("DB_HOST", "")
    app = create_app(
        {"TESTING": True},
        AppDependencies(
            scraper=FakeScraper(make_records(1)),
            analysis_fn=dict,  # the page isn't under test; keeps create_app from building the ORM engine
            busy_state=InMemoryBusyState(),
        ),
    )

    response = app.test_client().post("/pull-data")

    assert response.status_code == 500
    assert response.get_json()["error"].startswith("Missing required database setting(s): DB_HOST.")
    assert json.loads(pull_result_path.read_text())["ok"] is False


def test_load_data_cli_reports_missing_settings(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DB_HOST", "")
    data_file = tmp_path / "records.json"
    data_file.write_text(json.dumps([make_record(0)]))

    with pytest.raises(SystemExit) as excinfo:
        load_data.main(data_file=str(data_file))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith("LOAD FAILED: Missing required database setting(s): DB_HOST.")
    assert "see .env.example" in out
    assert "Traceback" not in out


# ---------------------------------------------------------------------------
# Launching the pull subprocess
# ---------------------------------------------------------------------------


@pytest.fixture
def lock_state(tmp_path):
    return FileLockBusyState(str(tmp_path / "pull.lock"))


def _launch_app(test_database_url, lock_state, launcher):
    return create_app(
        {"DB_URL": test_database_url, "TESTING": True},
        AppDependencies(busy_state=lock_state, pull_launcher=launcher),
    )


def test_launch_os_error_is_500_and_releases_lock(test_database_url, lock_state, pull_result_path):
    def launcher():
        raise OSError("exec format error")

    response = _launch_app(test_database_url, lock_state, launcher).test_client().post("/pull-data")

    assert response.status_code == 500
    assert response.get_json() == {"ok": False, "error": "exec format error"}
    assert lock_state.is_busy() is False
    recorded = json.loads(pull_result_path.read_text())
    assert (recorded["ok"], recorded["error"]) == (False, "exec format error")
    assert "pending" not in recorded


def test_uncaught_launch_error_still_releases_lock(test_database_url, lock_state, pull_result_path):
    def launcher():
        raise ValueError("bad Popen argument")  # not a launch failure we handle

    client = _launch_app(test_database_url, lock_state, launcher).test_client()

    with pytest.raises(ValueError, match="bad Popen argument"):
        client.post("/pull-data")
    assert lock_state.is_busy() is False  # `finally` released it without catching
    status = client.get("/pull-status").get_json()
    assert status["running"] is False
    assert status["last_result"]["ok"] is False
    assert status["last_result"]["error"] == pull_data.CRASHED_PULL_ERROR


def test_crashed_child_is_reported_as_failed(test_database_url, lock_state, pull_result_path):
    """A pull process that exits nonzero without writing a result is reported as failed."""
    children = []

    def launcher():
        # A real child process that dies without touching the result file.
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
        children.append(child)
        return child

    client = _launch_app(test_database_url, lock_state, launcher).test_client()
    assert client.post("/pull-data").status_code == 202
    assert children[0].wait(timeout=30) == 3

    status = client.get("/pull-status").get_json()

    assert status["running"] is False
    assert status["last_result"]["ok"] is False
    assert status["last_result"]["error"] == "pull process exited without reporting a result"
    assert not os.path.exists(lock_state.lock_path)  # stale lock cleared
    recorded = json.loads(pull_result_path.read_text())
    assert "pending" not in recorded and recorded["finished_at"]
    banner = BeautifulSoup(client.get("/analysis").data, "html.parser").select_one('[data-testid="last-pull"]')
    assert banner.get_text(" ", strip=True).startswith(
        "Last pull failed: pull process exited without reporting a result"
    )


def test_pending_result_hidden_while_child_runs(test_database_url, lock_state, pull_result_path):
    children = []

    def launcher():
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        children.append(child)
        return child

    client = _launch_app(test_database_url, lock_state, launcher).test_client()
    assert client.post("/pull-data").status_code == 202
    try:
        status = client.get("/pull-status").get_json()
        assert status == {"running": True, "last_result": None}
        assert json.loads(pull_result_path.read_text())["pending"] is True
    finally:
        children[0].stdin.close()
        children[0].wait(timeout=30)

    assert client.get("/pull-status").get_json()["last_result"]["error"] == pull_data.CRASHED_PULL_ERROR


def test_child_result_replaces_pending_record(tmp_path):
    path = tmp_path / "result.json"
    pull_data.write_pull_result(str(path), pull_data.pending_result())
    pull_data.write_pull_result(str(path), pull_data.pull_result(run={"inserted": 4}))

    assert pull_data.settle_pull_result(str(path), running=False)["inserted"] == 4


# ---------------------------------------------------------------------------
# Unreadable lock and result files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected_log"),
    [
        (b"{not json", "Cleared unreadable lock file"),
        (b"\xff\xfe\x00 not utf-8", "Cleared unreadable lock file"),
        (b"[1, 2]", "Cleared malformed lock file"),
    ],
)
def test_bad_lock_file_is_cleared_with_warning(tmp_path, caplog, content, expected_log):
    lock_path = tmp_path / "pull.lock"
    lock_path.write_bytes(content)

    with caplog.at_level(logging.WARNING, logger="busy_state"):
        assert FileLockBusyState(str(lock_path)).status() == {"running": False}

    assert not lock_path.exists()
    assert expected_log in caplog.text


def test_clear_reports_whether_it_removed_the_lock(tmp_path, caplog):
    state = FileLockBusyState(str(tmp_path / "pull.lock"))
    assert state.try_acquire() is True

    with caplog.at_level(logging.DEBUG, logger="busy_state"):
        assert state._clear() is True
        assert state._clear() is False

    assert "Lock already removed" in caplog.text


@pytest.mark.parametrize("kind", ["bad_utf8", "directory"])
def test_unreadable_result_file_never_breaks_the_page(make_app, pull_result_path, caplog, kind):
    if kind == "bad_utf8":
        pull_result_path.write_bytes(b"\xff\xfe not utf-8")
    else:
        pull_result_path.mkdir()

    with caplog.at_level(logging.DEBUG, logger="pull_data"):
        assert pull_data.read_pull_result(str(pull_result_path)) is None
        response = make_app().test_client().get("/analysis")

    assert response.status_code == 200
    assert "No readable pull result" in caplog.text


# ---------------------------------------------------------------------------
# Row-level database errors in load_rows()
# ---------------------------------------------------------------------------


@pytest.fixture
def constrained_table(db_conn):
    """Tighten the test table; dropping it on teardown lets the autouse fixture recreate it."""
    with db_conn, db_conn.cursor() as cur:
        cur.execute("ALTER TABLE applicants ADD CONSTRAINT gpa_at_most_4 CHECK (gpa <= 4.0)")
        cur.execute("ALTER TABLE applicants ALTER COLUMN degree TYPE VARCHAR(5)")
    yield
    with db_conn, db_conn.cursor() as cur:
        cur.execute("DROP TABLE applicants")


@pytest.mark.parametrize(
    ("bad_field", "bad_value", "error_text"),
    [
        ("GPA", "9.9", 'violates check constraint "gpa_at_most_4"'),  # IntegrityError
        ("Masters or PhD", "Doctorate", "value too long for type character varying(5)"),  # DataError
    ],
)
def test_row_level_database_error_skips_only_that_row(
    constrained_table, db_conn, fetch_rows, bad_field, bad_value, error_text
):
    records = [make_record(0), make_record(1, **{bad_field: bad_value}), make_record(2)]

    inserted, skipped, failed = load_data.load_rows(records, db_conn)

    assert (inserted, skipped) == (2, 0)
    [(index, reason)] = failed
    assert index == 1
    assert error_text in reason
    assert [row["url"] for row in fetch_rows()] == [records[0]["URL"], records[2]["URL"]]


def test_connection_level_error_is_not_swallowed_per_row(db_conn, test_database_url):
    # A closed connection is not a row-level problem: it propagates instead of
    # marking every record as failed.
    conn = psycopg2.connect(load_data.psycopg2_dsn(test_database_url))
    conn.close()

    with pytest.raises(psycopg2.InterfaceError):
        load_data.load_rows(make_records(2), conn)


# ---------------------------------------------------------------------------
# /api/applicants with a role that has no privileges
# ---------------------------------------------------------------------------

NO_ACCESS_ROLE = "jhu_module5_noaccess_test"
NO_ACCESS_PASSWORD = "noaccess-test-only"


@pytest.fixture
def no_access_url(db_conn, test_database_url):
    """A real login role with no grants on the test database's schema or table."""
    with db_conn, db_conn.cursor() as cur:
        cur.execute(f"DROP ROLE IF EXISTS {NO_ACCESS_ROLE}")
        cur.execute(f"CREATE ROLE {NO_ACCESS_ROLE} LOGIN PASSWORD %s", (NO_ACCESS_PASSWORD,))
    url = make_url(test_database_url).set(username=NO_ACCESS_ROLE, password=NO_ACCESS_PASSWORD)
    yield url.render_as_string(hide_password=False)
    with db_conn, db_conn.cursor() as cur:
        cur.execute(f"DROP ROLE IF EXISTS {NO_ACCESS_ROLE}")


def test_api_permission_denied_is_503(no_access_url, caplog):
    app = create_app({"DB_URL": no_access_url, "TESTING": True}, AppDependencies(busy_state=InMemoryBusyState()))

    response = app.test_client().get("/api/applicants")

    assert response.status_code == 503
    assert response.get_json() == {"error": "database permission denied"}
    assert "Applicant search: database permission denied" in caplog.text


# ---------------------------------------------------------------------------
# Debug off: unexpected errors never expose a traceback
# ---------------------------------------------------------------------------


def test_unexpected_error_with_debug_off_has_no_traceback(test_database_url):
    def broken_analysis():
        raise KeyError("q1_count")

    app = create_app(
        {"DB_URL": test_database_url},
        AppDependencies(analysis_fn=broken_analysis, busy_state=InMemoryBusyState()),
    )
    assert app.debug is False and app.testing is False

    response = app.test_client().get("/")

    assert response.status_code == 500
    assert b"Internal Server Error" in response.data
    assert b"Traceback" not in response.data and b"KeyError" not in response.data
