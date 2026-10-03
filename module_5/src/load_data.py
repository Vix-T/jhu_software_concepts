"""Load cleaned Grad Cafe applicant data from JSON (optionally gzipped) into PostgreSQL."""

import gzip
import json
import os
import sys
from datetime import datetime

import psycopg2

from config import get_database_url

# The cleaned Module 2 dataset, bundled with the repo (gzipped: 50.5 MB -> 4.0 MB).
DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "llm_extend_applicant_data_full.json.gz",
)

DB_FAILURE_MESSAGE = (
    "LOAD FAILED: could not connect to PostgreSQL or write to the applicants table.\n"
    "Check that DATABASE_URL is set (environment or module_5/.env) and points at a "
    "running PostgreSQL server and an existing database you can write to."
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS applicants (
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

INSERT_SQL = """
INSERT INTO applicants (
    program, comments, date_added, url, status, term,
    us_or_international, gpa, gre, gre_v, gre_aw, degree,
    llm_generated_program, llm_generated_university
) VALUES (
    %(program)s, %(comments)s, %(date_added)s, %(url)s, %(status)s, %(term)s,
    %(us_or_international)s, %(gpa)s, %(gre)s, %(gre_v)s, %(gre_aw)s, %(degree)s,
    %(llm_generated_program)s, %(llm_generated_university)s
)
ON CONFLICT (url) DO NOTHING;
"""


def parse_float(value):
    """Convert a scraped score (e.g. "3.91") to float; None if missing or not a number."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_date(value):
    """Parse a Grad Cafe "Date Added" string such as "Sep 08, 2026"; None if missing or malformed."""
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


def record_to_row(record):
    """Map one scraped/cleaned record (JSON keys) to an applicants-table row dict.

    Raises:
        ValueError: if the record has no URL, which is the table's natural key.
    """
    url = record.get("URL")
    if not url:
        raise ValueError("missing URL (required as natural key)")

    return {
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


def load_records(path):
    """Read a JSON list of records from `path`; a path ending in .gz is gunzipped."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def connect(database_url=None):
    """Open a psycopg2 connection to database_url (default: DATABASE_URL)."""
    return psycopg2.connect(database_url or get_database_url())


def create_table(conn):
    """Create the applicants table on `conn` if it doesn't exist yet (commits)."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)


def ensure_table(database_url=None):
    """Create the applicants table in database_url (default: DATABASE_URL) if needed."""
    conn = connect(database_url)
    try:
        create_table(conn)
    finally:
        conn.close()


def existing_urls(urls, database_url=None):
    """Return the subset of `urls` already present in the applicants table."""
    if not urls:
        return set()
    conn = connect(database_url)
    try:
        create_table(conn)
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT url FROM applicants WHERE url = ANY(%s)", (list(urls),))
                return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def load_rows(records, conn):
    """Create the applicants table if needed and insert `records` (a list of dicts).

    Each insert runs inside its own SAVEPOINT, so a database error on one
    record rolls back only that record -- rows inserted earlier in the same
    batch stay inserted and are counted correctly. All inserts share one
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

                cur.execute("SAVEPOINT load_row")
                try:
                    cur.execute(INSERT_SQL, row)
                except psycopg2.Error as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT load_row")
                    failed.append((i, str(exc).strip()))
                    continue
                rowcount = cur.rowcount
                cur.execute("RELEASE SAVEPOINT load_row")

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
    """Load `data_file` into DATABASE_URL and print a summary.

    Exits with status 1 and an actionable message if the data file is missing
    or the database can't be reached or written to.
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
