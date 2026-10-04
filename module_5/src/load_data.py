"""Load cleaned Grad Cafe applicant data from JSON (optionally gzipped) into PostgreSQL."""

import gzip
import json
import logging
import os
import sys
from datetime import datetime

import psycopg2
from psycopg2 import sql

from config import ConfigError, psycopg2_dsn
from sql_utils import (
    APPLICANT_COLUMNS,
    APPLICANTS,
    APPLICANTS_TABLE,
    INSUFFICIENT_PRIVILEGE,
    MAX_LIMIT,
    SINGLE_ROW,
    clamp_limit,
)

logger = logging.getLogger(__name__)

# The cleaned Module 2 dataset, bundled with the repo (gzipped: 50.5 MB -> 4.0 MB).
DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "llm_extend_applicant_data_full.json.gz",
)

DB_FAILURE_MESSAGE = (
    "LOAD FAILED: could not connect to PostgreSQL or write to the applicants table.\n"
    "Check that DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD (environment or module_5/.env)\n"
    "point at a running PostgreSQL server and an existing database you can write to."
)

CREATE_TABLE_SQL = sql.SQL(
    """
CREATE TABLE IF NOT EXISTS {} (
    p_id                        SERIAL PRIMARY KEY,
    program                     TEXT,
    comments                    TEXT,
    date_added                  DATE,
    url                         TEXT UNIQUE,
    status                      TEXT,
    term                        TEXT,
    us_or_international         TEXT,
    gpa                         FLOAT,
    gre                         FLOAT,
    gre_v                       FLOAT,
    gre_aw                      FLOAT,
    degree                      TEXT,
    llm_generated_program       TEXT,
    llm_generated_university    TEXT
);
"""
).format(APPLICANTS)

# Columns written by INSERT_SQL (every column but the SERIAL p_id), in order;
# record_to_row() returns a dict with exactly these keys, bound by name
# through sql.Placeholder(column).
INSERT_COLUMNS = tuple(column for column in APPLICANT_COLUMNS if column != "p_id")

INSERT_SQL = sql.SQL(
    "INSERT INTO {table} ({columns}) VALUES ({values}) ON CONFLICT ({key}) DO NOTHING"
).format(
    table=APPLICANTS,
    columns=sql.SQL(", ").join(sql.Identifier(column) for column in INSERT_COLUMNS),
    values=sql.SQL(", ").join(sql.Placeholder(column) for column in INSERT_COLUMNS),
    key=sql.Identifier("url"),
)

# Per-row savepoint in load_rows() (transaction control: no values, no LIMIT).
_SAVEPOINT = sql.Identifier("load_row")
SAVEPOINT_SQL = sql.SQL("SAVEPOINT {}").format(_SAVEPOINT)
ROLLBACK_TO_SAVEPOINT_SQL = sql.SQL("ROLLBACK TO SAVEPOINT {}").format(_SAVEPOINT)
RELEASE_SAVEPOINT_SQL = sql.SQL("RELEASE SAVEPOINT {}").format(_SAVEPOINT)


def parse_float(value):
    """Convert a scraped score (e.g. "3.91") to float; None if missing or not a number."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_date(value):
    """Parse a Grad Cafe "Date Added" string like "Sep 08, 2026"; None if missing or malformed."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%b %d, %Y").date()
    except ValueError:
        return None


def build_program(record):
    """Combine University and Program Name into the "University, Program" program string.

    Falls back to whichever part is present, or None if neither is.
    """
    university = (record.get("University") or "").strip()
    program_name = (record.get("Program Name") or "").strip()
    if university and program_name:
        return f"{university}, {program_name}"
    return university or program_name or None


# Row columns stored as TEXT: each must be a str (or None) before it reaches psycopg2.
TEXT_COLUMNS = (
    "comments",
    "url",
    "status",
    "term",
    "us_or_international",
    "degree",
    "llm_generated_program",
    "llm_generated_university",
)


def record_to_row(record):
    """Map one scraped/cleaned record (JSON keys) to an applicants-table row dict.

    Raises:
        ValueError: if the record has no URL (the table's natural key), or a
            text field holds something other than a string.
    """
    url = record.get("URL")
    if not url:
        raise ValueError("missing URL (required as natural key)")

    row = {
        "program": build_program(record),
        "comments": record.get("Comments"),
        "date_added": parse_date(record.get("Date Added")),
        "url": url,
        "status": record.get("Applicant Status"),
        "term": record.get("Semester and Year"),
        "us_or_international": record.get("International/American"),
        "gpa": parse_float(record.get("GPA")),
        "gre": parse_float(record.get("GRE Score")),
        "gre_v": parse_float(record.get("GRE V Score")),
        "gre_aw": parse_float(record.get("GRE AW Score")),
        "degree": record.get("Masters or PhD"),
        "llm_generated_program": record.get("llm-generated-program"),
        "llm_generated_university": record.get("llm-generated-university"),
    }
    for column in TEXT_COLUMNS:
        value = row[column]
        if value is not None and not isinstance(value, str):
            raise ValueError(f"field '{column}' must be text, got {type(value).__name__}")
    return row


def load_records(path):
    """Read a JSON list of records from `path`; a path ending in .gz is gunzipped."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def connect(database_url=None):
    """Open a psycopg2 connection to database_url (default: the DB_* settings)."""
    return psycopg2.connect(psycopg2_dsn(database_url))


TABLE_MISSING_MESSAGE = "applicants table missing; run load_data.py as the database owner"


class TableMissingError(RuntimeError):
    """The applicants table doesn't exist and the connected role isn't allowed to create it."""


def table_exists_query():
    """SELECT to_regclass(<applicants>): the table's name if it exists, else NULL."""
    stmt = sql.SQL("SELECT to_regclass(%s) LIMIT %s")
    return stmt, [APPLICANTS_TABLE, clamp_limit(SINGLE_ROW)]


def create_table(conn):
    """Create the applicants table on `conn` if it doesn't exist yet (commits).

    Checks to_regclass() first and only runs CREATE TABLE when the table is
    missing, so a role without CREATE on the schema (the app role) never
    attempts DDL against a table that already exists. If the table is
    missing and the role may not create it, raises TableMissingError.
    """
    with conn:
        with conn.cursor() as cur:
            stmt, params = table_exists_query()
            cur.execute(stmt, params)
            if cur.fetchone()[0] is not None:
                return
            try:
                cur.execute(CREATE_TABLE_SQL)
            except INSUFFICIENT_PRIVILEGE as exc:
                logger.error("Cannot create the applicants table as %s: %s", conn.info.user, exc)
                raise TableMissingError(TABLE_MISSING_MESSAGE) from exc


def ensure_table(database_url=None):
    """Create the applicants table in database_url (default: the DB_* settings) if needed."""
    conn = connect(database_url)
    try:
        create_table(conn)
    finally:
        conn.close()


def existing_urls_query(batch):
    """SELECT the URLs of `batch` (at most MAX_LIMIT candidates) that are already stored.

    url is UNIQUE, so at most len(batch) rows can match: LIMIT len(batch)
    never cuts off a real match.
    """
    stmt = sql.SQL("SELECT {url} FROM {table} WHERE {url} = ANY(%s) LIMIT %s").format(
        url=sql.Identifier("url"), table=APPLICANTS
    )
    return stmt, [list(batch), clamp_limit(len(batch))]


def existing_urls(urls, database_url=None):
    """Return the subset of `urls` already present in the applicants table.

    Only the candidate URLs are looked up, in batches of at most MAX_LIMIT.
    """
    urls = list(urls)
    if not urls:
        return set()
    found = set()
    conn = connect(database_url)
    try:
        create_table(conn)
        with conn:
            with conn.cursor() as cur:
                for start in range(0, len(urls), MAX_LIMIT):
                    stmt, params = existing_urls_query(urls[start:start + MAX_LIMIT])
                    cur.execute(stmt, params)
                    found.update(row[0] for row in cur.fetchall())
    finally:
        conn.close()
    return found


def load_rows(records, conn):
    """Create the applicants table if needed and insert `records` (a list of dicts).

    Each insert runs inside its own SAVEPOINT, so a row-level database error
    (psycopg2.DataError / IntegrityError) on one record rolls back only that
    record -- rows inserted earlier in the same batch stay inserted and are
    counted correctly. All inserts share one
    transaction, committed only after the last record: any other exception
    raised mid-batch propagates out of `with conn:`, which rolls back the
    whole batch, so an unexpected failure never leaves partial writes. The
    caller owns `conn` (this function commits but does not close it).

    Returns:
        tuple: (inserted, skipped_duplicates, failed), where failed is a
            list of (record_index, reason) pairs.
    """
    inserted = 0
    skipped_duplicates = 0
    failed = []

    create_table(conn)

    with conn:
        with conn.cursor() as cur:
            for i, record in enumerate(records):
                try:
                    row = record_to_row(record)
                except (ValueError, TypeError) as exc:
                    failed.append((i, str(exc)))
                    continue

                cur.execute(SAVEPOINT_SQL)
                try:
                    cur.execute(INSERT_SQL, row)
                except (psycopg2.DataError, psycopg2.IntegrityError) as exc:
                    # Row-level rejection (bad value, constraint): undo just this row.
                    cur.execute(ROLLBACK_TO_SAVEPOINT_SQL)
                    failed.append((i, str(exc).strip()))
                    continue
                rowcount = cur.rowcount
                cur.execute(RELEASE_SAVEPOINT_SQL)

                if rowcount == 1:
                    inserted += 1
                else:
                    skipped_duplicates += 1

    return inserted, skipped_duplicates, failed


def load_into_database(records, database_url=None):
    """Open a connection, load_rows() `records` into it, and close it.

    Returns load_rows()'s (inserted, skipped_duplicates, failed) tuple.
    """
    conn = connect(database_url)
    try:
        return load_rows(records, conn)
    finally:
        conn.close()


def main(data_file=DATA_FILE):
    """Load `data_file` into the DB_* database and print a summary.

    Exits with status 1 and an actionable message if the data file is missing,
    the DB_* settings are incomplete, or the database can't be reached or
    written to.
    """
    try:
        records = load_records(data_file)
    except FileNotFoundError:
        print(f"LOAD FAILED: data file not found: {data_file}")
        print("Pass the path to a JSON (or .json.gz) data file as the first argument.")
        sys.exit(1)

    try:
        inserted, skipped_duplicates, failed = load_into_database(records)
    except psycopg2.Error as exc:
        print(DB_FAILURE_MESSAGE)
        print(f"Underlying error: {str(exc).strip().splitlines()[0]}")
        sys.exit(1)
    except ConfigError as exc:
        print(f"LOAD FAILED: {exc}")
        sys.exit(1)

    print("Load summary:")
    print(f"  Inserted:           {inserted}")
    print(f"  Skipped duplicates: {skipped_duplicates}")
    print(f"  Failed to parse:    {len(failed)}")
    if failed:
        print("  Failure details (first 10):")
        for index, reason in failed[:10]:
            print(f"    record[{index}]: {reason}")

    return inserted, skipped_duplicates, failed


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DATA_FILE)
