"""Specific exception handling: missing settings, row-level DB errors, denied access.

Everything runs against the real test database. (The web page's own
database-failure handling is in test_web_page.py, publish failures in
test_web_buttons.py, and the worker's in test_consumer.py.)
"""

import json

import psycopg2
import pytest
from psycopg2 import sql
from conftest import database_name, drop_role, make_record, make_records, role_url

import load_data
from app import create_app

pytestmark = [pytest.mark.buttons, pytest.mark.db]


# ---------------------------------------------------------------------------
# Missing DATABASE_URL
# ---------------------------------------------------------------------------


def test_load_data_cli_reports_missing_database_url(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DATABASE_URL")
    data_file = tmp_path / "records.json"
    data_file.write_text(json.dumps([make_record(0)]))

    with pytest.raises(SystemExit) as excinfo:
        load_data.main(data_file=str(data_file))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert out == "LOAD FAILED: DATABASE_URL is not set (see .env.example).\n"


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


def test_connection_level_error_is_not_swallowed_per_row(test_database_url):
    # A closed connection is not a row-level problem: it propagates instead of
    # marking every record as failed.
    conn = psycopg2.connect(test_database_url)
    conn.close()

    with pytest.raises(psycopg2.InterfaceError):
        load_data.load_rows(make_records(2), conn)


# ---------------------------------------------------------------------------
# /api/applicants with a role that has no privileges
# ---------------------------------------------------------------------------

NO_ACCESS_ROLE = "jhu_module6_noaccess_test"
NO_ACCESS_PASSWORD = "noaccess-test-only"


@pytest.fixture
def no_access_url(db_conn, test_database_url):
    """A real login role that may connect but has no grants on the table.

    CONNECT is granted explicitly: setup_roles revokes PUBLIC's default
    CONNECT on the database, so a role without it couldn't log in at all.
    """
    drop_role(db_conn, NO_ACCESS_ROLE)
    with db_conn, db_conn.cursor() as cur:
        cur.execute(f"CREATE ROLE {NO_ACCESS_ROLE} LOGIN PASSWORD %s", (NO_ACCESS_PASSWORD,))
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name(test_database_url)), sql.Identifier(NO_ACCESS_ROLE)
            )
        )
    yield role_url(test_database_url, NO_ACCESS_ROLE, NO_ACCESS_PASSWORD)
    drop_role(db_conn, NO_ACCESS_ROLE)


def test_api_permission_denied_is_503(no_access_url, caplog):
    app = create_app({"DATABASE_URL": no_access_url, "TESTING": True})

    response = app.test_client().get("/api/applicants")

    assert response.status_code == 503
    assert response.get_json() == {"error": "database permission denied"}
    assert "Applicant search: database permission denied" in caplog.text


def test_page_and_status_permission_denied_is_503(no_access_url, tables, caplog):
    client = create_app({"DATABASE_URL": no_access_url, "TESTING": True}).test_client()

    page = client.get("/")
    assert page.status_code == 503
    assert b"Database permission denied" in page.data
    status = client.get("/api/status")
    assert status.status_code == 503 and status.get_json() == {"error": "database permission denied"}
    assert "Analysis page: database permission denied" in caplog.text
