"""Read-only database access for the web service.

The web service only reads: the analysis snapshot the worker stores in
analysis_summary, the time of the last pull (ingestion_watermarks), and
applicant rows for /api/applicants. Every connection is opened read-only,
and every statement is a psycopg2.sql composed object with values (LIMIT
included) as bound parameters.
"""

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
DB_NOT_INITIALIZED = "database not initialized"
DB_PERMISSION_DENIED = "database permission denied"

WATERMARK_SOURCE = "gradcafe_survey"
ONE_ROW = 1

SUMMARY_QUERY = sql.SQL("SELECT results, row_count, computed_at FROM {} LIMIT %s").format(
    sql.Identifier("analysis_summary")
)
LAST_PULL_QUERY = sql.SQL("SELECT updated_at FROM {} WHERE source = %s LIMIT %s").format(
    sql.Identifier("ingestion_watermarks")
)


def db_error_message(exc):
    """The short reason a DB_READ_ERRORS exception is reported with."""
    if isinstance(exc, UNDEFINED_TABLE):
        return DB_NOT_INITIALIZED
    if isinstance(exc, INSUFFICIENT_PRIVILEGE):
        return DB_PERMISSION_DENIED
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
