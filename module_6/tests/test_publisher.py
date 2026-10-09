"""publisher.py: topology declarations, the message it sends, and cleanup on failure.

Only pika.BlockingConnection is faked (conftest `broker`); pika.URLParameters
and pika.BasicProperties are the real classes.
"""

import json
from datetime import datetime, timedelta, timezone

import pika
import pytest
from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker, UnroutableError

import publisher
from app.config import ConfigError

pytestmark = pytest.mark.buttons


def test_declares_durable_topology_with_confirms(broker):
    publisher.publish_task("recompute_analytics")

    assert broker.declarations == [
        ("exchange", {"exchange": "tasks", "exchange_type": "direct", "durable": True}),
        ("queue", {"queue": "tasks_q", "durable": True}),
        ("bind", {"queue": "tasks_q", "exchange": "tasks", "routing_key": "tasks"}),
    ]
    assert broker.confirm_calls == 1


def test_connects_to_rabbitmq_url(broker, monkeypatch):
    monkeypatch.setenv("RABBITMQ_URL", "amqp://worker:s3cret@broker.test:5673/%2F")

    publisher.publish_task("recompute_analytics")

    [conn] = broker.connections
    assert isinstance(conn.parameters, pika.URLParameters)
    assert (conn.parameters.host, conn.parameters.port) == ("broker.test", 5673)
    assert conn.parameters.credentials.username == "worker"


def test_publishes_persistent_compact_json(broker):
    before = datetime.now(timezone.utc)

    body = publisher.publish_task("scrape_new_data", {"since": 1020478}, headers={"source": "web"})

    [message] = broker.published
    assert (message["exchange"], message["routing_key"]) == ("tasks", "tasks")
    assert message["mandatory"] is True
    assert message["body"] == body
    assert ", " not in body and ": " not in body  # compact separators
    sent = json.loads(body)
    assert list(sent) == ["kind", "ts", "payload"]
    assert sent["kind"] == "scrape_new_data"
    assert sent["payload"] == {"since": 1020478}
    ts = datetime.fromisoformat(sent["ts"])
    assert ts.utcoffset() == timedelta(0)  # timezone-aware, UTC
    assert before <= ts <= datetime.now(timezone.utc)
    properties = message["properties"]
    assert isinstance(properties, pika.BasicProperties)
    assert properties.delivery_mode == 2
    assert properties.content_type == "application/json"
    assert properties.headers == {"source": "web"}


def test_payload_defaults_to_empty_object(broker):
    publisher.publish_task("recompute_analytics")

    assert broker.bodies()[0]["payload"] == {}
    assert broker.published[0]["properties"].headers is None


def test_connection_is_closed_after_publishing(broker):
    publisher.publish_task("recompute_analytics")

    [conn] = broker.connections
    assert conn.is_open is False and conn.close_calls == 1


def test_publish_error_propagates_and_connection_is_closed(broker):
    broker.publish_error = UnroutableError([])

    with pytest.raises(UnroutableError):
        publisher.publish_task("scrape_new_data")

    [conn] = broker.connections
    assert conn.is_open is False and conn.close_calls == 1
    assert broker.published == []


def test_setup_error_propagates_and_connection_is_closed(broker):
    broker.channel_error = ChannelClosedByBroker(406, "PRECONDITION_FAILED - inequivalent arg 'durable'")

    with pytest.raises(ChannelClosedByBroker):
        publisher.publish_task("scrape_new_data")

    [conn] = broker.connections
    assert conn.is_open is False and conn.close_calls == 1


def test_connection_closed_by_broker_is_not_closed_again(broker):
    broker.publish_error = UnroutableError([])
    broker.publish_drops_connection = True

    with pytest.raises(UnroutableError):  # not masked by a ConnectionWrongStateError from close()
        publisher.publish_task("scrape_new_data")

    [conn] = broker.connections
    assert conn.is_open is False and conn.close_calls == 0


def test_unreachable_broker_propagates(broker):
    broker.connect_error = AMQPConnectionError("connection refused")

    with pytest.raises(AMQPConnectionError):
        publisher.publish_task("scrape_new_data")

    assert broker.connections == []


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_rabbitmq_url_is_config_error(broker, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("RABBITMQ_URL")
    else:
        monkeypatch.setenv("RABBITMQ_URL", value)

    with pytest.raises(ConfigError, match="RABBITMQ_URL is not set"):
        publisher.publish_task("scrape_new_data")

    assert broker.connections == []
