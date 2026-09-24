"""Flask app displaying the Module 3 applicant-data analysis (Q1-9 + 2 custom questions).

All database reads go through the SQLAlchemy Applicant model (models.py).
Questions already implemented in orm_queries.py (Q1, Q4, Q5, Q8, Q9, GRE
contamination) are reused as-is; the remaining questions (Q2, Q3, Q6, Q7,
acceptance-by-degree) are written here with the same ORM-only approach --
no raw SQL / text() anywhere in this file.
"""

import os
import subprocess
import sys
import threading

from flask import Flask, flash, redirect, render_template, url_for
from sqlalchemy import case, func, select

import scrape_lock
from models import Applicant, Session
from orm_queries import orm_custom1, orm_q1, orm_q4, orm_q5, orm_q8, orm_q9
from query_data import CS_PATTERN, JHU_PATTERN

app = Flask(__name__)
app.secret_key = os.urandom(24)

PULL_DATA_SCRIPT = os.path.join(os.path.dirname(__file__), "pull_data.py")


def q2_percent_international(session):
    """Percent international among entries with a usable nationality classification."""
    stmt = select(
        func.sum(case((Applicant.us_or_international.ilike("International"), 1), else_=0)),
        func.count(),
    ).where(
        Applicant.us_or_international.isnot(None),
        func.trim(Applicant.us_or_international) != "",
    )
    numerator, denominator = session.execute(stmt).one()
    return numerator, denominator, 100.0 * numerator / denominator


def q3_averages(session):
    """Average GPA, GRE, GRE V, GRE AW, each independently, with range-filtering (see Q3 write-up)."""
    averages = {}

    stmt = select(func.avg(Applicant.gpa), func.count(Applicant.gpa)).where(Applicant.gpa.isnot(None))
    averages["GPA"] = session.execute(stmt).one()

    stmt = select(func.avg(Applicant.gre), func.count(Applicant.gre)).where(Applicant.gre.between(130, 170))
    averages["GRE"] = session.execute(stmt).one()

    stmt = select(func.avg(Applicant.gre_v), func.count(Applicant.gre_v)).where(
        Applicant.gre_v.between(130, 170)
    )
    averages["GRE V"] = session.execute(stmt).one()

    stmt = select(func.avg(Applicant.gre_aw), func.count(Applicant.gre_aw)).where(
        Applicant.gre_aw.between(0, 6)
    )
    averages["GRE AW"] = session.execute(stmt).one()

    return averages


def q6_avg_gpa_accepted(session):
    """Average GPA of accepted applicants who applied for Fall 2026."""
    stmt = select(func.avg(Applicant.gpa), func.count(Applicant.gpa)).where(
        Applicant.term.ilike("Fall 2026"),
        Applicant.status.ilike("Accepted"),
        Applicant.gpa.isnot(None),
    )
    return session.execute(stmt).one()


def q7_jhu_masters_cs(session):
    """JHU + master's degree + Computer Science, all-time, one combined count."""
    stmt = select(func.count()).where(
        Applicant.program.op("~*")(JHU_PATTERN),
        Applicant.program.op("~*")(CS_PATTERN),
        Applicant.degree == "Masters",
    )
    return session.scalar(stmt)


def custom2_acceptance_by_degree(session):
    """Acceptance rate by degree type (PhD vs. Masters, plus all other degree values)."""
    accepted = func.sum(case((Applicant.status.ilike("Accepted"), 1), else_=0))
    stmt = (
        select(Applicant.degree, accepted, func.count())
        .where(Applicant.degree.isnot(None))
        .group_by(Applicant.degree)
        .order_by(func.count().desc())
    )
    rows = session.execute(stmt).all()
    return [(degree, accepted_n, total, 100.0 * accepted_n / total) for degree, accepted_n, total in rows]


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
        q1_count = orm_q1(session)
        q2_num, q2_denom, q2_pct = q2_percent_international(session)
        q3 = q3_averages(session)
        q4_avg, q4_n = orm_q4(session)
        q5_num, q5_denom, q5_pct = orm_q5(session)
        q6_avg, q6_n = q6_avg_gpa_accepted(session)
        q7_count = q7_jhu_masters_cs(session)
        q8_count = orm_q8(session)
        q9_count = orm_q9(session)
        custom1_contaminated, custom1_total, custom1_pct = orm_custom1(session)
        custom2_rows = custom2_acceptance_by_degree(session)

    return render_template(
        "analysis.html",
        pull_status=pull_status,
        q1_count=q1_count,
        q2_num=q2_num,
        q2_denom=q2_denom,
        q2_pct=q2_pct,
        q3=q3,
        q4_avg=q4_avg,
        q4_n=q4_n,
        q5_num=q5_num,
        q5_denom=q5_denom,
        q5_pct=q5_pct,
        q6_avg=q6_avg,
        q6_n=q6_n,
        q7_count=q7_count,
        q8_count=q8_count,
        q9_count=q9_count,
        q8_q9_diff=q8_count - q9_count,
        custom1_contaminated=custom1_contaminated,
        custom1_total=custom1_total,
        custom1_pct=custom1_pct,
        custom2_rows=custom2_rows,
    )


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
