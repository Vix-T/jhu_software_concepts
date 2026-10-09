"""Shared fixtures for the Module 6 test suite.

Every test runs against the database named by TEST_DATABASE_URL (from the
environment, else module_6/.env), never the development database. Two
guards enforce that:

- pytest refuses to start (pytest.UsageError) unless TEST_DATABASE_URL is
  set and its database name ends in "_test".
- For the whole run, DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD are
  overridden with the parts of the test URL, so any code path that falls
  back to config.get_db_url() also lands on the test database (python-dotenv
  never overrides a variable already set in the environment). The original
  values are restored when the run ends.

The developer's own module_6/.env never reaches the code under test:
config.load_dotenv skips that one file for the whole run (any other path,
such as a test's temporary .env, loads normally). Otherwise any keys in it
beyond the pinned ones would leak into os.environ and could change a
test's outcome. TEST_DATABASE_URL is the only value the suite takes from it,
read above with dotenv_values().

The applicants table is created once per session with the application's
own CREATE_TABLE_SQL and truncated before every test, so each test starts
from an empty table; ingestion_watermarks and analysis_summary are dropped
before every test.
"""

import os
import secrets

import psycopg2
import pytest
from bs4 import BeautifulSoup
from dotenv import dotenv_values
from psycopg2 import sql
from selenium.common.exceptions import NoSuchElementException
from sqlalchemy.engine import make_url

import config as app_config
import load_data
from config import db_env
from flask_app import AppDependencies, create_app
from busy_state import InMemoryBusyState

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

_UNSET = object()
_saved_db_env = {}
_real_load_dotenv = app_config.load_dotenv


def _load_dotenv_except_developer_env(dotenv_path=None, **kwargs):
    if dotenv_path is not None and os.path.abspath(dotenv_path) == app_config.ENV_FILE:
        return False
    return _real_load_dotenv(dotenv_path, **kwargs)


def _read_test_database_url():
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ENV_FILE).get("TEST_DATABASE_URL")
    if not url:
        raise pytest.UsageError(
            "TEST_DATABASE_URL is not set. Define it in the environment or in module_6/.env as "
            "postgresql://USER[:PASSWORD]@HOST:PORT/<name>_test"
        )
    database = make_url(url).database or ""
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
        **db_env(test_url),
        "APP_DB_USER": TEST_APP_ROLE,
        "APP_DB_PASSWORD": secrets.token_urlsafe(16),
    }
    for name in test_env:
        _saved_db_env[name] = os.environ.get(name, _UNSET)
    os.environ.update(test_env)
    app_config.load_dotenv = _load_dotenv_except_developer_env


def pytest_unconfigure(config):
    app_config.load_dotenv = _real_load_dotenv
    for name, value in _saved_db_env.items():
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


@pytest.fixture(autouse=True)
def pull_result_path(tmp_path, monkeypatch):
    """Per-test pull result file: apps, pull_data.main() and CLI runs never touch src/."""
    path = tmp_path / "pull_result.json"
    monkeypatch.setenv("PULL_RESULT_FILE", str(path))
    return path


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


class FakeScraper:
    """Stands in for pull_data.scrape_new_entries: returns fixed records, or raises."""

    def __init__(self, records=(), raises=None):
        self.records = list(records)
        self.raises = raises
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return list(self.records)


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
def scraper(fake_records):
    return FakeScraper(fake_records)


@pytest.fixture
def real_loader(test_database_url):
    def loader(records):
        return load_data.load_into_database(records, test_database_url)

    return loader


@pytest.fixture
def loader_spy(real_loader):
    return Spy(real_loader)


@pytest.fixture
def refresh_spy():
    return Spy()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@pytest.fixture
def busy_state():
    return InMemoryBusyState()


@pytest.fixture
def make_app(test_database_url, busy_state, _applicants_table):
    """Build an app on the test database. analysis_fn is never injected, so pages
    run the real get_analysis(); refresh_fn is real unless a test passes one."""

    def build(**overrides):
        overrides.setdefault("busy_state", busy_state)
        return create_app({"DB_URL": test_database_url, "TESTING": True}, AppDependencies(**overrides))

    return build


@pytest.fixture
def app(make_app, scraper, loader_spy, refresh_spy):
    return make_app(scraper=scraper, loader=loader_spy, refresh_fn=refresh_spy)


@pytest.fixture
def client(app):
    return app.test_client()


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
    """TEST_DATABASE_URL, logging in as `role` instead."""
    url = make_url(test_database_url).set(username=role, password=password)
    return url.render_as_string(hide_password=False)
