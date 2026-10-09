"""ORM (SQLAlchemy 2.x) versions of query_data.py's analyses.

Reuses the exact same word-boundary regex patterns already defined in
query_data.py for Q8/Q9 university/program matching (imported, not
redefined), and the same Applicant model / session factory from models.py. The
ORM query functions themselves (orm_q1/orm_q4/orm_q5/orm_q8/orm_q9/
orm_custom1) use no raw SQL (text()) or psycopg2 cursors anywhere --
everything is expressed with ``select()``/``where()``/``func``/``and_``/``or_``/``case()``.

main()'s comparison harness is the one exception: it calls
query_data.py's own raw-SQL functions (q1/q4/q5/q8/q9/custom1) live,
via a psycopg2 cursor, so the "raw-SQL" side of the comparison always
reflects the current database state instead of a hardcoded snapshot
that goes stale the next time Pull Data adds rows.

get_analysis() gathers every question the Flask page displays into one
dict. Percentages over an empty set come back as None (rendered "N/A")
instead of raising ZeroDivisionError.
"""

import psycopg2
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.sql import functions as sql_functions

from sql_utils import MAX_LIMIT, SINGLE_ROW, clamp_limit
import query_data
from config import psycopg2_dsn
from models import Applicant, make_session_factory
from query_data import CS_PATTERN, JHU_PATTERN, NO_DATA, Q8_Q9_UNIVERSITIES
from query_data import percent_or_none as _percent


def _round2(value):
    return None if value is None else round(value, 2)


def _single_row(stmt):
    """Aggregate queries return exactly one row: LIMIT 1, through clamp_limit()."""
    return stmt.limit(clamp_limit(SINGLE_ROW))


def orm_q1(session):
    """Count of Fall 2026 entries, by term."""
    stmt = select(sql_functions.count()).where(Applicant.term.ilike("Fall 2026"))
    return session.scalar(_single_row(stmt))


def orm_q4(session):
    """Average GPA of American applicants who applied for Fall 2026."""
    stmt = select(func.avg(Applicant.gpa), sql_functions.count(Applicant.gpa)).where(
        Applicant.term.ilike("Fall 2026"),
        Applicant.us_or_international.ilike("American"),
        Applicant.gpa.isnot(None),
    )
    return session.execute(_single_row(stmt)).one()


def orm_q5(session):
    """Percentage of Fall 2025 entries that are acceptances."""
    accepted = sql_functions.sum(case((Applicant.status.ilike("Accepted"), 1), else_=0))
    stmt = select(accepted, sql_functions.count()).where(Applicant.term.ilike("Fall 2025"))
    numerator, denominator = session.execute(_single_row(stmt)).one()
    numerator = numerator or 0
    return numerator, denominator, _percent(numerator, denominator)


def orm_q8(session):
    """Fall 2026 + Accepted + PhD + CS, at one of 4 universities (original fields)."""
    university_clause = or_(
        *[Applicant.program.op("~*")(pattern) for _, pattern in Q8_Q9_UNIVERSITIES]
    )
    stmt = select(sql_functions.count()).where(
        and_(
            Applicant.term == "Fall 2026",
            Applicant.status == "Accepted",
            Applicant.degree == "PhD",
            Applicant.program.op("~*")(CS_PATTERN),
            university_clause,
        )
    )
    return session.scalar(_single_row(stmt))


def orm_q9(session):
    """Same as Q8, but university/program matched via the LLM-generated fields."""
    university_clause = or_(
        *[
            Applicant.llm_generated_university.op("~*")(pattern)
            for _, pattern in Q8_Q9_UNIVERSITIES
        ]
    )
    stmt = select(sql_functions.count()).where(
        and_(
            Applicant.term == "Fall 2026",
            Applicant.status == "Accepted",
            Applicant.degree == "PhD",
            Applicant.llm_generated_program.op("~*")(CS_PATTERN),
            university_clause,
        )
    )
    return session.scalar(_single_row(stmt))


def orm_custom1(session):
    """Percent of non-null GRE Quant entries outside the plausible [130,170] range."""
    contaminated = sql_functions.sum(
        case((~Applicant.gre.between(130, 170), 1), else_=0)
    )
    stmt = select(contaminated, sql_functions.count()).where(Applicant.gre.isnot(None))
    contaminated_count, total = session.execute(_single_row(stmt)).one()
    contaminated_count = contaminated_count or 0
    return contaminated_count, total, _percent(contaminated_count, total)


def q2_percent_international(session):
    """Percent international among entries with a usable nationality classification."""
    stmt = select(
        sql_functions.sum(case((Applicant.us_or_international.ilike("International"), 1), else_=0)),
        sql_functions.count(),
    ).where(
        Applicant.us_or_international.isnot(None),
        func.trim(Applicant.us_or_international) != "",
    )
    numerator, denominator = session.execute(_single_row(stmt)).one()
    numerator = numerator or 0
    return numerator, denominator, _percent(numerator, denominator)


def q3_averages(session):
    """Average GPA, GRE, GRE V, GRE AW, each independently, range-filtered (see Q3 write-up)."""
    averages = {}

    stmt = select(func.avg(Applicant.gpa), sql_functions.count(Applicant.gpa)).where(
        Applicant.gpa.isnot(None)
    )
    averages["GPA"] = session.execute(_single_row(stmt)).one()

    stmt = select(func.avg(Applicant.gre), sql_functions.count(Applicant.gre)).where(
        Applicant.gre.between(130, 170)
    )
    averages["GRE"] = session.execute(_single_row(stmt)).one()

    stmt = select(func.avg(Applicant.gre_v), sql_functions.count(Applicant.gre_v)).where(
        Applicant.gre_v.between(130, 170)
    )
    averages["GRE V"] = session.execute(_single_row(stmt)).one()

    stmt = select(func.avg(Applicant.gre_aw), sql_functions.count(Applicant.gre_aw)).where(
        Applicant.gre_aw.between(0, 6)
    )
    averages["GRE AW"] = session.execute(_single_row(stmt)).one()

    return averages


def q6_avg_gpa_accepted(session):
    """Average GPA of accepted applicants who applied for Fall 2026."""
    stmt = select(func.avg(Applicant.gpa), sql_functions.count(Applicant.gpa)).where(
        Applicant.term.ilike("Fall 2026"),
        Applicant.status.ilike("Accepted"),
        Applicant.gpa.isnot(None),
    )
    return session.execute(_single_row(stmt)).one()


def q7_jhu_masters_cs(session):
    """JHU + master's degree + Computer Science, all-time, one combined count."""
    stmt = select(sql_functions.count()).where(
        Applicant.program.op("~*")(JHU_PATTERN),
        Applicant.program.op("~*")(CS_PATTERN),
        Applicant.degree == "Masters",
    )
    return session.scalar(_single_row(stmt))


def custom2_acceptance_by_degree(session):
    """Acceptance rate by degree type; ties on count are ordered alphabetically by degree."""
    accepted = sql_functions.sum(case((Applicant.status.ilike("Accepted"), 1), else_=0))
    stmt = (
        select(Applicant.degree, accepted, sql_functions.count())
        .where(Applicant.degree.isnot(None))
        .group_by(Applicant.degree)
        .order_by(sql_functions.count().desc(), Applicant.degree)
        .limit(clamp_limit(MAX_LIMIT))
    )
    rows = session.execute(stmt).all()
    return [
        (degree, accepted_n, total, _percent(accepted_n, total))
        for degree, accepted_n, total in rows
    ]


def get_applicants(session, limit=None):
    """Return applicant rows as dicts keyed by the applicants-table column names.

    Rows are ordered by p_id (insertion order). `limit` goes through
    clamp_limit(): None means DEFAULT_LIMIT, and it is clamped to
    [MIN_LIMIT, MAX_LIMIT].
    """
    stmt = select(Applicant).order_by(Applicant.p_id).limit(clamp_limit(limit))
    return [applicant.to_dict() for applicant in session.scalars(stmt)]


def get_analysis(session):
    """Run every question shown on the analysis page and return the results as one dict."""
    results = {"q1_count": orm_q1(session)}
    results["q2_num"], results["q2_denom"], results["q2_pct"] = q2_percent_international(session)
    results["q3"] = q3_averages(session)
    results["q4_avg"], results["q4_n"] = orm_q4(session)
    results["q5_num"], results["q5_denom"], results["q5_pct"] = orm_q5(session)
    results["q6_avg"], results["q6_n"] = q6_avg_gpa_accepted(session)
    results["q7_count"] = q7_jhu_masters_cs(session)
    results["q8_count"] = orm_q8(session)
    results["q9_count"] = orm_q9(session)
    results["q8_q9_diff"] = results["q8_count"] - results["q9_count"]
    (
        results["custom1_contaminated"],
        results["custom1_total"],
        results["custom1_pct"],
    ) = orm_custom1(session)
    results["custom2_rows"] = custom2_acceptance_by_degree(session)
    return results


# The questions main() computes both ways: name (also the query_data.py
# raw-SQL function's name) -> the ORM function answering it.
COMPARED_QUESTIONS = {
    "q1": orm_q1,
    "q4": orm_q4,
    "q5": orm_q5,
    "q8": orm_q8,
    "q9": orm_q9,
    "custom1": orm_custom1,
}


def orm_answers(session):
    """COMPARED_QUESTIONS answered through the ORM, keyed by question name."""
    return {name: orm_fn(session) for name, orm_fn in COMPARED_QUESTIONS.items()}


def raw_sql_answers(cur):
    """COMPARED_QUESTIONS answered by query_data.py's raw SQL, keyed by question name."""
    return {name: getattr(query_data, name)(cur) for name in COMPARED_QUESTIONS}


def comparison_checks(orm, raw):
    """(label, description, orm_value, raw_value, fmt) rows for format_comparison()."""
    return [
        ("Q1", "Fall 2026 applicant count", orm["q1"], raw["q1"], "{}"),
        (
            "Q4",
            "Avg GPA, American, Fall 2026",
            _round2(orm["q4"][0]),
            _round2(raw["q4"][0]),
            "{:.2f}",
        ),
        ("Q5", "Fall 2025 acceptance %", _round2(orm["q5"][2]), _round2(raw["q5"][2]), "{:.2f}"),
        ("Q8", "Fall 2026/Accepted/PhD/CS, original fields", orm["q8"], raw["q8"], "{}"),
        ("Q9", "Same as Q8, llm-generated fields", orm["q9"], raw["q9"], "{}"),
        (
            "Custom Q1",
            "GRE Quant contamination %",
            _round2(orm["custom1"][2]),
            _round2(raw["custom1"][2]),
            "{:.2f}",
        ),
    ]


def format_comparison(checks):
    """Format ORM-vs-raw-SQL checks for main()'s report.

    Args:
        checks: (label, description, orm_value, raw_value, fmt) tuples.

    Returns:
        tuple: (table_lines, verdict_line) -- one line per check marked
            [MATCH] or [MISMATCH], and the overall verdict.
    """
    table_lines = []
    all_match = True
    for label, description, orm_value, raw_value, fmt in checks:
        match = orm_value == raw_value
        all_match = all_match and match
        status = "MATCH" if match else "MISMATCH"
        orm_text = NO_DATA if orm_value is None else fmt.format(orm_value)
        raw_text = NO_DATA if raw_value is None else fmt.format(raw_value)
        table_lines.append(
            f"{label:<10} {description:<45} "
            f"ORM={orm_text:<10} raw-SQL={raw_text:<10} [{status}]"
        )

    if all_match:
        verdict_line = "All ORM results match the raw-SQL results from query_data.py."
    else:
        verdict_line = (
            "MISMATCH DETECTED -- see table above. "
            "Not adjusting either query to force a match."
        )
    return table_lines, verdict_line


def main():
    """CLI: answer the comparable questions via the ORM and via raw SQL; print both."""
    with make_session_factory()() as session:
        orm = orm_answers(session)

    conn = psycopg2.connect(psycopg2_dsn())
    try:
        with conn.cursor() as cur:
            raw = raw_sql_answers(cur)
    finally:
        conn.close()

    table_lines, verdict_line = format_comparison(comparison_checks(orm, raw))

    print("ORM vs. raw-SQL (query_data.py) comparison, both computed live in this run:\n")
    for line in table_lines:
        print(line)

    print()
    print(f"Q4 n={orm['q4'][1]} (raw-SQL n={raw['q4'][1]})")
    print(f"Q5 {orm['q5'][0]}/{orm['q5'][1]} (raw-SQL {raw['q5'][0]}/{raw['q5'][1]})")
    print(
        f"Custom Q1 {orm['custom1'][0]}/{orm['custom1'][1]} "
        f"(raw-SQL {raw['custom1'][0]}/{raw['custom1'][1]})"
    )

    print()
    print(verdict_line)


if __name__ == "__main__":
    main()
