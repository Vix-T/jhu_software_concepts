"""The worker's task handlers, against the real test database.

handle_scrape_new_data reads pages through FakeDriver (no browser): the pull
fixture pages hold, newest first, 5010 and 5009 (new), 5008-5006 (page 1),
5005-5004 (page 2) and 5003 (page 3). Known rows 5004-5008 are seeded with
load_data.initialize_database(), which sets the watermark to 5008.

Handlers never commit: each test commits (as the consumer does after a
successful handler) or rolls back.
"""

import json

import psycopg2
import psycopg2.errors
import pytest
from conftest import PULL_PAGE_URLS, SURVEY_URL, DriverFactory, load_fixture, make_record, pull_pages, result_url

import consumer
import load_data
from etl import incremental_scraper

pytestmark = pytest.mark.db

KNOWN_IDS = [5004, 5005, 5006, 5007, 5008]


@pytest.fixture
def known(db_conn, tmp_path):
    """Seed rows 5004-5008 through initialize_database (watermark 5008, all tables created)."""
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps([make_record(i, URL=result_url(rid)) for i, rid in enumerate(KNOWN_IDS)]))
    assert load_data.initialize_database(db_conn, str(seed_file))["watermark"] == 5008


def _browser(factory, start_url=PULL_PAGE_URLS[0], **settings):
    return incremental_scraper.BrowserSettings(
        driver_factory=factory, start_url=start_url, delay_seconds=0, crash_retry_wait=0, **settings
    )


def _scrape(db_conn, payload=None, factory=None):
    factory = factory or DriverFactory(pull_pages())
    return consumer.handle_scrape_new_data(db_conn, payload or {}, browser=_browser(factory))


def _query(conn, statement, params=()):
    with conn, conn.cursor() as cur:
        cur.execute(statement, params)
        return cur.fetchall()


def _ids(conn):
    rows = _query(conn, "SELECT url FROM applicants")
    return sorted(load_data.result_id(url) for (url,) in rows)


def _watermark(conn):
    return _query(conn, "SELECT last_seen, updated_at FROM ingestion_watermarks")


def _summary(conn):
    return _query(conn, "SELECT row_count, results->>'q1_count', computed_at FROM analysis_summary")


# ---------------------------------------------------------------------------
# scrape_new_data
# ---------------------------------------------------------------------------


def test_scrape_inserts_new_rows_advances_watermark_and_recomputes(db_conn, known):
    factory = DriverFactory(pull_pages())

    result = _scrape(db_conn, factory=factory)
    db_conn.commit()

    assert result == {"scraped": 2, "inserted": 2, "skipped": 0, "failed": 0, "watermark": 5010}
    assert factory.visited == PULL_PAGE_URLS[:2]
    assert _ids(db_conn) == KNOWN_IDS + [5009, 5010]
    [(last_seen, _)] = _watermark(db_conn)
    assert last_seen == 5010
    [(row_count, _, _)] = _summary(db_conn)
    assert row_count == 7  # recomputed in the same transaction, over the new rows too


def test_scrape_leaves_the_transaction_to_the_caller(db_conn, known, test_database_url):
    _scrape(db_conn)

    other = psycopg2.connect(test_database_url)
    try:
        assert _ids(other) == KNOWN_IDS  # nothing committed yet
    finally:
        other.close()
    db_conn.rollback()
    assert _ids(db_conn) == KNOWN_IDS
    assert _watermark(db_conn)[0][0] == 5008
    assert _summary(db_conn) == []


def test_repeat_scrape_finds_nothing_new_but_stamps_the_pull(db_conn, known):
    _scrape(db_conn)
    db_conn.commit()
    [(_, first_pull)] = _watermark(db_conn)

    result = _scrape(db_conn)
    db_conn.commit()

    assert (result["scraped"], result["inserted"], result["watermark"]) == (0, 0, 5010)
    [(last_seen, second_pull)] = _watermark(db_conn)
    assert last_seen == 5010
    assert second_pull > first_pull  # "Data last pulled" moves even when nothing was new


def test_payload_since_overrides_the_stored_watermark(db_conn, known):
    result = _scrape(db_conn, {"since": 5005})
    db_conn.commit()

    # 5010-5006 count as new; 5008-5006 are already stored (ON CONFLICT skips them);
    # page 2 (5005, 5004) is all at or below 5005, so the scrape stops there.
    assert (result["scraped"], result["inserted"], result["skipped"]) == (5, 2, 3)
    assert _ids(db_conn) == KNOWN_IDS + [5009, 5010]
    assert _watermark(db_conn)[0][0] == 5010


def test_since_never_lowers_the_watermark(db_conn, known):
    factory = DriverFactory({SURVEY_URL: load_fixture("page_no_results.html")})

    result = consumer.handle_scrape_new_data(db_conn, {"since": 0}, browser=_browser(factory, SURVEY_URL, wait_timeout=0))
    db_conn.commit()

    assert result["watermark"] == 5008
    assert _watermark(db_conn)[0][0] == 5008


@pytest.mark.parametrize("since", [-1, "5005", True, 5005.0, [5005]])
def test_invalid_since_is_rejected_before_scraping(db_conn, known, since):
    factory = DriverFactory(pull_pages())

    with pytest.raises(ValueError, match="payload 'since' must be a non-negative integer"):
        _scrape(db_conn, {"since": since}, factory=factory)

    assert factory.drivers == []


def test_first_scrape_without_a_watermark_takes_everything(db_conn, tables):
    result = _scrape(db_conn)
    db_conn.commit()

    assert (result["scraped"], result["inserted"], result["watermark"]) == (8, 8, 5010)
    assert _ids(db_conn) == list(range(5003, 5011))


def test_nothing_scraped_and_no_watermark_leaves_no_watermark_row(db_conn, tables):
    factory = DriverFactory({SURVEY_URL: load_fixture("page_no_results.html")})

    result = consumer.handle_scrape_new_data(db_conn, {}, browser=_browser(factory, SURVEY_URL, wait_timeout=0))
    db_conn.commit()

    assert result["watermark"] is None
    assert _watermark(db_conn) == []
    assert _summary(db_conn)[0][0] == 0  # the summary is still recomputed


def test_a_second_scrape_waits_for_the_watermark_lock(db_conn, known, test_database_url):
    _scrape(db_conn)  # holds the watermark row lock until it commits or rolls back
    waiter = psycopg2.connect(test_database_url, options="-c lock_timeout=200")
    factory = DriverFactory(pull_pages())
    try:
        with pytest.raises(psycopg2.errors.LockNotAvailable):
            consumer.handle_scrape_new_data(waiter, {}, browser=_browser(factory))
        waiter.rollback()
        assert factory.drivers == []  # it never started scraping
    finally:
        waiter.close()
    db_conn.rollback()


def test_scrape_failure_propagates_and_rolls_back_cleanly(db_conn, known):
    def crashing_factory():
        raise incremental_scraper.WebDriverException("session not created")

    with pytest.raises(incremental_scraper.ScrapeRetriesExhausted):
        _scrape(db_conn, factory=crashing_factory)
    db_conn.rollback()

    assert _ids(db_conn) == KNOWN_IDS
    assert _watermark(db_conn)[0][0] == 5008


# ---------------------------------------------------------------------------
# recompute_analytics
# ---------------------------------------------------------------------------


def test_recompute_stores_the_summary(db_conn, known):
    summary = consumer.handle_recompute_analytics(db_conn, {})
    db_conn.commit()

    assert summary["q1_count"] == 5
    [(row_count, q1, _)] = _summary(db_conn)
    assert (row_count, q1) == (5, "5")


def test_recompute_leaves_the_transaction_to_the_caller(db_conn, known):
    consumer.handle_recompute_analytics(db_conn, {})
    db_conn.rollback()

    assert _summary(db_conn) == []


def test_recompute_rejects_payload_parameters(db_conn, known):
    with pytest.raises(ValueError, match=r"recompute_analytics takes no payload parameters, got \['since'\]"):
        consumer.handle_recompute_analytics(db_conn, {"since": 1})


def test_task_map_routes_each_kind_to_its_handler():
    assert consumer.TASKS == {
        "scrape_new_data": consumer.handle_scrape_new_data,
        "recompute_analytics": consumer.handle_recompute_analytics,
    }
