"""Load cleaned Grad Cafe applicant data from JSON (optionally gzipped) into PostgreSQL.

Also owns the database schema: the applicants table, the ingestion_watermarks
table (the highest Grad Cafe result ID ingested so far) and the
analysis_summary table (the analysis snapshot the web page renders).
initialize_database() creates all three and seeds an empty applicants table
from the SEED_JSON file; the worker calls it at startup.
"""

import gzip
import json
import logging
import os
import re
import sys
from datetime import datetime

import psycopg2
from psycopg2 import sql

from sql_utils import (
    APPLICANT_COLUMNS,
    APPLICANTS,
    APPLICANTS_TABLE,
    INSUFFICIENT_PRIVILEGE,
    MAX_LIMIT,
    SINGLE_ROW,
    clamp_limit,
)
# Transitional: config.py still lives in src/ until the web slice replaces it.
from config import ConfigError, psycopg2_dsn

logger = logging.getLogger(__name__)

# The cleaned Module 2 dataset, bundled with the repo as src/data/applicant_data.json.
DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "applicant_data.json",
)
# Environment variable naming the JSON file initialize_database() seeds from.
SEED_ENV = "SEED_JSON"

DB_FAILURE_MESSAGE = (
    "LOAD FAILED: could not connect to PostgreSQL or write to the applicants table.\n"
    "Check that DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD (environment or module_6/.env)\n"
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

# One row per ingestion source: the highest Grad Cafe result ID (the <id> in
# /result/<id>) ingested so far. Result IDs grow with Date Added, so the
# incremental scraper treats any entry at or below it as already loaded.
WATERMARKS = sql.Identifier("ingestion_watermarks")
WATERMARK_SOURCE = "gradcafe_survey"
CREATE_WATERMARKS_SQL = sql.SQL(
    """
CREATE TABLE IF NOT EXISTS {} (
    source      TEXT PRIMARY KEY,
    last_seen   BIGINT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
).format(WATERMARKS)

# Never moves the watermark backwards; updated_at records every advance attempt.
ADVANCE_WATERMARK_SQL = sql.SQL(
    "INSERT INTO {table} (source, last_seen) VALUES (%s, %s) "
    "ON CONFLICT (source) DO UPDATE SET "
    "last_seen = GREATEST({table}.last_seen, EXCLUDED.last_seen), updated_at = now()"
).format(table=WATERMARKS)

# The analysis snapshot the web page renders: a single row (id = 1) that the
# worker replaces. JSON rather than JSONB, so the stored key order is kept.
SUMMARY = sql.Identifier("analysis_summary")
CREATE_SUMMARY_SQL = sql.SQL(
    """
CREATE TABLE IF NOT EXISTS {} (
    id          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    results     JSON NOT NULL,
    row_count   INTEGER NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
).format(SUMMARY)

# pg_advisory_xact_lock key serialising initialize_database() across workers.
INIT_LOCK_KEY = 605256006

RESULT_URL_PATTERN = re.compile(r"/result/(\d+)$")

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


def insert_rows(cur, records):
    """Insert `records` (a list of dicts) on `cur`, inside the caller's transaction.

    Never commits or rolls back the transaction itself: the caller decides
    (load_rows() commits; the worker commits once per queue message). Each
    insert runs inside its own SAVEPOINT, so a row-level database error
    (psycopg2.DataError / IntegrityError) on one record undoes only that
    record -- rows inserted earlier stay inserted and are counted correctly.
    A record whose URL is already stored is skipped (ON CONFLICT DO NOTHING).

    Returns:
        tuple: (inserted, skipped_duplicates, failed), where failed is a
            list of (record_index, reason) pairs.
    """
    inserted = 0
    skipped_duplicates = 0
    failed = []

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


def load_rows(records, conn):
    """Create the applicants table if needed and insert `records` (a list of dicts).

    The rows are inserted by insert_rows() in one transaction, committed only
    after the last record: any exception other than a row-level rejection
    propagates out of `with conn:`, which rolls back the whole batch, so an
    unexpected failure never leaves partial writes. The caller owns `conn`
    (this function commits but does not close it).

    Returns:
        tuple: insert_rows()'s (inserted, skipped_duplicates, failed).
    """
    create_table(conn)

    with conn:
        with conn.cursor() as cur:
            return insert_rows(cur, records)


def load_into_database(records, database_url=None):
    """Open a connection, load_rows() `records` into it, and close it.

    Returns load_rows()'s (inserted, skipped_duplicates, failed) tuple.
    """
    conn = connect(database_url)
    try:
        return load_rows(records, conn)
    finally:
        conn.close()


class SeedError(RuntimeError):
    """applicants is empty and there is no readable seed file to fill it from."""


def result_id(url):
    """The numeric Grad Cafe result ID at the end of a .../result/<id> URL, or None."""
    if not isinstance(url, str):
        return None
    match = RESULT_URL_PATTERN.search(url)
    return int(match.group(1)) if match else None


def advance_watermark(cur, last_seen, source=WATERMARK_SOURCE):
    """Raise `source`'s watermark to `last_seen` (never lowers it), in the caller's transaction."""
    cur.execute(ADVANCE_WATERMARK_SQL, [source, last_seen])


def init_lock_query():
    """SELECT pg_advisory_xact_lock(INIT_LOCK_KEY): held until the transaction ends."""
    stmt = sql.SQL("SELECT pg_advisory_xact_lock(%s) LIMIT %s")
    return stmt, [INIT_LOCK_KEY, clamp_limit(SINGLE_ROW)]


def any_applicant_query():
    """SELECT one applicants row, if there is any."""
    stmt = sql.SQL("SELECT 1 FROM {} LIMIT %s").format(APPLICANTS)
    return stmt, [clamp_limit(SINGLE_ROW)]


def create_tables(cur):
    """Create applicants, ingestion_watermarks and analysis_summary if missing (no commit)."""
    for statement in (CREATE_TABLE_SQL, CREATE_WATERMARKS_SQL, CREATE_SUMMARY_SQL):
        cur.execute(statement)


def read_seed(seed_path):
    """Load the seed records from `seed_path`.

    Raises SeedError if seed_path is unset, the file is missing or unreadable,
    or it isn't a JSON list of objects.
    """
    if not seed_path:
        logger.error("applicants is empty and %s is not set", SEED_ENV)
        raise SeedError(f"applicants is empty and {SEED_ENV} is not set: nothing to seed it from")
    try:
        records = load_records(seed_path)
    except (OSError, ValueError) as exc:  # missing/unreadable file, or not JSON / not UTF-8
        logger.error("Cannot read seed file %s: %s", seed_path, exc)
        raise SeedError(f"cannot read seed file {seed_path}: {exc}") from exc
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        logger.error("Seed file %s is not a JSON list of objects", seed_path)
        raise SeedError(f"seed file {seed_path} is not a JSON list of objects")
    return records


def seed_watermark(records, failed):
    """Highest result ID among the seed records that loaded, or None if none has one."""
    failed_indexes = {index for index, _ in failed}
    ids = [
        result_id(record.get("URL"))
        for index, record in enumerate(records)
        if index not in failed_indexes
    ]
    ids = [rid for rid in ids if rid is not None]
    return max(ids) if ids else None


def initialize_database(conn, seed_path=None):
    """Create the three tables, and seed applicants if it is empty -- in one transaction.

    seed_path defaults to $SEED_JSON; it is only read when applicants has no
    rows. Seeding inserts the file's records and initialises the watermark to
    the highest result ID among the rows that loaded. A transaction-level
    advisory lock (INIT_LOCK_KEY) is taken first, so two workers starting at
    once run one after the other and only one of them seeds. Safe to run
    any number of times.

    Returns None when applicants already had rows (nothing seeded), else a
    dict: {"inserted", "skipped", "failed" (a count), "watermark"}.

    Raises SeedError (nothing committed) when seeding is needed but the seed
    file is unset, unreadable, or not a JSON list of objects.
    """
    seed_path = seed_path or os.environ.get(SEED_ENV)
    with conn:
        with conn.cursor() as cur:
            cur.execute(*init_lock_query())
            create_tables(cur)
            cur.execute(*any_applicant_query())
            if cur.fetchone() is not None:
                return None
            records = read_seed(seed_path)
            inserted, skipped, failed = insert_rows(cur, records)
            watermark = seed_watermark(records, failed)
            if watermark is not None:
                advance_watermark(cur, watermark)
    logger.info("Seeded applicants from %s: %d inserted", seed_path, inserted)
    return {"inserted": inserted, "skipped": skipped, "failed": len(failed), "watermark": watermark}


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
