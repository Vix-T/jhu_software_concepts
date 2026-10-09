"""Shared fixtures for the Module 6 test suite.

Every test runs against the database named by TEST_DATABASE_URL (from the
environment, else module_6/.env), never the development database. Two
guards enforce that:

- pytest refuses to start (pytest.UsageError) unless TEST_DATABASE_URL is
  set and its database name ends in "_test".
- For the whole run, DATABASE_URL (what every database client reads) is
  overridden with the test URL, and RABBITMQ_URL with an address that can't
  resolve, so nothing can reach a real broker unless a test fakes
  pika.BlockingConnection (the `broker` fixture). The original values are
  restored when the run ends.

No code under test reads a .env file; TEST_DATABASE_URL is the only value
the suite takes from module_6/.env, read below with dotenv_values().

The applicants table is created once per session with the application's
own CREATE_TABLE_SQL and truncated before every test, so each test starts
from an empty table; ingestion_watermarks and analysis_summary are dropped
before every test (the `tables` fixture recreates them).
"""

import json
import os
import secrets
from urllib.parse import quote, urlsplit, urlunsplit

import pika
import psycopg2
import pytest
from bs4 import BeautifulSoup
from dotenv import dotenv_values
from pika.exceptions import ConnectionWrongStateError
from psycopg2 import sql
from selenium.common.exceptions import NoSuchElementException

import load_data
from app import create_app
from etl import query_data

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# A broker address that never resolves (.invalid is reserved, RFC 2606).
TEST_RABBITMQ_URL = "amqp://guest:guest@rabbitmq.invalid:5672/%2F"

_UNSET = object()
_saved_env = {}


def database_name(url):
    """The database name in a postgresql:// URL."""
    return urlsplit(url).path.lstrip("/")


def _read_test_database_url():
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ENV_FILE).get("TEST_DATABASE_URL")
    if not url:
        raise pytest.UsageError(
            "TEST_DATABASE_URL is not set. Define it in the environment or in module_6/.env as "
            "postgresql://USER[:PASSWORD]@HOST:PORT/<name>_test"
        )
    database = database_name(url)
    if not database.endswith("_test"):
        raise pytest.UsageError(
            f"Refusing to run: TEST_DATABASE_URL points at database {database!r}, "
            "whose name does not end in '_test'."
        )
    return url


# Roles are cluster-wide, so the tests use their own app role name and a
# throwaway password: setup_roles can never touch the dev gradcafe_app role.
TEST_APP_ROLE = "gradcafe_app_test"


def pytest_configure(config):
    test_url = _read_test_database_url()
    config.test_database_url = test_url
    test_env = {
        "DATABASE_URL": test_url,
        "RABBITMQ_URL": TEST_RABBITMQ_URL,
        "APP_DB_USER": TEST_APP_ROLE,
        "APP_DB_PASSWORD": secrets.token_urlsafe(16),
    }
    for name in test_env:
        _saved_env[name] = os.environ.get(name, _UNSET)
    os.environ.update(test_env)


def pytest_unconfigure(config):
    for name, value in _saved_env.items():
        if value is _UNSET:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_database_url(pytestconfig):
    return pytestconfig.test_database_url


@pytest.fixture(scope="session")
def _applicants_table(test_database_url):
    conn = psycopg2.connect(test_database_url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(load_data.CREATE_TABLE_SQL)
    finally:
        conn.close()


@pytest.fixture
def db_conn(test_database_url, _applicants_table):
    conn = psycopg2.connect(test_database_url)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _empty_applicants(db_conn):
    # Recreate first in case an earlier test dropped the table on purpose.
    # The watermark and summary tables are dropped, so every test starts
    # without them, as on a database initialize_database() hasn't touched.
    with db_conn, db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS ingestion_watermarks, analysis_summary")
        cur.execute(load_data.CREATE_TABLE_SQL)
        cur.execute("TRUNCATE applicants RESTART IDENTITY")


@pytest.fixture
def row_count(db_conn):
    def count():
        with db_conn, db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM applicants")
            return cur.fetchone()[0]

    return count


@pytest.fixture
def fetch_rows(db_conn):
    """Return every applicants row as a dict, ordered by p_id."""

    def fetch():
        with db_conn, db_conn.cursor() as cur:
            cur.execute("SELECT * FROM applicants ORDER BY p_id")
            columns = [c.name for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    return fetch


@pytest.fixture
def seed(db_conn):
    """Insert scraper-shaped records through the real load_rows()."""

    def insert(records):
        return load_data.load_rows(records, db_conn)

    return insert


# ---------------------------------------------------------------------------
# Fake data, fakes and spies
# ---------------------------------------------------------------------------


def make_record(i, **overrides):
    """One record shaped like scrape._parse_entry()'s output, with every scraped field filled."""
    record = {
        "Program Name": "Computer Science",
        "University": f"Test University {i}",
        "Comments": f"synthetic entry {i}",
        "Date Added": "Sep 08, 2026",
        "URL": f"https://www.thegradcafe.com/result/test-{i}",
        "Applicant Status": "Accepted",
        "Acceptance Date": "08 Sep",
        "Rejection Date": None,
        "Semester and Year": "Fall 2026",
        "International/American": "American",
        "GRE Score": "165",
        "GRE V Score": "160",
        "GRE AW Score": "4.5",
        "Masters or PhD": "PhD",
        "GPA": "3.80",
        "raw_entry_text": f"Test University {i} | Computer Science PhD | Sep 08, 2026 | Accepted",
    }
    record.update(overrides)
    return record


def make_records(n, start=0, **overrides):
    return [make_record(i, **overrides) for i in range(start, start + n)]


class Spy:
    """Records each call's positional args; passes through to `fn` when given."""

    def __init__(self, fn=None):
        self.fn = fn
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if self.fn is not None:
            return self.fn(*args)
        return None


# Synthetic Grad Cafe pages (tests/fixtures/) keyed by the URL the fake driver serves them at.
SURVEY_URL = "https://www.thegradcafe.com/survey/"
PAGE_2_URL = "https://www.thegradcafe.com/survey/?page=2"
SELF_LINK_URL = "https://www.thegradcafe.com/survey/?page=self"


def load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


PULL_PAGE_URLS = [SURVEY_URL, PAGE_2_URL, "https://www.thegradcafe.com/survey/?page=3"]


def result_url(result_id):
    return f"https://www.thegradcafe.com/result/{result_id}"


def pull_pages():
    """Newest-first fixture pages: page 1 (2 new, 3 known), page 2 (all known), page 3 (never reached)."""
    return {
        url: load_fixture(f"pull_page_{n}.html") for n, url in enumerate(PULL_PAGE_URLS, start=1)
    }


def survey_pages():
    return {SURVEY_URL: load_fixture("page_1.html"), PAGE_2_URL: load_fixture("page_2.html")}


class FakeDriver:
    """Stands in for a Selenium WebDriver: serves fixture HTML by URL, no browser or network.

    find_element() answers CSS lookups against the current page and raises
    Selenium's NoSuchElementException when nothing matches, so scrape.py's
    real WebDriverWait logic runs unchanged against it.
    """

    def __init__(self, pages):
        self.pages = pages
        self.visited = []
        self.page_source = ""

    def get(self, url):
        self.visited.append(url)
        self.page_source = self.pages[url]

    def find_element(self, by, value):
        element = BeautifulSoup(self.page_source, "html.parser").select_one(value)
        if element is None:
            raise NoSuchElementException(f"no element matches {value!r}")
        return element


class DriverFactory:
    """driver_factory for scrape.py: hands out one FakeDriver per call and records the calls."""

    def __init__(self, pages):
        self.drivers = []
        self.pages = pages

    def __call__(self):
        driver = FakeDriver(self.pages)
        self.drivers.append(driver)
        return driver

    @property
    def visited(self):
        return [url for driver in self.drivers for url in driver.visited]


@pytest.fixture
def fake_records():
    return make_records(3)


@pytest.fixture
def real_loader(test_database_url):
    def loader(records):
        return load_data.load_into_database(records, test_database_url)

    return loader


@pytest.fixture
def tables(db_conn):
    """All three tables, as the worker's initialize_database() leaves them (applicants empty)."""
    with db_conn, db_conn.cursor() as cur:
        load_data.create_tables(cur)


@pytest.fixture
def refresh_summary(db_conn, tables):
    """Recompute and store the analysis summary from the current rows, as the worker does."""

    def refresh():
        with db_conn, db_conn.cursor() as cur:
            return query_data.refresh_summary(cur)

    return refresh


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@pytest.fixture
def make_app(test_database_url):
    """Build a web app on the test database (or on `database_url`)."""

    def build(database_url=test_database_url):
        return create_app({"DATABASE_URL": database_url, "TESTING": True})

    return build


@pytest.fixture
def app(make_app, tables):
    return make_app()


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# RabbitMQ: a fake pika.BlockingConnection (the only part of pika replaced)
# ---------------------------------------------------------------------------


class FakeBroker:
    """Records what publisher.py does through pika, and can be told to fail.

    connect_error / channel_error / publish_error: an exception to raise from
    pika.BlockingConnection(), connection.channel() or channel.basic_publish().
    publish_drops_connection: the broker also closes the connection when the
    publish fails (so the publisher must not close it a second time).
    """

    def __init__(self):
        self.connections = []
        self.declarations = []
        self.confirm_calls = 0
        self.published = []
        self.connect_error = None
        self.channel_error = None
        self.publish_error = None
        self.publish_drops_connection = False
        # Consumer side: what the worker asks for, and messages to deliver to it.
        self.qos = []
        self.consumers = []
        self.deliveries = []  # (body, redelivered) pairs; start_consuming() delivers them in order
        self.acks = []
        self.nacks = []
        self.ack_hook = None  # called with the delivery tag just before an ack is recorded
        self.consume_hook = None  # called when the worker registers its consumer

    def deliver(self, body, redelivered=False):
        """Queue a message body (dict -> JSON) for the next start_consuming()."""
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        self.deliveries.append((body, redelivered))

    def bodies(self):
        return [json.loads(message["body"]) for message in self.published]


class FakeChannel:
    def __init__(self, broker, connection):
        self.broker = broker
        self.connection = connection

    def exchange_declare(self, **kwargs):
        self.broker.declarations.append(("exchange", kwargs))

    def queue_declare(self, **kwargs):
        self.broker.declarations.append(("queue", kwargs))

    def queue_bind(self, **kwargs):
        self.broker.declarations.append(("bind", kwargs))

    def confirm_delivery(self):
        self.broker.confirm_calls += 1

    def basic_qos(self, **kwargs):
        self.broker.qos.append(kwargs)

    def basic_consume(self, **kwargs):
        if self.broker.consume_hook is not None:
            self.broker.consume_hook()
        self.broker.consumers.append(kwargs)

    def start_consuming(self):
        """Deliver every queued message to the registered callback, then return."""
        [consumer] = self.broker.consumers
        for tag, (body, redelivered) in enumerate(self.broker.deliveries, start=1):
            consumer["on_message_callback"](self, deliver_method(tag, redelivered), pika.BasicProperties(), body)
        self.broker.deliveries = []

    def basic_ack(self, delivery_tag):
        if self.broker.ack_hook is not None:
            self.broker.ack_hook(delivery_tag)
        self.broker.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue=True):
        self.broker.nacks.append((delivery_tag, requeue))

    def basic_publish(self, **kwargs):
        if self.broker.publish_error is not None:
            if self.broker.publish_drops_connection:
                self.connection.is_open = False
            raise self.broker.publish_error
        self.broker.published.append(kwargs)


class FakeConnection:
    def __init__(self, broker, parameters):
        if broker.connect_error is not None:
            raise broker.connect_error
        self.broker = broker
        self.parameters = parameters
        self.is_open = True
        self.close_calls = 0
        broker.connections.append(self)

    def channel(self):
        if self.broker.channel_error is not None:
            raise self.broker.channel_error
        return FakeChannel(self.broker, self)

    def close(self):
        if not self.is_open:
            raise ConnectionWrongStateError("connection already closed")
        self.close_calls += 1
        self.is_open = False


def deliver_method(tag, redelivered=False):
    """The real pika method frame a consumer callback receives."""
    return pika.spec.Basic.Deliver(
        consumer_tag="test", delivery_tag=tag, redelivered=redelivered, exchange="tasks", routing_key="tasks"
    )


@pytest.fixture
def broker(monkeypatch):
    fake = FakeBroker()
    monkeypatch.setattr(pika, "BlockingConnection", lambda parameters: FakeConnection(fake, parameters))
    return fake


# ---------------------------------------------------------------------------
# Test-only database roles
# ---------------------------------------------------------------------------


def drop_role(conn, role):
    """Drop `role` and its privileges in the test database (a no-op if it doesn't exist)."""
    with conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        if cur.fetchone() is not None:
            cur.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            cur.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def role_url(test_database_url, role, password):
    """TEST_DATABASE_URL, logging in as `role` (with `password`) instead."""
    parts = urlsplit(test_database_url)
    host = parts.netloc.rpartition("@")[2]
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{host}"
    return urlunsplit(parts._replace(netloc=netloc))


@pytest.fixture
def channel(broker):
    """A channel on the fake broker, for driving consumer.process_message() directly."""
    return FakeChannel(broker, FakeConnection(broker, None))
