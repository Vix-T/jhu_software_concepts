"""The ingestion watermark helpers in load_data: locked read, highest loaded result ID.

(Initialisation from the seed and advance_watermark()'s never-lower rule are
in test_db_init.py; the scraper's use of the watermark in test_handlers.py.)
"""

import psycopg2
import psycopg2.errors
import pytest
from conftest import make_record, result_url

import load_data

pytestmark = pytest.mark.db


def test_no_row_means_no_watermark(db_conn, tables):
    with db_conn.cursor() as cur:
        assert load_data.locked_watermark(cur) is None
    db_conn.rollback()


def test_locked_watermark_reads_the_sources_row(db_conn, tables):
    with db_conn, db_conn.cursor() as cur:
        load_data.advance_watermark(cur, 5008)
        load_data.advance_watermark(cur, 42, source="another_source")

    with db_conn.cursor() as cur:
        assert load_data.locked_watermark(cur) == 5008
        assert load_data.locked_watermark(cur, source="another_source") == 42
    db_conn.rollback()


def test_locked_watermark_holds_a_row_lock_until_the_transaction_ends(db_conn, tables, test_database_url):
    with db_conn, db_conn.cursor() as cur:
        load_data.advance_watermark(cur, 5008)
    waiter = psycopg2.connect(test_database_url, options="-c lock_timeout=200")
    try:
        with db_conn.cursor() as cur:
            load_data.locked_watermark(cur)  # lock taken, transaction left open

        with pytest.raises(psycopg2.errors.LockNotAvailable):
            with waiter.cursor() as cur:
                load_data.locked_watermark(cur)
        waiter.rollback()

        db_conn.rollback()  # releases the lock
        with waiter.cursor() as cur:
            assert load_data.locked_watermark(cur) == 5008
        waiter.rollback()
    finally:
        waiter.close()


def test_highest_result_id_ignores_failed_and_id_less_records():
    no_url = make_record(1)
    del no_url["URL"]
    records = [
        make_record(0, URL=result_url(900)),
        no_url,
        make_record(2, URL="https://www.thegradcafe.com/survey/"),
        make_record(3, URL=result_url(300)),
    ]

    assert load_data.highest_result_id(records) == 900
    assert load_data.highest_result_id(records, failed=[(0, "rejected")]) == 300
    assert load_data.highest_result_id(records[1:3]) is None
    assert load_data.highest_result_id([]) is None
