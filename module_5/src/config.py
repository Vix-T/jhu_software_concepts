"""Single source of database configuration for Module 5.

Every module that needs a database connection (the ORM session factory in
models.py, and the raw psycopg2 connections in load_data.py, query_data.py
and orm_queries.py) gets it from get_db_url() / psycopg2_dsn() -- nothing
else reads DB settings from the environment.

The connection is described by five variables:

    DB_HOST, DB_NAME, DB_USER   required
    DB_PORT                     optional, defaults to 5432
    DB_PASSWORD                 optional, may be empty (local trust auth)

They are read from the process environment, falling back to module_5/.env.
A value already set in the environment wins over .env (python-dotenv's
default override=False), so tests can point everything at a separate test
database just by setting the DB_* variables.

Connection strings are never assembled by hand: SQLAlchemy's URL.create()
and psycopg2's make_dsn() escape special characters in every field.
"""

import os

from dotenv import load_dotenv
from psycopg2.extensions import make_dsn
from sqlalchemy.engine import URL, make_url

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
REQUIRED_VARS = ("DB_HOST", "DB_NAME", "DB_USER")
DEFAULT_PORT = 5432


class ConfigError(RuntimeError):
    """A required DB_* setting is missing, or DB_PORT is not an integer."""


def get_db_url(env_file=ENV_FILE):
    """Return a SQLAlchemy URL built from the DB_* settings.

    env_file is the .env consulted for settings not already in the
    environment (default: module_5/.env). Raises ConfigError naming every
    missing required variable, or if DB_PORT is not an integer.
    """
    load_dotenv(env_file)
    missing = [name for name in REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise ConfigError(
            f"Missing required database setting(s): {', '.join(missing)}. "
            "Set them in the environment or in module_5/.env (see .env.example)."
        )
    port_text = os.environ.get("DB_PORT") or str(DEFAULT_PORT)
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ConfigError(f"DB_PORT must be an integer, got {port_text!r}.") from exc
    return URL.create(
        "postgresql+psycopg2",
        username=os.environ["DB_USER"],
        password=os.environ.get("DB_PASSWORD") or None,
        host=os.environ["DB_HOST"],
        port=port,
        database=os.environ["DB_NAME"],
    )


def psycopg2_dsn(db_url=None):
    """Return a libpq DSN for psycopg2.connect().

    db_url is a URL string or sqlalchemy URL (e.g. a test database);
    default: get_db_url() from the DB_* settings. make_dsn() quotes each
    value and leaves out fields that are None (no password, default port).
    """
    url = make_url(db_url) if db_url is not None else get_db_url()
    return make_dsn(
        host=url.host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
    )


def db_env(db_url):
    """Return the DB_* environment variables describing db_url.

    Used to hand a specific database to a child process (Pull Data) and by
    the test suite to point the whole run at the test database.
    """
    url = make_url(db_url)
    return {
        "DB_HOST": url.host or "",
        "DB_PORT": str(url.port or DEFAULT_PORT),
        "DB_NAME": url.database or "",
        "DB_USER": url.username or "",
        "DB_PASSWORD": url.password or "",
    }
