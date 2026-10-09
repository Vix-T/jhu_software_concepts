"""The worker: consume task messages from RabbitMQ and run them against PostgreSQL.

Start it with `python consumer.py` (from src/worker, with src/db on the
Python path for load_data and sql_utils). At startup it:

1. connects to $DATABASE_URL and runs load_data.initialize_database()
   (creates the tables; seeds an empty applicants table from $SEED_JSON),
2. computes the analysis summary if there is none yet,
3. connects to $RABBITMQ_URL (heartbeat 600 s, so a long scrape doesn't
   drop the connection), declares the same durable topology as
   publisher.py, sets prefetch_count=1, and consumes "tasks_q".

Each message is {"kind": ..., "ts": ..., "payload": {...}} and runs in one
database transaction: the task's handler, then COMMIT, then basic_ack. A
message that isn't acked -- malformed JSON, a body that isn't an object, an
unknown kind, a bad payload, or a handler failure -- is rolled back and
nacked without requeue (in `finally`). Expected failures (EXPECTED_ERRORS)
are logged and the worker carries on with the next message; anything else
is a bug and propagates after that cleanup, stopping the worker.
"""

import functools
import json
import logging
import os
import sys

import pika
import psycopg2
from pika.exceptions import AMQPError
from psycopg2 import sql
from selenium.common.exceptions import WebDriverException

import load_data
from etl import incremental_scraper, query_data
from etl.incremental_scraper import PullPreconditionError, ScrapeRetriesExhausted
from sql_utils import SINGLE_ROW, clamp_limit

logger = logging.getLogger(__name__)

RABBITMQ_URL_ENV = "RABBITMQ_URL"
HEARTBEAT_SECONDS = 600
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# The topology publisher.py declares; both sides must declare it identically.
EXCHANGE = "tasks"
QUEUE = "tasks_q"
ROUTING_KEY = "tasks"

# Handler failures the worker reports and survives: a database error, the
# browser failing or not being attached, or a payload it can't use.
EXPECTED_ERRORS = (
    psycopg2.Error,
    WebDriverException,
    PullPreconditionError,
    ScrapeRetriesExhausted,
    ValueError,
)
# Failures that stop the worker with a message (exit status 1) instead of a traceback.
STARTUP_ERRORS = (load_data.ConfigError, load_data.SeedError, psycopg2.Error, AMQPError)

SUMMARY_EXISTS_SQL = sql.SQL("SELECT 1 FROM {} LIMIT %s").format(load_data.SUMMARY)


class BadMessage(ValueError):
    """A message body the worker can't run: not JSON, not an object, or an unknown kind."""


# ---------------------------------------------------------------------------
# Task handlers: run inside the message's transaction; the caller commits.
# ---------------------------------------------------------------------------


def _since(payload):
    """payload["since"] as a result ID, or None; ValueError if it isn't a non-negative integer."""
    since = payload.get("since")
    if since is None:
        return None
    if isinstance(since, bool) or not isinstance(since, int) or since < 0:
        raise ValueError(f"payload 'since' must be a non-negative integer, got {since!r}")
    return since


def handle_scrape_new_data(conn, payload, browser=None):
    """Scrape entries newer than the watermark, store them, advance the watermark, recompute.

    The starting point is payload["since"] if given, else the stored
    watermark (row-locked with SELECT ... FOR UPDATE for the whole
    transaction). New rows go in with ON CONFLICT (url) DO NOTHING; the
    watermark advances (never backwards) to the highest result ID that
    loaded; and the analysis summary is recomputed -- all in conn's open
    transaction. browser is an incremental_scraper.BrowserSettings
    (default: attach to the real Chrome session).
    """
    since = _since(payload)
    with conn.cursor() as cur:
        stored = load_data.locked_watermark(cur)
        start = stored if since is None else since
        records = incremental_scraper.scrape_new_entries(start, browser=browser)
        inserted, skipped, failed = load_data.insert_rows(cur, records)
        newest = load_data.highest_result_id(records, failed)
        mark = newest if newest is not None else stored
        if mark is not None:  # also stamps updated_at: "Data last updated"
            load_data.advance_watermark(cur, mark)
        query_data.refresh_summary(cur)
    logger.info(
        "Scrape from watermark %s: %d scraped, %d inserted, %d already stored, %d failed",
        start, len(records), inserted, skipped, len(failed),
    )
    return {"scraped": len(records), "inserted": inserted, "skipped": skipped,
            "failed": len(failed), "watermark": mark}


def handle_recompute_analytics(conn, payload):
    """Recompute the analysis summary from the current rows. Takes no payload parameters."""
    if payload:
        raise ValueError(f"recompute_analytics takes no payload parameters, got {sorted(payload)}")
    with conn.cursor() as cur:
        return query_data.refresh_summary(cur)


TASKS = {
    "scrape_new_data": handle_scrape_new_data,
    "recompute_analytics": handle_recompute_analytics,
}


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def parse_message(body, tasks=None):
    """(kind, payload) from a message body; BadMessage if the worker can't run it."""
    tasks = TASKS if tasks is None else tasks
    try:
        message = json.loads(body)
    except ValueError as exc:  # JSONDecodeError, or bytes that aren't UTF-8
        raise BadMessage(f"body is not JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise BadMessage(f"body is not a JSON object: {type(message).__name__}")
    kind = message.get("kind")
    if kind not in tasks:
        raise BadMessage(f"unknown task kind {kind!r}")
    payload = message.get("payload") or {}
    if not isinstance(payload, dict):
        raise BadMessage(f"payload is not a JSON object: {type(payload).__name__}")
    return kind, payload


def process_message(db_conn, channel, method, body, tasks=None):
    """Run one delivery in one transaction: handler, COMMIT, then ack.

    If the message isn't acked, `finally` rolls the transaction back and
    nacks it without requeue -- whether it was rejected (BadMessage), its
    handler failed in an expected way (EXPECTED_ERRORS, logged and
    survived), or something unexpected was raised (propagates afterwards).
    Redelivered messages are processed like any other.
    """
    tasks = TASKS if tasks is None else tasks
    tag = method.delivery_tag
    acked = False
    try:
        try:
            kind, payload = parse_message(body, tasks)
        except BadMessage as exc:
            logger.error("Rejected message %s: %s", tag, exc)
            return
        try:
            tasks[kind](db_conn, payload)
            db_conn.commit()
        except EXPECTED_ERRORS as exc:
            logger.error("Task %s (message %s) failed: %s: %s", kind, tag, type(exc).__name__, exc)
            return
        channel.basic_ack(delivery_tag=tag)
        acked = True
        logger.info("Task %s (message %s) done", kind, tag)
    finally:
        if not acked:
            db_conn.rollback()
            channel.basic_nack(delivery_tag=tag, requeue=False)


def on_message(db_conn, channel, method, _properties, body):
    """pika on_message_callback (bound to db_conn with functools.partial)."""
    process_message(db_conn, channel, method, body)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def prepare_database(db_conn):
    """Create/seed the tables, then compute the summary if there is none yet (commits)."""
    seeded = load_data.initialize_database(db_conn)
    logger.info("Schema ready: applicants, ingestion_watermarks, analysis_summary")
    if seeded is None:
        logger.info("applicants already has rows: seed skipped")
    else:
        logger.info("Seeded applicants: %s", seeded)
    with db_conn:
        with db_conn.cursor() as cur:
            cur.execute(SUMMARY_EXISTS_SQL, [clamp_limit(SINGLE_ROW)])
            if cur.fetchone() is None:
                summary = query_data.refresh_summary(cur)
                logger.info("Computed the initial analysis summary (Q1 = %s)", summary["q1_count"])
            else:
                logger.info("Analysis summary already present: not recomputed")


def broker_parameters():
    """pika connection parameters for $RABBITMQ_URL, with the worker's long heartbeat."""
    url = os.environ.get(RABBITMQ_URL_ENV, "").strip()
    if not url:
        raise load_data.ConfigError(f"{RABBITMQ_URL_ENV} is not set (see .env.example).")
    parameters = pika.URLParameters(url)
    parameters.heartbeat = HEARTBEAT_SECONDS
    return parameters


def open_channel():
    """Connect, declare the durable topology, and allow one unacked message at a time."""
    connection = pika.BlockingConnection(broker_parameters())
    ready = False
    try:
        channel = connection.channel()
        channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
        channel.queue_declare(queue=QUEUE, durable=True)
        channel.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=ROUTING_KEY)
        channel.basic_qos(prefetch_count=1)
        ready = True
    finally:
        if not ready and connection.is_open:
            connection.close()
    return connection, channel


def run(db_conn):
    """Prepare the database, then consume until the broker connection ends."""
    prepare_database(db_conn)
    connection, channel = open_channel()
    try:
        callback = functools.partial(on_message, db_conn)
        channel.basic_consume(queue=QUEUE, on_message_callback=callback)
        logger.info("Waiting for tasks on %s", QUEUE)
        channel.start_consuming()
    finally:
        if connection.is_open:
            connection.close()


def main():
    """Entry point: run the worker; exit 1 with a message on a setup or connection failure."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    try:
        db_conn = load_data.connect()
    except (load_data.ConfigError, psycopg2.OperationalError) as exc:
        print(f"Worker stopped: {str(exc).strip()}")
        sys.exit(1)
    try:
        run(db_conn)
    except STARTUP_ERRORS as exc:
        print(f"Worker stopped: {str(exc).strip() or type(exc).__name__}")
        sys.exit(1)
    finally:
        db_conn.close()


if __name__ == "__main__":
    main()
