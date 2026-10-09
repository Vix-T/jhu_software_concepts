"""Read-only database access for the web service.

The web service only reads: the analysis snapshot the worker stores in
analysis_summary, the time of the last pull (ingestion_watermarks), and
applicant rows for /api/applicants. Every connection is opened read-only,
and every statement is a psycopg2.sql composed object with values (LIMIT
included) as bound parameters.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg2
import psycopg2.errorcodes
import psycopg2.errors
from psycopg2 import sql

# SQLSTATE classes looked up by code (psycopg2.errors is a C extension Pylint can't inspect).
INSUFFICIENT_PRIVILEGE = psycopg2.errors.lookup(psycopg2.errorcodes.INSUFFICIENT_PRIVILEGE)
UNDEFINED_TABLE = psycopg2.errors.lookup(psycopg2.errorcodes.UNDEFINED_TABLE)

# Database failures every read route reports as 503, and what it says about each.
DB_READ_ERRORS = (psycopg2.OperationalError, UNDEFINED_TABLE, INSUFFICIENT_PRIVILEGE)
DB_UNAVAILABLE = "database unavailable"
# The worker has not finished its first start: tables, seed, summary and web role.
DB_INITIALIZING = "database initializing"
DB_PERMISSION_DENIED = "database permission denied"

WATERMARK_SOURCE = "gradcafe_survey"
ONE_ROW = 1

SUMMARY_QUERY = sql.SQL("SELECT results, row_count, computed_at FROM {} LIMIT %s").format(
    sql.Identifier("analysis_summary")
)
LAST_PULL_QUERY = sql.SQL("SELECT updated_at FROM {} WHERE source = %s LIMIT %s").format(
    sql.Identifier("ingestion_watermarks")
)


# The server refused the web role's login: on a first start the worker hasn't created
# the role yet. psycopg2 gives connection failures no SQLSTATE, so this matches the
# server's message. (A wrong WEB_DB_PASSWORD reads the same way, and so also shows as
# "initializing" -- the server doesn't tell the two apart.)
LOGIN_REFUSED = re.compile(r'password authentication failed for user|role "[^"]*" does not exist')


def db_error_message(exc):
    """The short reason a DB_READ_ERRORS exception is reported with.

    Missing tables and a refused login mean the worker's first start isn't
    done yet (DB_INITIALIZING); anything else that stops the connection is
    DB_UNAVAILABLE.
    """
    if isinstance(exc, UNDEFINED_TABLE):
        return DB_INITIALIZING
    if isinstance(exc, INSUFFICIENT_PRIVILEGE):
        return DB_PERMISSION_DENIED
    if LOGIN_REFUSED.search(str(exc)):
        return DB_INITIALIZING
    return DB_UNAVAILABLE


def connect(database_url):
    """Open a read-only psycopg2 connection to database_url."""
    conn = psycopg2.connect(database_url)
    conn.set_session(readonly=True)
    return conn


def utc_iso(moment):
    """A TIMESTAMPTZ value as an ISO 8601 UTC string, or None."""
    return None if moment is None else moment.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Snapshot:
    """What the page shows: the stored analysis (None until computed) and two timestamps."""

    results: dict | None
    row_count: int | None
    computed_at: datetime | None
    last_pulled_at: datetime | None


def read_snapshot(database_url):
    """Read the analysis_summary row and the last pull time, in one read-only transaction."""
    conn = connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(SUMMARY_QUERY, [ONE_ROW])
            summary = cur.fetchone()
            cur.execute(LAST_PULL_QUERY, [WATERMARK_SOURCE, ONE_ROW])
            pulled = cur.fetchone()
    finally:
        conn.close()
    results, row_count, computed_at = summary or (None, None, None)
    return Snapshot(results, row_count, computed_at, pulled[0] if pulled else None)
