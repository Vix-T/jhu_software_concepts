"""ORM (SQLAlchemy 2.x) versions of a subset of query_data.py's analyses.

Reuses the exact same word-boundary regex patterns already defined in
query_data.py for Q8/Q9 university/program matching (imported, not
redefined), and the same Applicant model / Session from models.py. No
raw SQL (text()) or psycopg2 cursors are used anywhere -- everything is
expressed with select()/where()/func/and_/or_/case().
"""

from sqlalchemy import and_, case, func, or_, select

from models import Applicant, Session
from query_data import CS_PATTERN, Q8_Q9_UNIVERSITIES


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
    return numerator, denominator, 100.0 * numerator / denominator


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
    return contaminated_count, total, 100.0 * contaminated_count / total


def main():
    with Session() as session:
        q1_count = orm_q1(session)
        q4_avg, q4_n = orm_q4(session)
        q5_num, q5_denom, q5_pct = orm_q5(session)
        q8_count = orm_q8(session)
        q9_count = orm_q9(session)
        custom1_contaminated, custom1_total, custom1_pct = orm_custom1(session)

    checks = [
        ("Q1", "Fall 2026 applicant count", q1_count, 32344, "{}"),
        ("Q4", "Avg GPA, American, Fall 2026", round(q4_avg, 2), 3.79, "{:.2f}"),
        ("Q5", "Fall 2025 acceptance %", round(q5_pct, 2), 39.30, "{:.2f}"),
        ("Q8", "Fall 2026/Accepted/PhD/CS, original fields", q8_count, 30, "{}"),
        ("Q9", "Same as Q8, llm-generated fields", q9_count, 26, "{}"),
        ("Custom Q1", "GRE Quant contamination %", round(custom1_pct, 2), 60.99, "{:.2f}"),
    ]

    print("ORM vs. raw-SQL (query_data.py) comparison:\n")
    all_match = True
    for label, description, orm_value, raw_value, fmt in checks:
        match = orm_value == raw_value
        all_match = all_match and match
        status = "MATCH" if match else "MISMATCH"
        print(
            f"{label:<10} {description:<45} "
            f"ORM={fmt.format(orm_value):<10} raw-SQL={fmt.format(raw_value):<10} [{status}]"
        )

    print()
    print(f"Q4 n={q4_n} (raw-SQL n=11396)")
    print(f"Q5 {q5_num}/{q5_denom} (raw-SQL 10641/27074)")
    print(f"Custom Q1 {custom1_contaminated}/{custom1_total} (raw-SQL 2842/4660)")

    print()
    if all_match:
        print("All ORM results match the raw-SQL results from query_data.py.")
    else:
        print("MISMATCH DETECTED -- see table above. Not adjusting either query to force a match.")


if __name__ == "__main__":
    main()
