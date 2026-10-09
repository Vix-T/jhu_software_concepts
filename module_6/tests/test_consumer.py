"""consumer.py: one transaction per message, commit before ack, nack without requeue, and startup.

pika.BlockingConnection is the only fake (conftest `broker`); pika's method
frames and properties are real. Handlers run against the real test database.
A scrape uses the real handler with FakeDriver pages, passed in through
process_message()'s task map (the scraper's browser seam).
"""

import functools
import json
import logging
import os
import runpy

import psycopg2
import pytest
from conftest import PULL_PAGE_URLS, DriverFactory, deliver_method, make_record, pull_pages, result_url
from pika.exceptions import AMQPConnectionError

import consumer
import load_data
from etl import incremental_scraper

pytestmark = pytest.mark.integration

CONSUMER_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "worker", "consumer.py")


def _body(kind, payload=None):
    return json.dumps({"kind": kind, "ts": "2026-10-09T12:00:00+00:00", "payload": payload or {}}).encode()


def _fake_scrape_tasks():
    """The real task map, with the scrape handler reading fixture pages instead of attaching to Chrome."""
    browser = incremental_scraper.BrowserSettings(
        driver_factory=DriverFactory(pull_pages()), start_url=PULL_PAGE_URLS[0], delay_seconds=0
    )
    return {**consumer.TASKS, "scrape_new_data": functools.partial(consumer.handle_scrape_new_data, browser=browser)}


def _count(conn, table):
    with conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


@pytest.fixture
def watermarked(db_conn, tmp_path):
    """Rows 5004-5008 seeded through initialize_database (watermark 5008)."""
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps([make_record(i, URL=result_url(5004 + i)) for i in range(5)]))
    load_data.initialize_database(db_conn, str(seed_file))


# ---------------------------------------------------------------------------
# One message, one transaction
# ---------------------------------------------------------------------------


def test_commit_happens_before_ack(db_conn, channel, broker, watermarked, test_database_url):
    seen_at_ack = []

    def check_committed(tag):
        other = psycopg2.connect(test_database_url)  # a separate session sees only committed data
        try:
            seen_at_ack.append((tag, _count(other, "analysis_summary")))
        finally:
            other.close()

    broker.ack_hook = check_committed

    consumer.process_message(db_conn, channel, deliver_method(7), _body("recompute_analytics"))

    assert seen_at_ack == [(7, 1)]
    assert broker.acks == [7] and broker.nacks == []


def test_scrape_message_commits_rows_watermark_and_summary(db_conn, channel, broker, watermarked):
    consumer.process_message(db_conn, channel, deliver_method(1), _body("scrape_new_data"), _fake_scrape_tasks())

    assert broker.acks == [1] and broker.nacks == []
    assert _count(db_conn, "applicants") == 7
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT last_seen FROM ingestion_watermarks")
        assert cur.fetchone()[0] == 5010
        cur.execute("SELECT row_count FROM analysis_summary")
        assert cur.fetchone()[0] == 7


def test_handler_error_rolls_back_everything_and_nacks_without_requeue(db_conn, channel, broker, watermarked, caplog):
    # The scrape inserts rows and advances the watermark, then the summary
    # write fails (its table is gone): nothing from the message may persist.
    with db_conn, db_conn.cursor() as cur:
        cur.execute("DROP TABLE analysis_summary")

    consumer.process_message(db_conn, channel, deliver_method(3), _body("scrape_new_data"), _fake_scrape_tasks())

    assert broker.acks == []
    assert broker.nacks == [(3, False)]
    assert _count(db_conn, "applicants") == 5
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT last_seen FROM ingestion_watermarks")
        assert cur.fetchone()[0] == 5008
    assert "Task scrape_new_data (message 3) failed: UndefinedTable" in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        incremental_scraper.PullPreconditionError("no Chrome"),
        incremental_scraper.ScrapeRetriesExhausted("crashed 4 times"),
        incremental_scraper.WebDriverException("cannot attach"),
        psycopg2.OperationalError("server closed the connection"),
        ValueError("payload 'since' must be a non-negative integer, got -1"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_expected_errors_are_nacked_and_the_worker_continues(db_conn, channel, broker, tables, caplog, error):
    def failing(conn, payload):
        raise error

    tasks = {**consumer.TASKS, "scrape_new_data": failing}

    consumer.process_message(db_conn, channel, deliver_method(1), _body("scrape_new_data"), tasks)
    consumer.process_message(db_conn, channel, deliver_method(2), _body("recompute_analytics"), tasks)

    assert broker.nacks == [(1, False)]
    assert broker.acks == [2]  # the next message is processed normally
    assert f"Task scrape_new_data (message 1) failed: {type(error).__name__}" in caplog.text


def test_unexpected_error_propagates_after_rollback_and_nack(db_conn, channel, broker, tables, row_count):
    def buggy(conn, payload):
        with conn.cursor() as cur:
            load_data.insert_rows(cur, [make_record(0)])
        raise KeyError("bug")

    with pytest.raises(KeyError, match="bug"):
        consumer.process_message(
            db_conn, channel, deliver_method(4), _body("recompute_analytics"), {"recompute_analytics": buggy}
        )

    assert broker.nacks == [(4, False)] and broker.acks == []
    assert row_count() == 0  # rolled back before the error escaped


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"not json", "body is not JSON"),
        (b"\xff\xfe", "body is not JSON"),
        (b"[1, 2]", "body is not a JSON object: list"),
        (b'"recompute_analytics"', "body is not a JSON object: str"),
        (_body("drop_everything"), "unknown task kind 'drop_everything'"),
        (b'{"payload": {}}', "unknown task kind None"),
        (b'{"kind": "recompute_analytics", "payload": [1]}', "payload is not a JSON object: list"),
    ],
)
def test_bad_messages_are_nacked_without_requeue(db_conn, channel, broker, tables, caplog, body, reason):
    consumer.process_message(db_conn, channel, deliver_method(9), body)
    consumer.process_message(db_conn, channel, deliver_method(10), _body("recompute_analytics"))

    assert broker.nacks == [(9, False)]
    assert broker.acks == [10]
    assert f"Rejected message 9: {reason}" in caplog.text


def test_missing_payload_means_no_parameters(db_conn, channel, broker, tables):
    consumer.process_message(db_conn, channel, deliver_method(1), b'{"kind": "recompute_analytics"}')

    assert broker.acks == [1]


def test_redelivered_message_is_processed_normally(db_conn, channel, broker, tables):
    consumer.process_message(db_conn, channel, deliver_method(5, redelivered=True), _body("recompute_analytics"))

    assert broker.acks == [5] and broker.nacks == []
    assert _count(db_conn, "analysis_summary") == 1


def test_on_message_uses_the_real_task_map(db_conn, channel, broker, tables):
    consumer.on_message(db_conn, channel, deliver_method(1), None, _body("recompute_analytics"))

    assert broker.acks == [1]
    assert _count(db_conn, "analysis_summary") == 1


# ---------------------------------------------------------------------------
# Startup: database first, then the queue
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_json(tmp_path, monkeypatch):
    path = tmp_path / "seed.json"
    path.write_text(json.dumps([make_record(i, URL=result_url(100 + i)) for i in range(3)]))
    monkeypatch.setenv("SEED_JSON", str(path))
    return path


def test_main_prepares_database_then_consumes(broker, seed_json, db_conn, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="consumer")
    state_at_consume = []
    monkeypatch.setenv("RABBITMQ_URL", "amqp://worker:pw@broker.test:5672/%2F")
    broker.deliver({"kind": "recompute_analytics", "payload": {}})
    broker.deliver(b"garbage")

    broker.consume_hook = lambda: state_at_consume.append(
        (_count(db_conn, "applicants"), _count(db_conn, "analysis_summary"))
    )

    consumer.main()

    assert state_at_consume == [(3, 1)]  # seeded and summarised before consuming started
    assert "Schema ready: applicants, ingestion_watermarks, analysis_summary" in caplog.text
    assert "Seeded applicants: {'inserted': 3, 'skipped': 0, 'failed': 0, 'watermark': 102}" in caplog.text
    assert "Computed the initial analysis summary (Q1 = 3)" in caplog.text
    [conn] = broker.connections
    assert conn.parameters.heartbeat == 600
    assert conn.parameters.host == "broker.test"
    assert broker.declarations == [
        ("exchange", {"exchange": "tasks", "exchange_type": "direct", "durable": True}),
        ("queue", {"queue": "tasks_q", "durable": True}),
        ("bind", {"queue": "tasks_q", "exchange": "tasks", "routing_key": "tasks"}),
    ]
    assert broker.qos == [{"prefetch_count": 1}]
    assert [c["queue"] for c in broker.consumers] == ["tasks_q"]
    assert broker.acks == [1] and broker.nacks == [(2, False)]
    assert conn.is_open is False  # closed when consuming ended
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT last_seen FROM ingestion_watermarks")
        assert cur.fetchone()[0] == 102


def test_restart_skips_seed_and_keeps_summary(broker, db_conn, seed, seed_json, refresh_summary, caplog):
    caplog.set_level(logging.INFO, logger="consumer")
    seed([make_record(0)])
    refresh_summary()
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT computed_at FROM analysis_summary")
        before = cur.fetchone()[0]

    consumer.prepare_database(db_conn)

    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT computed_at FROM analysis_summary")
        assert cur.fetchone()[0] == before
    assert _count(db_conn, "applicants") == 1  # the seed file was not loaded
    assert "applicants already has rows: seed skipped" in caplog.text
    assert "Analysis summary already present: not recomputed" in caplog.text
    assert "Seeded applicants" not in caplog.text


def test_script_entry_point_runs_main(broker, seed_json):
    runpy.run_path(CONSUMER_PY, run_name="__main__")

    assert broker.qos == [{"prefetch_count": 1}]


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda mp, broker: mp.delenv("DATABASE_URL"), "Worker stopped: DATABASE_URL is not set (see .env.example)."),
        (lambda mp, broker: mp.delenv("RABBITMQ_URL"), "Worker stopped: RABBITMQ_URL is not set (see .env.example)."),
        (lambda mp, broker: mp.delenv("SEED_JSON"),
         "Worker stopped: applicants is empty and SEED_JSON is not set: nothing to seed it from"),
        (lambda mp, broker: setattr(broker, "connect_error", AMQPConnectionError("connection refused")),
         "Worker stopped: connection refused"),
    ],
    ids=["no-database-url", "no-rabbitmq-url", "no-seed", "broker-down"],
)
def test_main_exits_1_with_a_message(broker, seed_json, monkeypatch, capsys, setup, message):
    setup(monkeypatch, broker)

    with pytest.raises(SystemExit) as excinfo:
        consumer.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().out == message + "\n"
    assert broker.consumers == []


def test_main_exits_1_when_database_unreachable(broker, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/unreachable_test")

    with pytest.raises(SystemExit) as excinfo:
        consumer.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().out.startswith("Worker stopped: ")
    assert broker.connections == []


def test_broker_setup_failure_closes_the_connection(broker, monkeypatch):
    broker.channel_error = AMQPConnectionError("channel refused")

    with pytest.raises(AMQPConnectionError):
        consumer.open_channel()

    [conn] = broker.connections
    assert conn.is_open is False and conn.close_calls == 1
