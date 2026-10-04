"""config.py: DB_* settings from the environment first, then the .env file, else ConfigError."""

import psycopg2
import pytest
from psycopg2.extensions import parse_dsn
from sqlalchemy.engine import make_url

from config import DEFAULT_PORT, ConfigError, db_env, get_db_url, psycopg2_dsn

pytestmark = pytest.mark.db

DB_VARS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
FULL_SETTINGS = {
    "DB_HOST": "db.example.internal",
    "DB_PORT": "6543",
    "DB_NAME": "settings_test",
    "DB_USER": "grader",
    "DB_PASSWORD": "s3cret",
}
SPECIAL_PASSWORD = "p@ss:w/rd #'\"\\%"


@pytest.fixture
def clean_db_env(monkeypatch):
    """Start with no DB_* variables, and undo anything load_dotenv() adds.

    setenv-then-delenv makes monkeypatch remember each variable's original
    state, so values written into os.environ by load_dotenv() during the
    test are rolled back too.
    """
    for name in DB_VARS:
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    return monkeypatch


@pytest.fixture
def no_env_file(tmp_path):
    return str(tmp_path / "missing.env")


def _set(monkeypatch, settings):
    for name, value in settings.items():
        monkeypatch.setenv(name, value)


def test_all_settings_build_url_and_dsn(clean_db_env, no_env_file):
    _set(clean_db_env, FULL_SETTINGS)

    url = get_db_url(env_file=no_env_file)
    assert url.drivername == "postgresql+psycopg2"
    assert (url.host, url.port, url.database, url.username, url.password) == (
        "db.example.internal", 6543, "settings_test", "grader", "s3cret",
    )
    assert parse_dsn(psycopg2_dsn(url)) == {
        "host": "db.example.internal",
        "port": "6543",
        "dbname": "settings_test",
        "user": "grader",
        "password": "s3cret",
    }


@pytest.mark.parametrize("missing", ["DB_HOST", "DB_NAME", "DB_USER"])
@pytest.mark.parametrize("how", ["unset", "empty"])
def test_each_missing_required_setting_is_named(clean_db_env, no_env_file, missing, how):
    _set(clean_db_env, FULL_SETTINGS)
    if how == "unset":
        clean_db_env.delenv(missing)
    else:
        clean_db_env.setenv(missing, "")

    with pytest.raises(ConfigError) as excinfo:
        get_db_url(env_file=no_env_file)

    message = str(excinfo.value)
    assert message.startswith(f"Missing required database setting(s): {missing}.")
    others = {"DB_HOST", "DB_NAME", "DB_USER"} - {missing}
    assert not any(name in message for name in others)
    assert isinstance(excinfo.value, RuntimeError)


def test_all_missing_settings_are_listed(clean_db_env, no_env_file):
    with pytest.raises(ConfigError, match=r"setting\(s\): DB_HOST, DB_NAME, DB_USER\. "):
        get_db_url(env_file=no_env_file)


@pytest.mark.usefixtures("clean_db_env")
def test_developer_env_file_is_not_read_during_tests():
    """conftest keeps module_5/.env out of the run, so the default env_file
    finds nothing whatever a developer's .env contains."""
    with pytest.raises(ConfigError, match=r"setting\(s\): DB_HOST, DB_NAME, DB_USER\. "):
        get_db_url()


@pytest.mark.parametrize("port", [None, ""])
def test_port_defaults_to_5432(clean_db_env, no_env_file, port):
    _set(clean_db_env, FULL_SETTINGS)
    if port is None:
        clean_db_env.delenv("DB_PORT")
    else:
        clean_db_env.setenv("DB_PORT", port)

    assert DEFAULT_PORT == 5432
    url = get_db_url(env_file=no_env_file)
    assert url.port == 5432
    assert parse_dsn(psycopg2_dsn(url))["port"] == "5432"


def test_non_integer_port_raises(clean_db_env, no_env_file):
    _set(clean_db_env, {**FULL_SETTINGS, "DB_PORT": "abc"})

    with pytest.raises(ConfigError, match="DB_PORT must be an integer, got 'abc'"):
        get_db_url(env_file=no_env_file)


@pytest.mark.parametrize("password", [None, ""])
def test_password_may_be_empty(clean_db_env, no_env_file, password):
    _set(clean_db_env, FULL_SETTINGS)
    if password is None:
        clean_db_env.delenv("DB_PASSWORD")
    else:
        clean_db_env.setenv("DB_PASSWORD", password)

    url = get_db_url(env_file=no_env_file)
    assert url.password is None
    assert url.render_as_string(hide_password=False).startswith("postgresql+psycopg2://grader@")
    dsn = parse_dsn(psycopg2_dsn(url))
    assert "password" not in dsn
    assert dsn["dbname"] == "settings_test"


def test_special_characters_in_password_are_escaped(clean_db_env, no_env_file):
    _set(clean_db_env, {**FULL_SETTINGS, "DB_PASSWORD": SPECIAL_PASSWORD})

    url = get_db_url(env_file=no_env_file)
    rendered = url.render_as_string(hide_password=False)
    # The password's "@", ":" and "/" are percent-encoded, so the only bare "@"
    # is the one separating credentials from the host.
    assert rendered.count("@") == 1
    assert "p%40ss%3Aw%2Frd" in rendered
    reparsed = make_url(rendered)
    assert reparsed.password == SPECIAL_PASSWORD
    assert (reparsed.host, reparsed.database) == ("db.example.internal", "settings_test")

    dsn = parse_dsn(psycopg2_dsn(url))
    assert dsn["password"] == SPECIAL_PASSWORD
    assert dsn["dbname"] == "settings_test"
    assert dsn["user"] == "grader"


def test_settings_read_from_env_file(clean_db_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DB_HOST=filehost\nDB_PORT=5555\nDB_NAME=from_file_test\nDB_USER=file_user\nDB_PASSWORD=\n"
    )

    url = get_db_url(env_file=str(env_file))
    assert (url.host, url.port, url.database, url.username, url.password) == (
        "filehost", 5555, "from_file_test", "file_user", None,
    )


def test_environment_wins_over_env_file(clean_db_env, tmp_path):
    _set(clean_db_env, {**FULL_SETTINGS, "DB_NAME": "from_environment_test"})
    env_file = tmp_path / ".env"
    env_file.write_text("DB_HOST=filehost\nDB_NAME=from_file_test\nDB_USER=file_user\n")

    url = get_db_url(env_file=str(env_file))
    assert url.database == "from_environment_test"
    assert url.host == "db.example.internal"
    assert url.username == "grader"


def test_explicit_url_overrides_settings(clean_db_env, no_env_file):
    _set(clean_db_env, FULL_SETTINGS)

    dsn = parse_dsn(psycopg2_dsn("postgresql://other_user@otherhost/explicit_test"))
    assert dsn == {"host": "otherhost", "dbname": "explicit_test", "user": "other_user"}


def test_db_env_round_trips_through_get_db_url(clean_db_env, no_env_file):
    original = "postgresql://grader:p%40ss@db.example.internal:6543/settings_test"
    env = db_env(original)
    assert env == {
        "DB_HOST": "db.example.internal",
        "DB_PORT": "6543",
        "DB_NAME": "settings_test",
        "DB_USER": "grader",
        "DB_PASSWORD": "p@ss",
    }

    _set(clean_db_env, env)
    url = get_db_url(env_file=no_env_file)
    assert url.render_as_string(hide_password=False) == "postgresql+psycopg2://" + original.split("://")[1]


def test_db_env_fills_default_port_and_empty_password():
    env = db_env("postgresql://grader@localhost/settings_test")
    assert env["DB_PORT"] == "5432"
    assert env["DB_PASSWORD"] == ""


def test_real_connection_uses_db_name_from_settings():
    """conftest set the DB_* variables from TEST_DATABASE_URL; the DSN must
    carry dbname, or libpq would fall back to a database named after the user."""
    conn = psycopg2.connect(psycopg2_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            name = cur.fetchone()[0]
    finally:
        conn.close()

    assert name.endswith("_test")
    assert name == get_db_url().database
