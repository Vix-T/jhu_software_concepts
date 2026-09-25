"""Single source of database configuration for Module 4.

Every module that needs a database connection (the ORM session factory in
models.py, and the raw psycopg2 connections in load_data.py, query_data.py
and orm_queries.py) gets its URL from get_database_url() -- nothing else
reads DB settings from the environment.

DATABASE_URL uses the plain libpq URI form, which both SQLAlchemy and
psycopg2.connect() accept:

    postgresql://USER:PASSWORD@HOST:PORT/DBNAME

It is read from the process environment, falling back to module_4/.env.
A value already set in the environment wins over .env (python-dotenv's
default override=False), so tests can point everything at a separate test
database just by setting DATABASE_URL.
"""

import os

from dotenv import load_dotenv

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def get_database_url():
    """Return DATABASE_URL, raising a clear error if it isn't configured."""
    load_dotenv(ENV_FILE)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Define it in the environment or in "
            "module_4/.env as postgresql://USER:PASSWORD@HOST:PORT/DBNAME"
        )
    return url
