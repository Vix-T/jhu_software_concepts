"""Flask app displaying the Module 3 applicant-data analysis (Q1-9 + 2 custom questions).

Every question on the page is computed by orm_queries.get_analysis()
through the SQLAlchemy Applicant model (models.py). GET /api/applicants
reads through applicant_search.py's psycopg2.sql-composed query. There is
no SQL text anywhere in this file.

create_app(config, deps) builds the app; there is no module-level app.
Every external dependency is injectable through an AppDependencies, so tests
can substitute fakes:

    scraper        () -> list[dict]; when given, Pull Data runs in-process
                   (run_pull) and answers 200. When None, Pull Data launches
                   pull_data.py as a background subprocess and answers 202.
    loader         (records) -> (inserted, skipped, failed); default loads
                   into the DB_* database via load_data.load_into_database().
    analysis_fn    () -> dict of analysis results; default runs
                   get_analysis() against the DB_* database.
    refresh_fn     () -> None; what Update Analysis runs. Default recomputes
                   the analysis snapshot the page displays.
    busy_state     try_acquire/set_owner/release/is_busy/status (busy_state.py);
                   default FileLockBusyState on src/.scrape_lock.
    pull_launcher  () -> process with a .pid; default
                   starts pull_data.py with subprocess.Popen.

Every pull's outcome is recorded in a JSON result file (config
PULL_RESULT_PATH, default pull_data.default_result_path()): the pull
subprocess writes it when it finishes, and the in-process path writes it
itself. The page's last-pull banner and GET /pull-status read it.

The page shows a cached analysis snapshot: it is computed on the first
page load and recomputed only when Update Analysis runs, so newly pulled
rows appear once Update Analysis is clicked.
"""

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

import psycopg2
import sqlalchemy.exc
from flask import Flask, jsonify, render_template, request
from selenium.common.exceptions import WebDriverException

import load_data
from applicant_search import (
    DEFAULT_ORDER,
    DEFAULT_SORT,
    build_applicants_query,
    run_applicants_query,
)
from busy_state import FileLockBusyState
from config import ConfigError, db_env
from models import make_session_factory
from orm_queries import get_analysis
from pull_data import (
    PullPreconditionError,
    default_result_path,
    pending_result,
    pull_result,
    run_pull,
    settle_pull_result,
    write_pull_result,
)
from scrape import ScrapeRetriesExhausted
from sql_utils import INSUFFICIENT_PRIVILEGE, ValidationError, clamp_limit

logger = logging.getLogger(__name__)

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PULL_DATA_SCRIPT = os.path.join(SRC_DIR, "pull_data.py")
LOCK_PATH = os.path.join(SRC_DIR, ".scrape_lock")

# The database is unreachable: psycopg2 raises its own OperationalError, and
# SQLAlchemy (the ORM analysis) wraps the same driver error in its own class.
DB_UNAVAILABLE_ERRORS = (psycopg2.OperationalError, sqlalchemy.exc.OperationalError)
DB_UNAVAILABLE = "database unavailable"
DB_UNAVAILABLE_PAGE = (
    "Database unavailable: the analysis could not be loaded. Check that PostgreSQL "
    "is running and the DB_* settings are correct, then reload this page."
)
# Failures an in-process pull can actually hit and report: the scraper gave up
# or couldn't attach to Chrome, the database rejected the load, a file/socket
# error, or missing DB_* settings.
PULL_FAILURES = (
    load_data.TableMissingError,
    ScrapeRetriesExhausted,
    PullPreconditionError,
    WebDriverException,
    psycopg2.Error,
    OSError,
    ConfigError,
)
# Errors starting the pull subprocess.
LAUNCH_FAILURES = (OSError, subprocess.SubprocessError)
DEBUG_ENV = "FLASK_DEBUG"


def two_decimals(value):
    """Format a number with exactly 2 decimals, or "N/A" when there is no value."""
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def percent(value):
    """Format a percentage with exactly 2 decimals and a % sign, or "N/A" when there is no value."""
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def without_failure_prefix(message):
    """Drop a leading "PULL FAILED:" so the banner doesn't say "failed" twice."""
    prefix = "PULL FAILED:"
    if message.startswith(prefix):
        return message[len(prefix):].lstrip()
    return message


def debug_enabled():
    """True only when FLASK_DEBUG is 1/true/yes: the debugger and reloader are off by default."""
    return os.environ.get(DEBUG_ENV, "").strip().lower() in ("1", "true", "yes")


@dataclass
class AppDependencies:
    """create_app()'s injectable collaborators; None means the default (see module docstring)."""

    scraper: Callable | None = None
    loader: Callable | None = None
    analysis_fn: Callable | None = None
    refresh_fn: Callable | None = None
    busy_state: object = None
    pull_launcher: Callable | None = None


@dataclass
class _Snapshot:
    """The analysis the page shows, and when it was computed (None until the first load)."""

    data: dict = field(default_factory=dict)
    refreshed_at: str | None = None


class _AppRoutes:
    """The app's view functions and the state they share (one instance per app)."""

    def __init__(self, app, deps):
        self.database_url = app.config.get("DB_URL")
        self.result_path = app.config.get("PULL_RESULT_PATH") or default_result_path()
        self.snapshot = _Snapshot()
        self._table_checked = False
        self.deps = self._with_defaults(deps or AppDependencies(), app.config)

    def _with_defaults(self, deps, config):
        """Fill every dependency the caller didn't inject (the scraper stays optional)."""
        return replace(
            deps,
            loader=deps.loader or self._load_records,
            analysis_fn=deps.analysis_fn or self._orm_analysis(),
            refresh_fn=deps.refresh_fn or self.refresh_analysis,
            busy_state=deps.busy_state
            or FileLockBusyState(config.get("BUSY_LOCK_PATH", LOCK_PATH)),
            pull_launcher=deps.pull_launcher or self._start_pull_subprocess,
        )

    def register(self, app):
        """Attach every route to `app` (endpoint names match the template's url_for calls)."""
        app.add_url_rule("/", "analysis", self.analysis)
        app.add_url_rule("/analysis", "analysis", self.analysis)
        app.add_url_rule("/pull-data", "pull_data", self.pull_data, methods=["POST"])
        app.add_url_rule("/pull-status", "pull_status", self.pull_status)
        app.add_url_rule(
            "/update-analysis", "update_analysis", self.update_analysis, methods=["POST"]
        )
        app.add_url_rule("/api/applicants", "api_applicants", self.api_applicants)

    # -- default dependencies -------------------------------------------------

    def ensure_table_once(self):
        """Create the (empty) applicants table on first use against a fresh database."""
        if not self._table_checked:
            load_data.ensure_table(self.database_url)
            self._table_checked = True

    def _orm_analysis(self):
        """Default analysis_fn: get_analysis() on the configured database."""
        session_factory = make_session_factory(self.database_url)

        def analysis():
            # First run against a fresh database: the page renders "N/A"
            # answers instead of failing.
            self.ensure_table_once()
            with session_factory() as session:
                return get_analysis(session)

        return analysis

    def _load_records(self, records):
        """Default loader: insert records into the configured database via load_data."""
        return load_data.load_into_database(records, self.database_url)

    def _start_pull_subprocess(self):
        """Default pull_launcher: start pull_data.py as a background subprocess."""
        env = dict(os.environ)
        if self.database_url:
            env.update(db_env(self.database_url))
        env["PULL_RESULT_FILE"] = self.result_path
        return subprocess.Popen([sys.executable, PULL_DATA_SCRIPT], cwd=SRC_DIR, env=env)

    def refresh_analysis(self):
        """Recompute the analysis and store it, with a timestamp, as the page's snapshot."""
        self.snapshot.data = self.deps.analysis_fn()
        self.snapshot.refreshed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # -- routes ---------------------------------------------------------------

    def analysis(self):
        """GET / and /analysis: render the analysis page (503 if the DB is down on first load)."""
        pull_status = self.deps.busy_state.status()
        last_result = settle_pull_result(self.result_path, pull_status["running"])
        if self.snapshot.refreshed_at is None:
            try:
                self.refresh_analysis()
            except DB_UNAVAILABLE_ERRORS:
                logger.exception("Analysis page: %s", DB_UNAVAILABLE)
                return self._error_page(DB_UNAVAILABLE_PAGE, pull_status, last_result)
            except load_data.TableMissingError as exc:
                logger.exception("Analysis page: %s", exc)
                return self._error_page(str(exc), pull_status, last_result)
        return render_template(
            "analysis.html",
            db_error=None,
            pull_status=pull_status,
            last_result=last_result,
            refreshed_at=self.snapshot.refreshed_at,
            **self.snapshot.data,
        )

    @staticmethod
    def _error_page(message, pull_status, last_result):
        """The analysis page with `message` in place of the questions, as a 503."""
        page = render_template(
            "analysis.html",
            db_error=message,
            pull_status=pull_status,
            last_result=last_result,
            refreshed_at=None,
        )
        return page, 503

    def pull_data(self):
        """POST /pull-data: start a pull (202 subprocess / 200 in-process), or 409 if busy."""
        busy_state = self.deps.busy_state
        if busy_state.is_busy():
            return jsonify(busy=True), 409

        # Acquire before launching anything: the app's own PID holds the
        # lock until the subprocess exists, then ownership moves to it.
        if not busy_state.try_acquire(os.getpid()):
            return jsonify(busy=True), 409

        if self.deps.scraper is None:
            return self._launch_pull()
        return self._pull_in_process()

    def _launch_pull(self):
        """Start the pull subprocess: 202, or 500 if it couldn't be started."""
        busy_state = self.deps.busy_state
        # Until the child owns the lock, any failure -- handled below or
        # not -- must release it; `finally` does that without catching.
        launched = False
        try:
            # The child overwrites this when it finishes; if it dies first,
            # settle_pull_result() turns it into a recorded failure.
            write_pull_result(self.result_path, pending_result())
            proc = self.deps.pull_launcher()
            launched = True
        except LAUNCH_FAILURES as exc:
            logger.exception("Launching Pull Data failed")
            error = str(exc) or type(exc).__name__
            write_pull_result(self.result_path, pull_result(error=error))
            return jsonify(ok=False, error=error), 500
        finally:
            if not launched:
                busy_state.release()
        # Once the child exists it must keep the lock, so ownership moves
        # to it only after a successful launch.
        busy_state.set_owner(proc.pid)
        return jsonify(ok=True), 202

    def _pull_in_process(self):
        """Run the injected scraper and loader now: 200, or 500 with the recorded failure."""
        try:
            result = run_pull(self.deps.scraper, self.deps.loader)
        except PULL_FAILURES as exc:
            logger.exception("Pull Data failed")
            error = str(exc) or type(exc).__name__
            write_pull_result(self.result_path, pull_result(error=error))
            return jsonify(ok=False, error=error), 500
        finally:
            self.deps.busy_state.release()
        write_pull_result(self.result_path, pull_result(run=result))
        return jsonify(ok=True, inserted=result["inserted"]), 200

    def pull_status(self):
        """GET /pull-status: whether a pull is running, plus the last pull's result."""
        running = self.deps.busy_state.is_busy()
        return jsonify(running=running, last_result=settle_pull_result(self.result_path, running))

    def update_analysis(self):
        """POST /update-analysis: refresh -> 200; 409 while a pull runs; 503 if the DB is down."""
        if self.deps.busy_state.is_busy():
            return jsonify(busy=True), 409
        try:
            self.deps.refresh_fn()
        except DB_UNAVAILABLE_ERRORS:
            logger.exception("Update Analysis: %s", DB_UNAVAILABLE)
            return jsonify(ok=False, error=DB_UNAVAILABLE), 503
        except load_data.TableMissingError as exc:
            logger.exception("Update Analysis: %s", exc)
            return jsonify(ok=False, error=str(exc)), 503
        return jsonify(ok=True), 200

    def api_applicants(self):
        """GET /api/applicants: applicant rows; 400 on invalid parameters, 503 if the DB fails.

        Query parameters: limit (clamped to [1, 100], default 10), sort (one
        of applicant_search.SORT_COLUMNS), order ("asc"/"desc"), and an
        optional university substring filter.
        """
        args = request.args
        try:
            limit = clamp_limit(args.get("limit"))
            sort = args.get("sort", DEFAULT_SORT)
            order = args.get("order", DEFAULT_ORDER)
            stmt, params = build_applicants_query(limit, sort, order, args.get("university"))
        except ValidationError as exc:
            return jsonify(error=str(exc)), 400

        try:
            self.ensure_table_once()
            rows = run_applicants_query(stmt, params, self.database_url)
        except psycopg2.OperationalError:
            logger.exception("Applicant search: %s", DB_UNAVAILABLE)
            return jsonify(error=DB_UNAVAILABLE), 503
        except INSUFFICIENT_PRIVILEGE:
            logger.exception("Applicant search: database permission denied")
            return jsonify(error="database permission denied"), 503
        except load_data.TableMissingError as exc:
            logger.exception("Applicant search: %s", exc)
            return jsonify(error=str(exc)), 503
        return jsonify(count=len(rows), limit=limit, sort=sort, order=order, rows=rows), 200


def create_app(config=None, deps=None):
    """Build the Flask app. See the module docstring for the injectable dependencies.

    config keys: SECRET_KEY (else the SECRET_KEY env var, else random),
    DB_URL (a database URL, else the DB_* settings via config.get_db_url()),
    BUSY_LOCK_PATH, PULL_RESULT_PATH (else pull_data.default_result_path()),
    plus any standard Flask setting such as TESTING. deps is an
    AppDependencies (default: every dependency at its default).
    """
    config = dict(config or {})
    app = Flask(__name__)
    app.config["SECRET_KEY"] = (
        config.pop("SECRET_KEY", None) or os.environ.get("SECRET_KEY") or os.urandom(24)
    )
    app.config.update(config)
    app.add_template_filter(two_decimals, "two_decimals")
    app.add_template_filter(percent, "percent")
    app.add_template_filter(without_failure_prefix, "without_failure_prefix")
    _AppRoutes(app, deps).register(app)
    return app


def main():
    """Run the development server; exit 1 with a clear message if DB_* settings are missing."""
    try:
        app = create_app()
    except ConfigError as exc:
        print(f"Cannot start the app: {exc}")
        sys.exit(1)
    app.run(debug=debug_enabled(), threaded=True)


if __name__ == "__main__":
    main()
