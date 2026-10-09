"""The web service's Flask app: the analysis page, the two task buttons, and two JSON APIs.

The page renders the analysis snapshot the worker stores in analysis_summary;
nothing here computes the analysis, scrapes, or writes applicant data. The
Pull Data and Update Analysis buttons publish a task for the worker
(publisher.publish_task) and answer 202 at once.

create_app(config) builds the app; there is no module-level app.
"""

from datetime import timezone

from flask import Flask

from app import routes
from app.config import require_env

DATABASE_URL_ENV = "DATABASE_URL"


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


def utc_time(moment):
    """Format a timestamp as "YYYY-MM-DD HH:MM:SS UTC", or "never" when there is none."""
    if moment is None:
        return "never"
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def create_app(config=None):
    """Build the Flask app.

    config keys: DATABASE_URL (else $DATABASE_URL; ConfigError if neither),
    plus any standard Flask setting such as TESTING. RabbitMQ settings are
    read by publisher.py when a task is published.
    """
    config = dict(config or {})
    app = Flask(__name__)
    app.config["DATABASE_URL"] = config.pop("DATABASE_URL", None) or require_env(DATABASE_URL_ENV)
    app.config.update(config)
    app.add_template_filter(two_decimals, "two_decimals")
    app.add_template_filter(percent, "percent")
    app.add_template_filter(utc_time, "utc_time")
    routes.register(app)
    return app
