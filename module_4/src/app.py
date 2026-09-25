"""Flask app displaying the Module 3 applicant-data analysis (Q1-9 + 2 custom questions).

All database reads go through the SQLAlchemy Applicant model (models.py);
every question on the page is computed by orm_queries.get_analysis(), with
no raw SQL / text() anywhere in this file.
"""

import os
import subprocess
import sys
import threading

from flask import Flask, flash, redirect, render_template, url_for

import scrape_lock
from models import make_session_factory
from orm_queries import get_analysis

app = Flask(__name__)
app.secret_key = os.urandom(24)

Session = make_session_factory()

PULL_DATA_SCRIPT = os.path.join(os.path.dirname(__file__), "pull_data.py")


@app.template_filter("two_decimals")
def two_decimals(value):
    """Format a number with exactly 2 decimals, or "N/A" when there is no value."""
    if value is None:
        return "N/A"
    return f"{value:.2f}"


@app.template_filter("percent")
def percent(value):
    """Format a percentage with exactly 2 decimals and a % sign, or "N/A" when there is no value."""
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _wait_and_release(proc):
    proc.wait()
    scrape_lock.release()


@app.route("/pull-data", methods=["POST"])
def pull_data():
    status = scrape_lock.get_status()
    if status["running"]:
        flash(f"A data pull is already in progress (started {status['started_at']}) — please wait.")
        return redirect(url_for("analysis"))

    proc = subprocess.Popen([sys.executable, PULL_DATA_SCRIPT], cwd=os.path.dirname(__file__))

    if not scrape_lock.try_acquire(proc.pid):
        # Lost an extremely unlikely race against another request; don't
        # leave this subprocess orphaned and untracked.
        proc.terminate()
        flash("A data pull is already in progress — please wait.")
        return redirect(url_for("analysis"))

    threading.Thread(target=_wait_and_release, args=(proc,), daemon=True).start()
    flash(
        "Data pull started — this requires a Chrome browser already running with "
        "remote debugging enabled and Grad Cafe's Cloudflare challenge already "
        "manually cleared in that session (see README.txt). Click Update Analysis "
        "later to refresh the numbers below."
    )
    return redirect(url_for("analysis"))


@app.route("/update-analysis", methods=["POST"])
def update_analysis():
    status = scrape_lock.get_status()
    if status["running"]:
        flash(f"A data pull is currently in progress (started {status['started_at']}) — showing current data.")
    else:
        flash("Analysis refreshed.")
    return redirect(url_for("analysis"))


@app.route("/")
def analysis():
    pull_status = scrape_lock.get_status()

    with Session() as session:
        analysis_data = get_analysis(session)

    return render_template("analysis.html", pull_status=pull_status, **analysis_data)


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
