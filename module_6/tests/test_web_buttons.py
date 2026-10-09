"""Pull Data and Update Analysis: each queues one task for the worker and answers 202, or 503.

The routes run the real publisher.publish_task(); only pika.BlockingConnection
is faked (conftest `broker`). Nothing is scraped, computed or written by the
web service itself.
"""

import pytest
from pika.exceptions import AMQPConnectionError, NackError, UnroutableError

pytestmark = pytest.mark.buttons

BUTTONS = [("/pull-data", "scrape_new_data"), ("/update-analysis", "recompute_analytics")]


@pytest.mark.parametrize(("path", "task"), BUTTONS)
def test_button_queues_its_task(client, broker, path, task):
    response = client.post(path)

    assert response.status_code == 202
    assert response.get_json() == {"status": "queued", "task": task}
    [body] = broker.bodies()
    assert body["kind"] == task
    assert body["payload"] == {}


@pytest.mark.parametrize(("path", "task"), BUTTONS)
def test_button_writes_nothing_to_the_database(client, broker, row_count, db_conn, path, task):
    client.post(path)

    assert row_count() == 0
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM analysis_summary")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM ingestion_watermarks")
        assert cur.fetchone()[0] == 0


def test_each_click_queues_another_task(client, broker):
    assert client.post("/pull-data").status_code == 202
    assert client.post("/pull-data").status_code == 202
    assert client.post("/update-analysis").status_code == 202

    assert [body["kind"] for body in broker.bodies()] == [
        "scrape_new_data", "scrape_new_data", "recompute_analytics",
    ]


@pytest.mark.parametrize(("path", "task"), BUTTONS)
@pytest.mark.parametrize(
    "error",
    [AMQPConnectionError("connection refused"), UnroutableError([]), NackError([])],
    ids=["unreachable", "unroutable", "nacked"],
)
def test_publish_failure_is_503(client, broker, caplog, path, task, error):
    if isinstance(error, AMQPConnectionError):
        broker.connect_error = error
    else:
        broker.publish_error = error

    response = client.post(path)

    assert response.status_code == 503
    assert response.get_json() == {"status": "error", "task": task, "error": "message queue unavailable"}
    assert f"Queueing {task} failed" in caplog.text


@pytest.mark.parametrize(("path", "task"), BUTTONS)
def test_missing_rabbitmq_url_is_503(client, broker, monkeypatch, caplog, path, task):
    monkeypatch.delenv("RABBITMQ_URL")

    response = client.post(path)

    assert response.status_code == 503
    assert response.get_json() == {"status": "error", "task": task, "error": "RABBITMQ_URL is not set"}
    assert broker.connections == []
    assert f"Queueing {task} failed: RABBITMQ_URL is not set" in caplog.text


@pytest.mark.parametrize("path", ["/pull-data", "/update-analysis"])
def test_buttons_only_accept_post(client, broker, path):
    assert client.get(path).status_code == 405
    assert broker.published == []


def test_unexpected_publish_error_is_not_swallowed(client, broker):
    broker.publish_error = RuntimeError("bug in the publisher")

    with pytest.raises(RuntimeError, match="bug in the publisher"):  # TESTING propagates it
        client.post("/pull-data")
