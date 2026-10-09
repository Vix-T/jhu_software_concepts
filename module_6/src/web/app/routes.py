"""The web service's routes.

GET  /, /analysis      the analysis page, rendered from analysis_summary
POST /pull-data        queue a scrape_new_data task      -> 202, or 503
POST /update-analysis  queue a recompute_analytics task  -> 202, or 503
GET  /api/status       when the analysis and the data last changed (polled by the page)
GET  /api/applicants   applicant rows (validated limit/sort/order/university)

Only specific exceptions are handled: database read failures
(app.db.DB_READ_ERRORS), invalid query parameters (ValidationError), and a
failed publish (pika.exceptions.AMQPError, or ConfigError when RABBITMQ_URL
is missing). Anything else is a bug: Flask answers 500 without a traceback.
"""

import logging

from flask import current_app, jsonify, render_template, request
from pika.exceptions import AMQPError

import publisher
from app.applicant_search import (
    DEFAULT_ORDER,
    DEFAULT_SORT,
    ValidationError,
    build_applicants_query,
    parse_limit,
    run_applicants_query,
)
from app.config import ConfigError
from app.db import (
    DB_NOT_INITIALIZED,
    DB_PERMISSION_DENIED,
    DB_READ_ERRORS,
    DB_UNAVAILABLE,
    db_error_message,
    read_snapshot,
    utc_iso,
)

logger = logging.getLogger(__name__)

# Button endpoint -> the task kind it queues for the worker.
SCRAPE_TASK = "scrape_new_data"
RECOMPUTE_TASK = "recompute_analytics"
QUEUE_UNAVAILABLE = "message queue unavailable"

# The page's explanation for each database failure (see app.db.db_error_message).
PAGE_DB_MESSAGES = {
    DB_UNAVAILABLE: (
        "Database unavailable: the analysis could not be loaded. Check that PostgreSQL "
        "is running and DATABASE_URL is correct, then reload this page."
    ),
    DB_NOT_INITIALIZED: (
        "Database not initialized: the worker creates the tables when it starts. "
        "Start the worker, then reload this page."
    ),
    DB_PERMISSION_DENIED: (
        "Database permission denied: the web service's database user cannot read the analysis."
    ),
}


def _database_url():
    return current_app.config["DATABASE_URL"]


def analysis():
    """GET / and /analysis: the analysis page (503 with a message if the database can't be read)."""
    try:
        snapshot = read_snapshot(_database_url())
    except DB_READ_ERRORS as exc:
        reason = db_error_message(exc)
        logger.exception("Analysis page: %s", reason)
        page = render_template("analysis.html", db_error=PAGE_DB_MESSAGES[reason], snapshot=None)
        return page, 503
    return render_template(
        "analysis.html",
        db_error=None,
        snapshot=snapshot,
        computed_at_iso=utc_iso(snapshot.computed_at),
        last_pulled_iso=utc_iso(snapshot.last_pulled_at),
        **(snapshot.results or {}),
    )


def _queue(task):
    """Publish `task` for the worker: 202 {"status": "queued"}, or 503 if it can't be queued."""
    try:
        publisher.publish_task(task)
    except AMQPError as exc:
        logger.error("Queueing %s failed: %r", task, exc)
        return jsonify(status="error", task=task, error=QUEUE_UNAVAILABLE), 503
    except ConfigError as exc:
        logger.error("Queueing %s failed: %s", task, exc)
        return jsonify(status="error", task=task, error=str(exc)), 503
    return jsonify(status="queued", task=task), 202


def pull_data():
    """POST /pull-data: queue a scrape of the newest Grad Cafe entries."""
    return _queue(SCRAPE_TASK)


def update_analysis():
    """POST /update-analysis: queue a recompute of the analysis summary."""
    return _queue(RECOMPUTE_TASK)


def api_status():
    """GET /api/status: when the analysis was computed and data last updated, and the row count."""
    try:
        snapshot = read_snapshot(_database_url())
    except DB_READ_ERRORS as exc:
        reason = db_error_message(exc)
        logger.exception("Status: %s", reason)
        return jsonify(error=reason), 503
    return jsonify(
        computed_at=utc_iso(snapshot.computed_at),
        watermark_updated_at=utc_iso(snapshot.last_pulled_at),
        row_count=snapshot.row_count,
    )


def api_applicants():
    """GET /api/applicants: applicant rows; 400 on invalid parameters, 503 if the DB fails.

    Query parameters: limit (clamped to [1, 100], default 10), sort (one of
    applicant_search.SORT_COLUMNS), order ("asc"/"desc"), and an optional
    university substring filter.
    """
    args = request.args
    try:
        limit = parse_limit(args.get("limit"))
        sort = args.get("sort", DEFAULT_SORT)
        order = args.get("order", DEFAULT_ORDER)
        stmt, params = build_applicants_query(limit, sort, order, args.get("university"))
    except ValidationError as exc:
        return jsonify(error=str(exc)), 400

    try:
        rows = run_applicants_query(stmt, params, _database_url())
    except DB_READ_ERRORS as exc:
        reason = db_error_message(exc)
        logger.exception("Applicant search: %s", reason)
        return jsonify(error=reason), 503
    return jsonify(count=len(rows), limit=limit, sort=sort, order=order, rows=rows)


def register(app):
    """Attach every route to `app` (endpoint names match the template's url_for calls)."""
    app.add_url_rule("/", "analysis", analysis)
    app.add_url_rule("/analysis", "analysis", analysis)
    app.add_url_rule("/pull-data", "pull_data", pull_data, methods=["POST"])
    app.add_url_rule("/update-analysis", "update_analysis", update_analysis, methods=["POST"])
    app.add_url_rule("/api/status", "api_status", api_status)
    app.add_url_rule("/api/applicants", "api_applicants", api_applicants)
