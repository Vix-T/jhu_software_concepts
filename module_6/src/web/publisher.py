"""Publish tasks for the worker on RabbitMQ.

The web service never scrapes or writes applicant data: each button
publishes one task message and returns. The worker consumes the queue.

Topology (declared identically by the worker, all durable so it survives a
broker restart): direct exchange "tasks" -> queue "tasks_q", bound with
routing key "tasks".

Message body: compact JSON {"kind": ..., "ts": ..., "payload": {...}},
where ts is the UTC publish time in ISO 8601. Messages are persistent
(delivery_mode=2), published with mandatory=True on a channel with
publisher confirms, so an unroutable or broker-rejected message raises
instead of being dropped silently.
"""

import json
from datetime import datetime, timezone

import pika

from app.config import require_env

RABBITMQ_URL_ENV = "RABBITMQ_URL"
EXCHANGE = "tasks"
QUEUE = "tasks_q"
ROUTING_KEY = "tasks"
PERSISTENT = 2  # AMQP delivery_mode: the broker stores the message on disk


def _close(conn):
    """Close `conn` unless the broker already closed it (closing twice raises)."""
    if conn.is_open:
        conn.close()


def _open_channel():
    """Connect to $RABBITMQ_URL and declare the durable exchange, queue and binding.

    Returns (connection, channel), with publisher confirms enabled on the
    channel. If anything fails after the connection opens, the connection is
    closed before the error propagates.

    Raises:
        app.config.ConfigError: RABBITMQ_URL is not set.
        pika.exceptions.AMQPError: the broker is unreachable or refuses a declaration.
    """
    conn = pika.BlockingConnection(pika.URLParameters(require_env(RABBITMQ_URL_ENV)))
    ready = False
    try:
        ch = conn.channel()
        ch.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
        ch.queue_declare(queue=QUEUE, durable=True)
        ch.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=ROUTING_KEY)
        ch.confirm_delivery()
        ready = True
    finally:
        if not ready:
            _close(conn)
    return conn, ch


def publish_task(kind, payload=None, headers=None):
    """Publish one task message for the worker; return the JSON body that was sent.

    payload is the task's parameters (None means {}); headers are optional
    AMQP message headers. The connection is closed whether or not the
    publish succeeds; errors propagate to the caller (ConfigError or a
    pika.exceptions.AMQPError such as UnroutableError or NackError).
    """
    body = json.dumps(
        {"kind": kind, "ts": datetime.now(timezone.utc).isoformat(), "payload": payload or {}},
        separators=(",", ":"),
    )
    conn, ch = _open_channel()
    try:
        ch.basic_publish(
            exchange=EXCHANGE,
            routing_key=ROUTING_KEY,
            body=body,
            properties=pika.BasicProperties(
                content_type="application/json", delivery_mode=PERSISTENT, headers=headers
            ),
            mandatory=True,
        )
    finally:
        _close(conn)
    return body
