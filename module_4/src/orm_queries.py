"""ORM (SQLAlchemy 2.x) versions of query_data.py's analyses.

Reuses the exact same word-boundary regex patterns already defined in
query_data.py for Q8/Q9 university/program matching (imported, not
redefined), and the same Applicant model / session factory from models.py. The
ORM query functions themselves (orm_q1/orm_q4/orm_q5/orm_q8/orm_q9/
orm_custom1) use no raw SQL (text()) or psycopg2 cursors anywhere --
everything is expressed with select()/where()/func/and_/or_/case().

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

import query_data
from config import get_database_url
from models import Applicant, make_session_factory
from query_data import CS_PATTERN, JHU_PATTERN, NO_DATA, Q8_Q9_UNIVERSITIES
from query_data import percent_or_none as _percent


def _round2(value):
    return None if value is None else round(value, 2)


def orm_q1(session):
    """Count of Fall 2026 entries, by term."""
    stmt = select(func.count()).where(Applicant.term.ilike("Fall 2026"))
    return session.scalar(stmt)


def orm_q4(session):
    """Average GPA of American applicants who applied for Fall 2026."""
    stmt = select(func.avg(Applicant.gpa), func.count(Applicant.gpa)).where(
        Applicant.term.ilike("Fall 2026"),
        Applicant.us_or_international.ilike("American"),
        Applicant.gpa.isnot(None),
    )
    return session.execute(stmt).one()


def orm_q5(session):
    """Percentage of Fall 2025 entries that are acceptances."""
    accepted = func.sum(case((Applicant.status.ilike("Accepted"), 1), else_=0))
    stmt = select(accepted, func.count()).where(Applicant.term.ilike("Fall 2025"))
    numerator, denominator = session.execute(stmt).one()
    numerator = numerator or 0
    return numerator, denominator, _percent(numerator, denominator)


def orm_q8(session):
    """Fall 2026 + Accepted + PhD + CS, at one of 4 universities (original fields)."""
    university_clause = or_(
        *[Applicant.program.op("~*")(pattern) for _, pattern in Q8_Q9_UNIVERSITIES]
    )
    stmt = select(func.count()).where(
        and_(
            Applicant.term == "Fall 2026",
            Applicant.status == "Accepted",
            Applicant.degree == "PhD",
            Applicant.program.op("~*")(CS_PATTERN),
            university_clause,
        )
    )
    return session.scalar(stmt)


def orm_q9(session):
    """Same as Q8, but university/program matched via the LLM-generated fields."""
    university_clause = or_(
        *[
            Applicant.llm_generated_university.op("~*")(pattern)
            for _, pattern in Q8_Q9_UNIVERSITIES
        ]
    )
    stmt = select(func.count()).where(
        and_(
            Applicant.term == "Fall 2026",
            Applicant.status == "Accepted",
            Applicant.degree == "PhD",
            Applicant.llm_generated_program.op("~*")(CS_PATTERN),
            university_clause,
        )
    )
    return session.scalar(stmt)


def orm_custom1(session):
    """Percent of non-null GRE Quant entries outside the plausible [130,170] range."""
    contaminated = func.sum(
        case((~Applicant.gre.between(130, 170), 1), else_=0)
    )
    stmt = select(contaminated, func.count()).where(Applicant.gre.isnot(None))
    contaminated_count, total = session.execute(stmt).one()
    contaminated_count = contaminated_count or 0
    return contaminated_count, total, _percent(contaminated_count, total)


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
    numerator = numerator or 0
    return numerator, denominator, _percent(numerator, denominator)


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
    """Acceptance rate by degree type; ties on count are ordered alphabetically by degree."""
    accepted = func.sum(case((Applicant.status.ilike("Accepted"), 1), else_=0))
    stmt = (
        select(Applicant.degree, accepted, func.count())
        .where(Applicant.degree.isnot(None))
        .group_by(Applicant.degree)
        .order_by(func.count().desc(), Applicant.degree)
    )
    rows = session.execute(stmt).all()
    return [(degree, accepted_n, total, _percent(accepted_n, total)) for degree, accepted_n, total in rows]


APPLICANT_FIELDS = (
    "p_id",
    "program",
    "comments",
    "date_added",
    "url",
    "status",
    "term",
    "us_or_international",
    "gpa",
    "gre",
    "gre_v",
    "gre_aw",
    "degree",
    "llm_generated_program",
    "llm_generated_university",
)


def get_applicants(session, limit=None):
    """Return applicant rows as dicts keyed by the applicants-table column names.

    Rows are ordered by p_id (insertion order). `limit` caps the number
    returned; None returns every row.
    """
    stmt = select(Applicant).order_by(Applicant.p_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return [
        {field: getattr(applicant, field) for field in APPLICANT_FIELDS}
        for applicant in session.scalars(stmt)
    ]


def get_analysis(session):
    """Run every question shown on the analysis page and return the results as one dict."""
    q2_num, q2_denom, q2_pct = q2_percent_international(session)
    q4_avg, q4_n = orm_q4(session)
    q5_num, q5_denom, q5_pct = orm_q5(session)
    q6_avg, q6_n = q6_avg_gpa_accepted(session)
    q8_count = orm_q8(session)
    q9_count = orm_q9(session)
    custom1_contaminated, custom1_total, custom1_pct = orm_custom1(session)

    return {
        "q1_count": orm_q1(session),
        "q2_num": q2_num,
        "q2_denom": q2_denom,
        "q2_pct": q2_pct,
        "q3": q3_averages(session),
        "q4_avg": q4_avg,
        "q4_n": q4_n,
        "q5_num": q5_num,
        "q5_denom": q5_denom,
        "q5_pct": q5_pct,
        "q6_avg": q6_avg,
        "q6_n": q6_n,
        "q7_count": q7_jhu_masters_cs(session),
        "q8_count": q8_count,
        "q9_count": q9_count,
        "q8_q9_diff": q8_count - q9_count,
        "custom1_contaminated": custom1_contaminated,
        "custom1_total": custom1_total,
        "custom1_pct": custom1_pct,
        "custom2_rows": custom2_acceptance_by_degree(session),
    }


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
        verdict_line = "MISMATCH DETECTED -- see table above. Not adjusting either query to force a match."
    return table_lines, verdict_line


def main():
    with make_session_factory()() as session:
        q1_count = orm_q1(session)
        q4_avg, q4_n = orm_q4(session)
        q5_num, q5_denom, q5_pct = orm_q5(session)
        q8_count = orm_q8(session)
        q9_count = orm_q9(session)
        custom1_contaminated, custom1_total, custom1_pct = orm_custom1(session)

    conn = psycopg2.connect(get_database_url())
    try:
        with conn.cursor() as cur:
            raw_q1_count = query_data.q1(cur)
            raw_q4_avg, raw_q4_n = query_data.q4(cur)
            raw_q5_num, raw_q5_denom, raw_q5_pct = query_data.q5(cur)
            raw_q8_count = query_data.q8(cur)
            raw_q9_count = query_data.q9(cur)
            raw_custom1_contaminated, raw_custom1_total, raw_custom1_pct = query_data.custom1(cur)
    finally:
        conn.close()

    checks = [
        ("Q1", "Fall 2026 applicant count", q1_count, raw_q1_count, "{}"),
        ("Q4", "Avg GPA, American, Fall 2026", _round2(q4_avg), _round2(raw_q4_avg), "{:.2f}"),
        ("Q5", "Fall 2025 acceptance %", _round2(q5_pct), _round2(raw_q5_pct), "{:.2f}"),
        ("Q8", "Fall 2026/Accepted/PhD/CS, original fields", q8_count, raw_q8_count, "{}"),
        ("Q9", "Same as Q8, llm-generated fields", q9_count, raw_q9_count, "{}"),
        ("Custom Q1", "GRE Quant contamination %", _round2(custom1_pct), _round2(raw_custom1_pct), "{:.2f}"),
    ]

    table_lines, verdict_line = format_comparison(checks)

    print("ORM vs. raw-SQL (query_data.py) comparison, both computed live in this run:\n")
    for line in table_lines:
        print(line)

    print()
    print(f"Q4 n={q4_n} (raw-SQL n={raw_q4_n})")
    print(f"Q5 {q5_num}/{q5_denom} (raw-SQL {raw_q5_num}/{raw_q5_denom})")
    print(f"Custom Q1 {custom1_contaminated}/{custom1_total} (raw-SQL {raw_custom1_contaminated}/{raw_custom1_total})")

    print()
    print(verdict_line)


if __name__ == "__main__":
    main()
