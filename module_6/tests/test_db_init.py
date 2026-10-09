"""load_data.initialize_database(), insert_rows() and the ingestion watermark.

Real connections to the test database; seed files are small JSON lists
written to tmp_path (never the bundled dataset). conftest drops
ingestion_watermarks and analysis_summary before every test, so each test
starts from an empty applicants table and neither of the other two.
"""

import json

import psycopg2
import psycopg2.errors
import pytest
from conftest import make_record, result_url

import load_data

pytestmark = pytest.mark.db


def _seed_file(tmp_path, records, name="seed.json"):
    path = tmp_path / name
    path.write_text(json.dumps(records), encoding="utf-8")
    return str(path)


def _records(*ids):
    return [make_record(i, URL=result_url(rid)) for i, rid in enumerate(ids)]


def _table_exists(conn, name):
    with conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", [name])
        return cur.fetchone()[0] is not None


def _watermarks(conn):
    with conn, conn.cursor() as cur:
        cur.execute("SELECT source, last_seen FROM ingestion_watermarks ORDER BY source")
        return cur.fetchall()


def _urls(fetch_rows):
    return [row["url"] for row in fetch_rows()]


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_creates_all_three_tables(db_conn, tmp_path):
    with db_conn, db_conn.cursor() as cur:
        cur.execute("DROP TABLE applicants")
    assert not _table_exists(db_conn, "applicants")

    load_data.initialize_database(db_conn, _seed_file(tmp_path, _records(7)))

    for table in ("applicants", "ingestion_watermarks", "analysis_summary"):
        assert _table_exists(db_conn, table), table
    with db_conn, db_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_name IN ('ingestion_watermarks', 'analysis_summary') "
            "ORDER BY table_name, ordinal_position"
        )
        assert cur.fetchall() == [
            ("analysis_summary", "id", "smallint"),
            ("analysis_summary", "results", "json"),
            ("analysis_summary", "row_count", "integer"),
            ("analysis_summary", "computed_at", "timestamp with time zone"),
            ("ingestion_watermarks", "source", "text"),
            ("ingestion_watermarks", "last_seen", "bigint"),
            ("ingestion_watermarks", "updated_at", "timestamp with time zone"),
        ]


def test_analysis_summary_holds_a_single_row(db_conn, tmp_path):
    load_data.initialize_database(db_conn, _seed_file(tmp_path, _records(7)))

    with db_conn, db_conn.cursor() as cur:
        cur.execute("INSERT INTO analysis_summary (results, row_count) VALUES (%s, %s)", ['{"b": 1, "a": 2}', 1])
        cur.execute("SELECT id, results::text FROM analysis_summary")
        assert cur.fetchall() == [(1, '{"b": 1, "a": 2}')]  # id defaults to 1; JSON keeps key order
    with pytest.raises(psycopg2.errors.CheckViolation):
        with db_conn, db_conn.cursor() as cur:
            cur.execute("INSERT INTO analysis_summary (id, results, row_count) VALUES (2, '{}', 0)")


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seeds_empty_applicants_and_initialises_watermark(db_conn, tmp_path, fetch_rows):
    records = _records(101, 250, 99)

    result = load_data.initialize_database(db_conn, _seed_file(tmp_path, records))

    assert result == {"inserted": 3, "skipped": 0, "failed": 0, "watermark": 250}
    assert _urls(fetch_rows) == [result_url(101), result_url(250), result_url(99)]
    assert _watermarks(db_conn) == [("gradcafe_survey", 250)]


def test_second_run_inserts_nothing(db_conn, tmp_path, row_count):
    path = _seed_file(tmp_path, _records(101, 250, 99))
    load_data.initialize_database(db_conn, path)

    assert load_data.initialize_database(db_conn, path) is None

    assert row_count() == 3
    assert _watermarks(db_conn) == [("gradcafe_survey", 250)]


def test_does_not_seed_when_applicants_has_rows(db_conn, tmp_path, seed, fetch_rows):
    seed([make_record(0, URL=result_url(5))])
    path = _seed_file(tmp_path, _records(101, 250))

    assert load_data.initialize_database(db_conn, path) is None

    assert _urls(fetch_rows) == [result_url(5)]
    assert _watermarks(db_conn) == []
    assert _table_exists(db_conn, "analysis_summary")


def test_non_empty_table_never_reads_the_seed_file(db_conn, tmp_path, seed, monkeypatch):
    monkeypatch.delenv("SEED_JSON", raising=False)
    seed([make_record(0)])

    assert load_data.initialize_database(db_conn, None) is None
    assert load_data.initialize_database(db_conn, str(tmp_path / "missing.json")) is None


def test_seed_path_defaults_to_seed_json_env(db_conn, tmp_path, monkeypatch, fetch_rows):
    monkeypatch.setenv("SEED_JSON", _seed_file(tmp_path, _records(42)))

    result = load_data.initialize_database(db_conn)

    assert result["inserted"] == 1
    assert _urls(fetch_rows) == [result_url(42)]


def test_unset_seed_json_raises_and_commits_nothing(db_conn, monkeypatch, row_count, caplog):
    monkeypatch.delenv("SEED_JSON", raising=False)

    with pytest.raises(load_data.SeedError, match="applicants is empty and SEED_JSON is not set"):
        load_data.initialize_database(db_conn)

    assert "SEED_JSON is not set" in caplog.text
    assert row_count() == 0
    assert not _table_exists(db_conn, "ingestion_watermarks")  # rolled back with the rest
    assert not _table_exists(db_conn, "analysis_summary")


def test_missing_seed_file_raises_and_commits_nothing(db_conn, tmp_path, row_count, caplog):
    missing = str(tmp_path / "nope.json")

    with pytest.raises(load_data.SeedError, match=f"cannot read seed file {missing}"):
        load_data.initialize_database(db_conn, missing)

    assert f"Cannot read seed file {missing}" in caplog.text
    assert row_count() == 0
    assert not _table_exists(db_conn, "ingestion_watermarks")


def test_seed_file_that_is_not_json_raises(db_conn, tmp_path, row_count):
    path = tmp_path / "seed.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(load_data.SeedError, match="cannot read seed file"):
        load_data.initialize_database(db_conn, str(path))
    assert row_count() == 0


@pytest.mark.parametrize("content", [{"URL": "x"}, [make_record(0), "not an object"]])
def test_seed_file_must_be_a_list_of_objects(db_conn, tmp_path, row_count, content, caplog):
    path = _seed_file(tmp_path, content)

    with pytest.raises(load_data.SeedError, match="is not a JSON list of objects"):
        load_data.initialize_database(db_conn, path)

    assert "is not a JSON list of objects" in caplog.text
    assert row_count() == 0


def test_watermark_counts_only_loaded_records_with_result_ids(db_conn, tmp_path, fetch_rows):
    no_url = make_record(1)
    del no_url["URL"]
    records = [
        make_record(0, URL=result_url(900), Comments={"not": "text"}),  # fails to load: not counted
        no_url,
        make_record(2, URL="https://www.thegradcafe.com/survey/?page=2"),  # loads, has no result ID
        make_record(3, URL=result_url(300)),
    ]

    result = load_data.initialize_database(db_conn, _seed_file(tmp_path, records))

    assert result == {"inserted": 2, "skipped": 0, "failed": 2, "watermark": 300}
    assert _urls(fetch_rows) == [records[2]["URL"], result_url(300)]
    assert _watermarks(db_conn) == [("gradcafe_survey", 300)]


def test_no_result_ids_leaves_no_watermark(db_conn, tmp_path, row_count):
    result = load_data.initialize_database(db_conn, _seed_file(tmp_path, [make_record(0)]))

    assert result["watermark"] is None
    assert row_count() == 1
    assert _watermarks(db_conn) == []


def test_initialization_waits_for_the_advisory_lock(db_conn, test_database_url, tmp_path):
    path = _seed_file(tmp_path, _records(1))
    holder = psycopg2.connect(test_database_url)
    waiter = psycopg2.connect(test_database_url, options="-c lock_timeout=200")
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [load_data.INIT_LOCK_KEY])

        with pytest.raises(psycopg2.errors.LockNotAvailable):
            load_data.initialize_database(waiter, path)
        assert not _table_exists(db_conn, "ingestion_watermarks")  # it never got past the lock

        holder.rollback()  # releases the transaction-level lock
        assert load_data.initialize_database(waiter, path)["inserted"] == 1
    finally:
        holder.close()
        waiter.close()


# ---------------------------------------------------------------------------
# insert_rows() and the watermark helpers
# ---------------------------------------------------------------------------


def test_insert_rows_leaves_the_transaction_to_the_caller(db_conn, test_database_url, row_count):
    other = psycopg2.connect(test_database_url)
    try:
        with db_conn.cursor() as cur:
            assert load_data.insert_rows(cur, _records(1, 2)) == (2, 0, [])
            with other, other.cursor() as other_cur:  # not committed: invisible elsewhere
                other_cur.execute("SELECT COUNT(*) FROM applicants")
                assert other_cur.fetchone()[0] == 0
        db_conn.rollback()
    finally:
        other.close()

    assert row_count() == 0


def test_insert_rows_skips_stored_urls(db_conn, seed, fetch_rows):
    seed(_records(1))

    with db_conn, db_conn.cursor() as cur:
        assert load_data.insert_rows(cur, _records(1, 2, 3)) == (2, 1, [])

    assert _urls(fetch_rows) == [result_url(1), result_url(2), result_url(3)]


def test_advance_watermark_never_lowers_it(db_conn, tmp_path):
    load_data.initialize_database(db_conn, _seed_file(tmp_path, _records(250)))

    with db_conn, db_conn.cursor() as cur:
        load_data.advance_watermark(cur, 100)
    assert _watermarks(db_conn) == [("gradcafe_survey", 250)]

    with db_conn, db_conn.cursor() as cur:
        load_data.advance_watermark(cur, 400)
        load_data.advance_watermark(cur, 7, source="another_source")
    assert _watermarks(db_conn) == [("another_source", 7), ("gradcafe_survey", 400)]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.thegradcafe.com/result/1020478", 1020478),
        ("/result/5", 5),
        ("https://www.thegradcafe.com/result/test-3", None),
        ("https://www.thegradcafe.com/result/12/extra", None),
        ("https://www.thegradcafe.com/survey/", None),
        (None, None),
        (12345, None),
    ],
)
def test_result_id(url, expected):
    assert load_data.result_id(url) == expected
