"""Flask app displaying the Module 3 applicant-data analysis (Q1-9 + 2 custom questions).

Every question on the page is computed by orm_queries.get_analysis()
through the SQLAlchemy Applicant model (models.py). GET /api/applicants
reads through applicant_search.py's psycopg2.sql-composed query. There is
no SQL text anywhere in this file.

create_app() builds the app; there is no module-level app. Every external
dependency is injectable so tests can substitute fakes:

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

import os
import subprocess
import sys
from datetime import datetime, timezone

import psycopg2
from flask import Flask, jsonify, render_template, request

import load_data
from applicant_search import DEFAULT_ORDER, DEFAULT_SORT, build_applicants_query, run_applicants_query
from busy_state import FileLockBusyState
from config import db_env
from models import make_session_factory
from orm_queries import get_analysis
from pull_data import default_result_path, pull_result, read_pull_result, run_pull, write_pull_result
from sql_utils import ValidationError, clamp_limit

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PULL_DATA_SCRIPT = os.path.join(SRC_DIR, "pull_data.py")
LOCK_PATH = os.path.join(SRC_DIR, ".scrape_lock")


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
    """Drop a leading "PULL FAILED:" so the banner doesn't read "Last pull failed: PULL FAILED: ..."."""
    prefix = "PULL FAILED:"
    if message.startswith(prefix):
        return message[len(prefix):].lstrip()
    return message


def create_app(
    config=None,
    *,
    scraper=None,
    loader=None,
    analysis_fn=None,
    refresh_fn=None,
    busy_state=None,
    pull_launcher=None,
):
    """Build the Flask app. See the module docstring for the injectable dependencies.

    config keys: SECRET_KEY (else the SECRET_KEY env var, else random),
    DB_URL (a database URL, else the DB_* settings via config.get_db_url()),
    BUSY_LOCK_PATH,
    PULL_RESULT_PATH (else pull_data.default_result_path()), plus any
    standard Flask setting such as TESTING.
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

    database_url = app.config.get("DB_URL")
    result_path = app.config.get("PULL_RESULT_PATH") or default_result_path()

    if busy_state is None:
        busy_state = FileLockBusyState(app.config.get("BUSY_LOCK_PATH", LOCK_PATH))

    table_checked = []

    def ensure_table_once():
        """Create the (empty) applicants table on first use against a fresh database."""
        if not table_checked:
            load_data.ensure_table(database_url)
            table_checked.append(True)

    if analysis_fn is None:
        session_factory = make_session_factory(database_url)

        def analysis_fn():
            """Default analysis: get_analysis() on the configured database, creating the table on first use."""
            # First run against a fresh database: the page renders "N/A"
            # answers instead of failing.
            ensure_table_once()
            with session_factory() as session:
                return get_analysis(session)

    if loader is None:

        def loader(records):
            """Default loader: insert records into the configured database via load_data."""
            return load_data.load_into_database(records, database_url)

    if pull_launcher is None:

        def pull_launcher():
            """Default launcher: start pull_data.py as a background subprocess."""
            env = dict(os.environ)
            if database_url:
                env.update(db_env(database_url))
            env["PULL_RESULT_FILE"] = result_path
            return subprocess.Popen([sys.executable, PULL_DATA_SCRIPT], cwd=SRC_DIR, env=env)

    snapshot = {"data": None, "refreshed_at": None}

    def refresh_analysis():
        """Recompute the analysis and store it, with a timestamp, as the page's snapshot."""
        snapshot["data"] = analysis_fn()
        snapshot["refreshed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if refresh_fn is None:
        refresh_fn = refresh_analysis

    @app.route("/")
    @app.route("/analysis")
    def analysis():
        """GET / and /analysis: render the analysis page from the current snapshot."""
        if snapshot["data"] is None:
            refresh_analysis()
        return render_template(
            "analysis.html",
            pull_status=busy_state.status(),
            last_result=read_pull_result(result_path),
            refreshed_at=snapshot["refreshed_at"],
            **snapshot["data"],
        )

    @app.route("/pull-data", methods=["POST"])
    def pull_data():
        """POST /pull-data: start a pull (202 subprocess / 200 in-process), or 409 if busy."""
        if busy_state.is_busy():
            return jsonify(busy=True), 409

        # Acquire before launching anything: the app's own PID holds the
        # lock until the subprocess exists, then ownership moves to it.
        if not busy_state.try_acquire(os.getpid()):
            return jsonify(busy=True), 409

        if scraper is None:
            try:
                proc = pull_launcher()
            except Exception as exc:  # e.g. OSError starting the interpreter
                busy_state.release()
                app.logger.exception("Launching Pull Data failed")
                return jsonify(ok=False, error=str(exc) or type(exc).__name__), 500
            busy_state.set_owner(proc.pid)
            return jsonify(ok=True), 202

        try:
            result = run_pull(scraper, loader)
        except Exception as exc:  # any scrape/load failure is reported to the client
            app.logger.exception("Pull Data failed")
            error = str(exc) or type(exc).__name__
            write_pull_result(result_path, pull_result(error=error))
            return jsonify(ok=False, error=error), 500
        finally:
            busy_state.release()
        write_pull_result(result_path, pull_result(run=result))
        return jsonify(ok=True, inserted=result["inserted"]), 200

    @app.route("/pull-status")
    def pull_status():
        """GET /pull-status: whether a pull is running, plus the last pull's result."""
        return jsonify(running=busy_state.is_busy(), last_result=read_pull_result(result_path))

    @app.route("/update-analysis", methods=["POST"])
    def update_analysis():
        """POST /update-analysis: run refresh_fn and answer 200, or 409 if a pull is running."""
        if busy_state.is_busy():
            return jsonify(busy=True), 409
        refresh_fn()
        return jsonify(ok=True), 200

    @app.route("/api/applicants")
    def api_applicants():
        """GET /api/applicants: applicant rows; 400 on invalid parameters, 503 if the DB is down.

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
            ensure_table_once()
            rows = run_applicants_query(stmt, params, database_url)
        except psycopg2.OperationalError:
            app.logger.exception("Applicant search failed: database unavailable")
            return jsonify(error="database unavailable"), 503
        return jsonify(count=len(rows), limit=limit, sort=sort, order=order, rows=rows), 200

    return app


if __name__ == "__main__":
    create_app().run(debug=True, threaded=True)
