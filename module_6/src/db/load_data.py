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
from psycopg2.extras import Json

from sql_utils import (
    APPLICANT_COLUMNS,
    APPLICANTS,
    APPLICANTS_TABLE,
    SINGLE_ROW,
    clamp_limit,
)

logger = logging.getLogger(__name__)

# The connection URL every database client in the project reads, e.g.
# postgresql://USER[:PASSWORD]@HOST:PORT/DBNAME.
DATABASE_URL_ENV = "DATABASE_URL"

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
    "Check that DATABASE_URL points at a running PostgreSQL server and an existing\n"
    "database you can write to."
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
WATERMARKS_TABLE = "ingestion_watermarks"
WATERMARKS = sql.Identifier(WATERMARKS_TABLE)
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

LOCKED_WATERMARK_SQL = sql.SQL(
    "SELECT last_seen FROM {} WHERE source = %s LIMIT %s FOR UPDATE"
).format(WATERMARKS)

# Never moves the watermark backwards; updated_at records every advance attempt.
ADVANCE_WATERMARK_SQL = sql.SQL(
    "INSERT INTO {table} (source, last_seen) VALUES (%s, %s) "
    "ON CONFLICT (source) DO UPDATE SET "
    "last_seen = GREATEST({table}.last_seen, EXCLUDED.last_seen), updated_at = now()"
).format(table=WATERMARKS)

# The analysis snapshot the web page renders: a single row (id = 1) that the
# worker replaces. JSON rather than JSONB, so the stored key order is kept.
SUMMARY_TABLE = "analysis_summary"
SUMMARY = sql.Identifier(SUMMARY_TABLE)
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

# Replaces the snapshot; computed_at is the database's clock at commit time.
STORE_SUMMARY_SQL = sql.SQL(
    "INSERT INTO {} (id, results, row_count) VALUES (1, %s, %s) "
    "ON CONFLICT (id) DO UPDATE SET results = EXCLUDED.results, "
    "row_count = EXCLUDED.row_count, computed_at = now()"
).format(SUMMARY)

# Every table initialize_database() creates.
ALL_TABLES = (APPLICANTS_TABLE, WATERMARKS_TABLE, SUMMARY_TABLE)

# pg_advisory_xact_lock key serialising initialize_database() (and role setup) across workers.
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


class ConfigError(RuntimeError):
    """DATABASE_URL is not set, and no database URL was passed in."""


def connect(database_url=None):
    """Open a psycopg2 connection to database_url (default: $DATABASE_URL).

    libpq parses the URL itself, including percent-escaped characters in the
    user name or password. Raises ConfigError if there is no URL at all.
    """
    url = database_url or os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise ConfigError(f"{DATABASE_URL_ENV} is not set (see .env.example).")
    return psycopg2.connect(url)


class TableMissingError(RuntimeError):
    """A table setup_roles.py grants privileges on doesn't exist yet."""


def table_exists_query(table=APPLICANTS_TABLE):
    """SELECT to_regclass(<table>): the table's name if it exists, else NULL."""
    stmt = sql.SQL("SELECT to_regclass(%s) LIMIT %s")
    return stmt, [table, clamp_limit(SINGLE_ROW)]


def missing_tables(cur, tables):
    """The names in `tables` that don't exist in cur's database, in the given order."""
    missing = []
    for table in tables:
        cur.execute(*table_exists_query(table))
        if cur.fetchone()[0] is None:
            missing.append(table)
    return missing


def applicants_table_exists(cur):
    """True if the applicants table exists in cur's database (to_regclass)."""
    cur.execute(*table_exists_query())
    return cur.fetchone()[0] is not None


def create_table(conn):
    """Create the applicants table on `conn` if it doesn't exist yet (commits).

    Checks first and only runs CREATE TABLE when the table is missing, so an
    existing table never sees DDL.
    """
    with conn:
        with conn.cursor() as cur:
            if not applicants_table_exists(cur):
                cur.execute(CREATE_TABLE_SQL)


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


def locked_watermark(cur, source=WATERMARK_SOURCE):
    """`source`'s watermark (None if it has none), row-locked until the transaction ends.

    SELECT ... FOR UPDATE: a second scrape of the same source waits here
    until the first one commits or rolls back, so two scrapes never run on
    the same starting point.
    """
    cur.execute(LOCKED_WATERMARK_SQL, [source, clamp_limit(SINGLE_ROW)])
    row = cur.fetchone()
    return row[0] if row else None


def advance_watermark(cur, last_seen, source=WATERMARK_SOURCE):
    """Raise `source`'s watermark to `last_seen` (never lowers it), in the caller's transaction."""
    cur.execute(ADVANCE_WATERMARK_SQL, [source, last_seen])


def store_summary(cur, results, row_count):
    """Replace the analysis_summary row with `results` (a JSON-able dict); no commit."""
    cur.execute(STORE_SUMMARY_SQL, [Json(results), row_count])


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


def highest_result_id(records, failed=()):
    """Highest result ID among `records` that loaded (not listed in `failed`), or None."""
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
            watermark = highest_result_id(records, failed)
            if watermark is not None:
                advance_watermark(cur, watermark)
    logger.info("Seeded applicants from %s: %d inserted", seed_path, inserted)
    return {"inserted": inserted, "skipped": skipped, "failed": len(failed), "watermark": watermark}


def main(data_file=DATA_FILE):
    """Load `data_file` into the $DATABASE_URL database and print a summary.

    Exits with status 1 and an actionable message if the data file is missing,
    DATABASE_URL is not set, or the database can't be reached or written to.
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
