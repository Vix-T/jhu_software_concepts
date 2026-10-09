"""setup_roles.py: the web service's read-only role, applied to the test database with real connections.

The role is TEST_WEB_ROLE (gradcafe_web_test, via the web_role_env fixture),
never a dev gradcafe_web: roles are cluster-wide. Every refusal below is
PostgreSQL's own permission check on a connection logged in as the role.
"""

import os
import runpy

import psycopg2
import psycopg2.errors
import pytest
from conftest import TEST_WEB_ROLE, database_name, make_record, result_url, role_url

import load_data
import setup_roles
from app import create_app

pytestmark = pytest.mark.db

CATALOG = setup_roles.CatalogState(role_exists=False, public_can_create=False)
SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "db", "setup_roles.py")
THREE_TABLES_SELECT = [
    ("analysis_summary", "SELECT"),
    ("applicants", "SELECT"),
    ("ingestion_watermarks", "SELECT"),
]


@pytest.fixture
def owner_conn(db_conn):
    return db_conn


@pytest.fixture
def web_role(owner_conn, tables, web_role_env, test_database_url):
    """Create TEST_WEB_ROLE with setup_roles (as the test DB's owner); return (password, url)."""
    setup_roles.apply_role_setup(owner_conn, TEST_WEB_ROLE, web_role_env)
    return web_role_env, role_url(test_database_url, TEST_WEB_ROLE, web_role_env)


@pytest.fixture
def role_conn(web_role):
    conn = psycopg2.connect(web_role[1])
    yield conn
    conn.close()


@pytest.fixture
def populated(seed, refresh_summary, owner_conn):
    """Two applicants, a stored summary and a watermark row: something in all three tables."""
    seed([make_record(i, URL=result_url(500 + i)) for i in range(2)])
    refresh_summary()
    with owner_conn, owner_conn.cursor() as cur:
        load_data.advance_watermark(cur, 501)


def _scalar(conn, query, params=()):
    with conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()[0]


def _table_privileges(conn, role):
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = %s ORDER BY 1, 2",
            (role,),
        )
        return cur.fetchall()


def _role_count(conn):
    return _scalar(conn, "SELECT COUNT(*) FROM pg_roles WHERE rolname = %s", (TEST_WEB_ROLE,))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_no_web_role_settings_means_no_role(monkeypatch):
    monkeypatch.delenv("WEB_DB_USER", raising=False)
    monkeypatch.delenv("WEB_DB_PASSWORD", raising=False)
    assert setup_roles.web_role_from_env() is None


def test_both_settings_give_the_role(monkeypatch):
    monkeypatch.setenv("WEB_DB_USER", " gradcafe_web ")
    monkeypatch.setenv("WEB_DB_PASSWORD", "pw")
    assert setup_roles.web_role_from_env() == ("gradcafe_web", "pw")


@pytest.mark.parametrize(
    ("user", "password", "missing"),
    [("gradcafe_web", "", "WEB_DB_PASSWORD"), ("", "pw", "WEB_DB_USER"), ("gradcafe_web", None, "WEB_DB_PASSWORD")],
)
def test_half_configured_role_is_a_config_error(monkeypatch, user, password, missing):
    monkeypatch.setenv("WEB_DB_USER", user)
    if password is None:
        monkeypatch.delenv("WEB_DB_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("WEB_DB_PASSWORD", password)

    with pytest.raises(load_data.ConfigError, match=rf"Missing web role setting\(s\): {missing}\."):
        setup_roles.web_role_from_env()


# ---------------------------------------------------------------------------
# Statement builder
# ---------------------------------------------------------------------------


def test_builder_statements_for_a_new_role(db_conn):
    statements = setup_roles.build_role_statements("gradcafe_web", "s3cr'et", "jhu_module6", catalog=CATALOG)
    text = [statement.as_string(db_conn) for statement in statements]

    assert text == [
        'CREATE ROLE "gradcafe_web" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
        "NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD 's3cr''et'",
        'REVOKE ALL ON DATABASE "jhu_module6" FROM PUBLIC',
        'REVOKE ALL ON DATABASE "jhu_module6" FROM "gradcafe_web"',
        'GRANT CONNECT ON DATABASE "jhu_module6" TO "gradcafe_web"',
        'REVOKE ALL ON SCHEMA "public" FROM "gradcafe_web"',
        'GRANT USAGE ON SCHEMA "public" TO "gradcafe_web"',
        'REVOKE ALL ON ALL TABLES IN SCHEMA "public" FROM "gradcafe_web"',
        'REVOKE ALL ON ALL SEQUENCES IN SCHEMA "public" FROM "gradcafe_web"',
        'GRANT SELECT ON TABLE "applicants", "ingestion_watermarks", "analysis_summary" TO "gradcafe_web"',
    ]


def test_builder_alters_an_existing_role_and_revokes_public_create(db_conn):
    catalog = setup_roles.CatalogState(role_exists=True, public_can_create=True)
    text = [
        statement.as_string(db_conn)
        for statement in setup_roles.build_role_statements(
            "gradcafe_web", setup_roles.PASSWORD_MASK, "jhu_module6", catalog=catalog
        )
    ]

    assert text[0].startswith('ALTER ROLE "gradcafe_web" WITH LOGIN NOSUPERUSER')
    assert text[0].endswith("PASSWORD '********'")
    assert text[1] == 'REVOKE CREATE ON SCHEMA "public" FROM PUBLIC'


def test_builder_quotes_hostile_names(db_conn):
    statements = setup_roles.build_role_statements('evil"; DROP ROLE x; --', "pw", "db", catalog=CATALOG)
    assert 'ROLE "evil""; DROP ROLE x; --" WITH' in statements[0].as_string(db_conn)


# ---------------------------------------------------------------------------
# What the role can and can't do (real connections as the role)
# ---------------------------------------------------------------------------


def test_role_can_select_all_three_tables(populated, role_conn):
    assert _scalar(role_conn, "SELECT COUNT(*) FROM applicants") == 2
    assert _scalar(role_conn, "SELECT row_count FROM analysis_summary") == 2
    assert _scalar(role_conn, "SELECT last_seen FROM ingestion_watermarks") == 501


@pytest.mark.parametrize(
    "statement",
    [
        # p_id given explicitly, so only the table privilege is checked (not the sequence's).
        "INSERT INTO applicants (p_id, url) VALUES (999, 'https://www.thegradcafe.com/result/999')",
        "UPDATE applicants SET status = 'Accepted'",
        "DELETE FROM applicants",
        "CREATE TABLE hacked (id INT)",
        "INSERT INTO analysis_summary (id, results, row_count) VALUES (1, '{}', 0)",
        "UPDATE analysis_summary SET row_count = 0",
        "DELETE FROM ingestion_watermarks",
        "UPDATE ingestion_watermarks SET last_seen = 0",
        "TRUNCATE applicants",
        "DROP TABLE applicants",
        "ALTER TABLE applicants ADD COLUMN hacked TEXT",
        "CREATE TABLE IF NOT EXISTS applicants (p_id INT)",
        "CREATE TEMP TABLE hacked_tmp (id INT)",
        "SELECT nextval(pg_get_serial_sequence('applicants', 'p_id'))",
    ],
)
def test_role_cannot_write_or_create(populated, role_conn, row_count, statement):
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        with role_conn, role_conn.cursor() as cur:
            cur.execute(statement)

    assert row_count() == 2


def test_role_attributes_and_ownership(web_role, owner_conn, test_database_url):
    with owner_conn, owner_conn.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, "
            "rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s",
            (TEST_WEB_ROLE,),
        )
        assert cur.fetchone() == (False, False, False, False, False, False, True)
        cur.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'applicants'::regclass")
        assert cur.fetchone()[0] != TEST_WEB_ROLE

        cur.execute(
            "SELECT has_database_privilege(%(r)s, %(d)s, 'CONNECT'), "
            "has_database_privilege(%(r)s, %(d)s, 'TEMPORARY'), "
            "has_database_privilege(%(r)s, %(d)s, 'CREATE'), "
            "has_schema_privilege(%(r)s, 'public', 'USAGE'), "
            "has_schema_privilege(%(r)s, 'public', 'CREATE'), "
            "has_sequence_privilege(%(r)s, 'public.applicants_p_id_seq', 'USAGE')",
            {"r": TEST_WEB_ROLE, "d": database_name(test_database_url)},
        )
        assert cur.fetchone() == (True, False, False, True, False, False)


def test_role_has_exactly_select_on_the_three_tables(web_role, owner_conn):
    assert _table_privileges(owner_conn, TEST_WEB_ROLE) == THREE_TABLES_SELECT


def test_setup_is_idempotent_and_removes_stray_grants(web_role, owner_conn):
    with owner_conn, owner_conn.cursor() as cur:
        cur.execute(f'GRANT INSERT, UPDATE ON applicants TO "{TEST_WEB_ROLE}"')
        cur.execute(f'GRANT DELETE ON analysis_summary TO "{TEST_WEB_ROLE}"')
        cur.execute(f'GRANT USAGE ON SEQUENCE applicants_p_id_seq TO "{TEST_WEB_ROLE}"')
        cur.execute("CREATE TABLE extra_table (id INT)")
        cur.execute(f'GRANT SELECT ON extra_table TO "{TEST_WEB_ROLE}"')
    try:
        masked = setup_roles.apply_role_setup(owner_conn, TEST_WEB_ROLE, web_role[0])  # second run

        assert masked[0].as_string(owner_conn).startswith(f'ALTER ROLE "{TEST_WEB_ROLE}"')
        assert _table_privileges(owner_conn, TEST_WEB_ROLE) == THREE_TABLES_SELECT
        assert _scalar(
            owner_conn, "SELECT has_sequence_privilege(%s, 'public.applicants_p_id_seq', 'USAGE')", (TEST_WEB_ROLE,)
        ) is False
    finally:
        with owner_conn, owner_conn.cursor() as cur:
            cur.execute("DROP TABLE extra_table")


def test_setup_needs_all_three_tables(owner_conn, tables, web_role_env):
    with owner_conn, owner_conn.cursor() as cur:
        cur.execute("DROP TABLE analysis_summary")

    with pytest.raises(load_data.TableMissingError, match=r"table\(s\) missing: analysis_summary; start the worker"):
        setup_roles.apply_role_setup(owner_conn, TEST_WEB_ROLE, web_role_env)

    assert _role_count(owner_conn) == 0


def test_setup_waits_for_the_initialization_lock(owner_conn, tables, web_role_env, test_database_url):
    holder = psycopg2.connect(test_database_url)
    waiter = psycopg2.connect(test_database_url, options="-c lock_timeout=200")
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [load_data.INIT_LOCK_KEY])
        with pytest.raises(psycopg2.errors.LockNotAvailable):
            setup_roles.apply_role_setup(waiter, TEST_WEB_ROLE, web_role_env)
    finally:
        holder.close()
        waiter.close()
    assert _role_count(owner_conn) == 0


# ---------------------------------------------------------------------------
# The web service, connected as the role
# ---------------------------------------------------------------------------


def test_web_service_works_as_the_role(populated, web_role):
    client = create_app({"DATABASE_URL": web_role[1], "TESTING": True}).test_client()

    assert client.get("/").status_code == 200
    status = client.get("/api/status")
    assert status.status_code == 200 and status.get_json()["row_count"] == 2
    api = client.get("/api/applicants", query_string={"limit": "1"})
    assert api.status_code == 200 and api.get_json()["count"] == 1


def test_web_before_the_role_exists_is_503(tables, web_role_env, test_database_url, caplog):
    # First start: the worker hasn't created the role yet, so the login itself fails.
    url = role_url(test_database_url, TEST_WEB_ROLE, web_role_env)
    client = create_app({"DATABASE_URL": url, "TESTING": True}).test_client()

    page = client.get("/")
    assert page.status_code == 503
    assert b"The database is being initialised." in page.data and b"Traceback" not in page.data
    assert b'<meta http-equiv="refresh" content="5">' in page.data
    assert client.get("/api/status").get_json() == {"error": "database initializing"}
    assert client.get("/api/applicants").status_code == 503
    assert "Analysis page: database initializing" in caplog.text


# ---------------------------------------------------------------------------
# The CLI (run as the test DB's owner: the DATABASE_URL conftest set)
# ---------------------------------------------------------------------------


def test_main_sets_up_role_and_masks_password(owner_conn, tables, web_role_env, capsys):
    setup_roles.main()
    out = capsys.readouterr().out

    assert out.startswith(f"Role setup for {TEST_WEB_ROLE} on database ")
    assert f'  CREATE ROLE "{TEST_WEB_ROLE}" WITH LOGIN' in out
    assert "PASSWORD '********';" in out
    assert web_role_env not in out
    assert f'"ingestion_watermarks", "analysis_summary" TO "{TEST_WEB_ROLE}";' in out
    assert _role_count(owner_conn) == 1


def test_main_without_settings_fails_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("WEB_DB_USER", raising=False)
    monkeypatch.delenv("WEB_DB_PASSWORD", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        setup_roles.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().out == (
        "ROLE SETUP FAILED: WEB_DB_USER and WEB_DB_PASSWORD are not set (see .env.example).\n"
    )


def test_main_half_configured_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setenv("WEB_DB_USER", "gradcafe_web")
    monkeypatch.delenv("WEB_DB_PASSWORD", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        setup_roles.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().out.startswith("ROLE SETUP FAILED: Missing web role setting(s): WEB_DB_PASSWORD.")


def test_main_without_tables_says_to_start_the_worker(web_role_env, capsys):
    with pytest.raises(SystemExit) as excinfo:
        setup_roles.main()  # conftest leaves only applicants

    assert excinfo.value.code == 1
    assert capsys.readouterr().out == (
        "ROLE SETUP FAILED: table(s) missing: ingestion_watermarks, analysis_summary; "
        "start the worker (it creates them) before setting up the web role\n"
    )


def test_main_as_a_non_owner_fails_cleanly(web_role, monkeypatch, capsys):
    # Connected as the web role itself: it may not create or grant anything.
    monkeypatch.setenv("DATABASE_URL", web_role[1])
    monkeypatch.setenv("WEB_DB_USER", "gradcafe_web_test_other")

    with pytest.raises(SystemExit) as excinfo:
        setup_roles.main()

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith("ROLE SETUP FAILED: permission denied to create role")
    assert out.endswith("Run setup_roles.py as the database owner (DATABASE_URL).\n")


def test_main_unreachable_database(web_role_env, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/unreachable_test")

    with pytest.raises(SystemExit) as excinfo:
        setup_roles.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().out.startswith("ROLE SETUP FAILED: ")


def test_script_entry_point_runs_main(tables, web_role_env, capsys):
    runpy.run_path(SCRIPT, run_name="__main__")

    assert capsys.readouterr().out.startswith(f"Role setup for {TEST_WEB_ROLE} on database ")
