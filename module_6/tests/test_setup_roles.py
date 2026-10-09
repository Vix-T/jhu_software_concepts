"""setup_roles.py: the least-privilege app role, applied to the test database with real connections.

The role is TEST_APP_ROLE (gradcafe_app_test, set as APP_DB_USER by
conftest), never the dev gradcafe_app: roles are cluster-wide.
"""

import json
import os
import runpy

import psycopg2
import psycopg2.errors
import pytest
from bs4 import BeautifulSoup
from conftest import TEST_APP_ROLE, FakeScraper, drop_role, make_records, role_url
from sqlalchemy.engine import make_url

import load_data
import setup_roles
from busy_state import InMemoryBusyState
from flask_app import AppDependencies, create_app
from load_data import TABLE_MISSING_MESSAGE

pytestmark = pytest.mark.db

CATALOG = setup_roles.CatalogState(
    role_exists=False, sequence=("public", "applicants_p_id_seq"), public_can_create=False
)


@pytest.fixture
def owner_conn(db_conn):
    return db_conn


@pytest.fixture
def app_role(owner_conn, test_database_url):
    """Create TEST_APP_ROLE with setup_roles (as the test DB's owner); yield (password, url)."""
    password = os.environ["APP_DB_PASSWORD"]
    drop_role(owner_conn, TEST_APP_ROLE)  # a leftover copy from an interrupted run
    setup_roles.apply_role_setup(owner_conn, TEST_APP_ROLE, password)
    yield password, role_url(test_database_url, TEST_APP_ROLE, password)
    drop_role(owner_conn, TEST_APP_ROLE)


@pytest.fixture
def role_conn(app_role):
    conn = psycopg2.connect(setup_roles.psycopg2_dsn(app_role[1]))
    yield conn
    conn.close()


def _scalar(conn, query, params=()):
    with conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Statement builder
# ---------------------------------------------------------------------------


def test_builder_statements_for_a_new_role(db_conn):
    statements = setup_roles.build_role_statements(
        "gradcafe_app", "s3cr'et", "jhu_module6", catalog=CATALOG
    )
    text = [statement.as_string(db_conn) for statement in statements]

    assert text == [
        'CREATE ROLE "gradcafe_app" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
        "NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD 's3cr''et'",
        'REVOKE ALL ON DATABASE "jhu_module6" FROM PUBLIC',
        'REVOKE ALL ON DATABASE "jhu_module6" FROM "gradcafe_app"',
        'GRANT CONNECT ON DATABASE "jhu_module6" TO "gradcafe_app"',
        'REVOKE ALL ON SCHEMA "public" FROM "gradcafe_app"',
        'GRANT USAGE ON SCHEMA "public" TO "gradcafe_app"',
        'REVOKE ALL ON TABLE "applicants" FROM "gradcafe_app"',
        'GRANT SELECT, INSERT ON TABLE "applicants" TO "gradcafe_app"',
        'REVOKE ALL ON SEQUENCE "public"."applicants_p_id_seq" FROM "gradcafe_app"',
        'GRANT USAGE ON SEQUENCE "public"."applicants_p_id_seq" TO "gradcafe_app"',
    ]


def test_builder_alters_an_existing_role_and_revokes_public_create(db_conn):
    catalog = setup_roles.CatalogState(
        role_exists=True, sequence=("public", "applicants_p_id_seq"), public_can_create=True
    )
    text = [
        statement.as_string(db_conn)
        for statement in setup_roles.build_role_statements(
            "gradcafe_app", setup_roles.PASSWORD_MASK, "jhu_module6", catalog=catalog
        )
    ]

    assert text[0].startswith('ALTER ROLE "gradcafe_app" WITH LOGIN NOSUPERUSER')
    assert text[0].endswith("PASSWORD '********'")
    assert text[1] == 'REVOKE CREATE ON SCHEMA "public" FROM PUBLIC'


def test_builder_quotes_hostile_names(db_conn):
    statements = setup_roles.build_role_statements(
        'evil"; DROP ROLE x; --', "pw", "db", catalog=CATALOG
    )
    assert 'ROLE "evil""; DROP ROLE x; --" WITH' in statements[0].as_string(db_conn)


# ---------------------------------------------------------------------------
# What the role can and can't do (real connections as the role)
# ---------------------------------------------------------------------------


def test_role_can_select_and_insert(seed, role_conn, row_count):
    seed(make_records(2))

    assert _scalar(role_conn, "SELECT COUNT(*) FROM applicants") == 2
    # The app's real loader, on a connection logged in as the role.
    inserted, skipped, failed = load_data.load_rows(make_records(3, start=10), role_conn)

    assert (inserted, skipped, failed) == (3, 0, [])  # SERIAL p_id: sequence USAGE works
    assert row_count() == 5
    assert _scalar(role_conn, "SELECT MAX(p_id) FROM applicants") == 5


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE applicants SET status = 'Accepted'",
        "DELETE FROM applicants",
        "TRUNCATE applicants",
        "DROP TABLE applicants",
        "ALTER TABLE applicants ADD COLUMN hacked TEXT",
        "CREATE TABLE hacked (id INT)",
        # Even on the EXISTING table: the schema CREATE check comes first, which
        # is why create_table() checks to_regclass() before running any DDL.
        "CREATE TABLE IF NOT EXISTS applicants (p_id INT)",
        "CREATE TEMP TABLE hacked_tmp (id INT)",
        "SELECT setval(pg_get_serial_sequence('applicants', 'p_id'), 1)",
    ],
)
def test_role_cannot_modify_or_create(seed, role_conn, row_count, statement):
    seed(make_records(2))

    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with role_conn, role_conn.cursor() as cur:
            cur.execute(statement)

    assert row_count() == 2


def test_role_attributes_and_ownership(app_role, owner_conn, test_database_url):
    with owner_conn, owner_conn.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, "
            "rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s",
            (TEST_APP_ROLE,),
        )
        assert cur.fetchone() == (False, False, False, False, False, False, True)
        cur.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'applicants'::regclass")
        assert cur.fetchone()[0] != TEST_APP_ROLE

        database = make_url(test_database_url).database
        cur.execute(
            "SELECT has_database_privilege(%(r)s, %(d)s, 'CONNECT'), "
            "has_database_privilege(%(r)s, %(d)s, 'TEMPORARY'), "
            "has_database_privilege(%(r)s, %(d)s, 'CREATE'), "
            "has_schema_privilege(%(r)s, 'public', 'USAGE'), "
            "has_schema_privilege(%(r)s, 'public', 'CREATE')",
            {"r": TEST_APP_ROLE, "d": database},
        )
        assert cur.fetchone() == (True, False, False, True, False)


def test_role_has_exactly_select_and_insert(app_role, owner_conn):
    with owner_conn, owner_conn.cursor() as cur:
        cur.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'applicants' AND grantee = %s",
            (TEST_APP_ROLE,),
        )
        assert {row[0] for row in cur.fetchall()} == {"SELECT", "INSERT"}
        sequence = "public.applicants_p_id_seq"
        cur.execute(
            "SELECT has_sequence_privilege(%(r)s, %(s)s, 'USAGE'), "
            "has_sequence_privilege(%(r)s, %(s)s, 'SELECT'), "
            "has_sequence_privilege(%(r)s, %(s)s, 'UPDATE')",
            {"r": TEST_APP_ROLE, "s": sequence},
        )
        assert cur.fetchone() == (True, False, False)


def test_setup_is_idempotent_and_removes_stray_grants(app_role, owner_conn):
    with owner_conn, owner_conn.cursor() as cur:
        cur.execute(f'GRANT UPDATE, DELETE ON applicants TO "{TEST_APP_ROLE}"')

    masked = setup_roles.apply_role_setup(owner_conn, TEST_APP_ROLE, app_role[0])  # second run

    assert masked[0].as_string(owner_conn).startswith(f'ALTER ROLE "{TEST_APP_ROLE}"')
    with owner_conn, owner_conn.cursor() as cur:
        cur.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'applicants' AND grantee = %s",
            (TEST_APP_ROLE,),
        )
        assert {row[0] for row in cur.fetchall()} == {"SELECT", "INSERT"}


# ---------------------------------------------------------------------------
# The app, end to end, connected as the role
# ---------------------------------------------------------------------------


def _role_app(url, **deps):
    deps.setdefault("busy_state", InMemoryBusyState())
    return create_app({"DB_URL": url, "TESTING": True}, AppDependencies(**deps))


def test_app_works_end_to_end_as_the_role(app_role, seed, row_count, pull_result_path):
    seed(make_records(3))
    client = _role_app(app_role[1], scraper=FakeScraper(make_records(2, start=100))).test_client()

    assert client.get("/").status_code == 200
    page = client.get("/analysis")
    assert page.status_code == 200
    assert "Fall 2026 applicant count: 3" in BeautifulSoup(page.data, "html.parser").get_text(" ", strip=True)
    api = client.get("/api/applicants", query_string={"limit": "2"})
    assert api.status_code == 200 and api.get_json()["count"] == 2

    pulled = client.post("/pull-data")  # default loader: INSERTs as the role
    assert pulled.status_code == 200 and pulled.get_json() == {"ok": True, "inserted": 2}
    assert row_count() == 5
    assert client.post("/update-analysis").status_code == 200
    assert "Fall 2026 applicant count: 5" in BeautifulSoup(
        client.get("/").data, "html.parser"
    ).get_text(" ", strip=True)


def test_missing_table_as_the_role_is_503(app_role, owner_conn, pull_result_path, caplog):
    with owner_conn, owner_conn.cursor() as cur:
        cur.execute("DROP TABLE applicants")  # the autouse fixture recreates it for the next test
    client = _role_app(app_role[1], scraper=FakeScraper(make_records(1))).test_client()

    page = client.get("/")
    assert page.status_code == 503
    message = BeautifulSoup(page.data, "html.parser").select_one('[data-testid="db-unavailable"]')
    assert message.get_text(strip=True) == TABLE_MISSING_MESSAGE

    update = client.post("/update-analysis")
    assert update.status_code == 503 and update.get_json() == {"ok": False, "error": TABLE_MISSING_MESSAGE}
    api = client.get("/api/applicants")
    assert api.status_code == 503 and api.get_json() == {"error": TABLE_MISSING_MESSAGE}
    pull = client.post("/pull-data")
    assert pull.status_code == 500 and pull.get_json()["error"] == TABLE_MISSING_MESSAGE
    assert json.loads(pull_result_path.read_text())["ok"] is False
    assert "Cannot create the applicants table as gradcafe_app_test" in caplog.text


# ---------------------------------------------------------------------------
# The CLI (run as the test DB's owner, the DB_* settings conftest set)
# ---------------------------------------------------------------------------


def test_main_sets_up_role_and_masks_password(owner_conn, capsys):
    drop_role(owner_conn, TEST_APP_ROLE)
    password = os.environ["APP_DB_PASSWORD"]
    try:
        setup_roles.main()
        out = capsys.readouterr().out

        assert out.startswith(f"Role setup for {TEST_APP_ROLE} on database ")
        assert f'  CREATE ROLE "{TEST_APP_ROLE}" WITH LOGIN' in out
        assert "PASSWORD '********';" in out
        assert password not in out
        assert f'  GRANT SELECT, INSERT ON TABLE "applicants" TO "{TEST_APP_ROLE}";' in out
        assert _scalar(owner_conn, "SELECT COUNT(*) FROM pg_roles WHERE rolname = %s", (TEST_APP_ROLE,)) == 1
    finally:
        drop_role(owner_conn, TEST_APP_ROLE)


@pytest.mark.parametrize("missing", ["APP_DB_USER", "APP_DB_PASSWORD"])
def test_main_missing_app_role_settings(monkeypatch, capsys, missing):
    monkeypatch.setenv(missing, "")  # load_dotenv never overrides a variable that is already set

    with pytest.raises(SystemExit) as excinfo:
        setup_roles.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().out.startswith(f"ROLE SETUP FAILED: Missing app role setting(s): {missing}.")


def test_main_without_table_tells_you_to_load_first(owner_conn, capsys):
    with owner_conn, owner_conn.cursor() as cur:
        cur.execute("DROP TABLE applicants")

    with pytest.raises(SystemExit) as excinfo:
        setup_roles.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().out == (
        f"ROLE SETUP FAILED: {TABLE_MISSING_MESSAGE} before running setup_roles.py\n"
    )


def test_main_as_a_non_owner_fails_cleanly(app_role, monkeypatch, capsys):
    # Connected as the app role itself: it may not grant anything.
    monkeypatch.setenv("DB_USER", TEST_APP_ROLE)
    monkeypatch.setenv("DB_PASSWORD", app_role[0])
    monkeypatch.setenv("APP_DB_USER", "gradcafe_app_test_other")

    with pytest.raises(SystemExit) as excinfo:
        setup_roles.main()

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith("ROLE SETUP FAILED: permission denied to create role")
    assert out.endswith("Run setup_roles.py as the database owner (DB_USER/DB_PASSWORD).\n")


def test_main_unreachable_database(monkeypatch, capsys):
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "1")

    with pytest.raises(SystemExit) as excinfo:
        setup_roles.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().out.startswith("ROLE SETUP FAILED: ")


def test_script_entry_point_runs_main(owner_conn, capsys):
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "setup_roles.py")
    drop_role(owner_conn, TEST_APP_ROLE)
    try:
        runpy.run_path(script, run_name="__main__")
        assert capsys.readouterr().out.startswith(f"Role setup for {TEST_APP_ROLE} on database ")
    finally:
        drop_role(owner_conn, TEST_APP_ROLE)
