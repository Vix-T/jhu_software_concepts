"""ORM (SQLAlchemy 2.x) versions of a subset of query_data.py's analyses.

Reuses the exact same word-boundary regex patterns already defined in
query_data.py for Q8/Q9 university/program matching (imported, not
redefined), and the same Applicant model / Session from models.py. The
ORM query functions themselves (orm_q1/orm_q4/orm_q5/orm_q8/orm_q9/
orm_custom1) use no raw SQL (text()) or psycopg2 cursors anywhere --
everything is expressed with select()/where()/func/and_/or_/case().

main()'s comparison harness is the one exception: it calls
query_data.py's own raw-SQL functions (q1/q4/q5/q8/q9/custom1) live,
via a psycopg2 cursor, so the "raw-SQL" side of the comparison always
reflects the current database state instead of a hardcoded snapshot
that goes stale the next time Pull Data adds rows.
"""

import os

import psycopg2
from dotenv import load_dotenv
from sqlalchemy import and_, case, func, or_, select

import query_data
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

    load_dotenv()
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
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
        ("Q4", "Avg GPA, American, Fall 2026", round(q4_avg, 2), round(raw_q4_avg, 2), "{:.2f}"),
        ("Q5", "Fall 2025 acceptance %", round(q5_pct, 2), round(raw_q5_pct, 2), "{:.2f}"),
        ("Q8", "Fall 2026/Accepted/PhD/CS, original fields", q8_count, raw_q8_count, "{}"),
        ("Q9", "Same as Q8, llm-generated fields", q9_count, raw_q9_count, "{}"),
        ("Custom Q1", "GRE Quant contamination %", round(custom1_pct, 2), round(raw_custom1_pct, 2), "{:.2f}"),
    ]

    print("ORM vs. raw-SQL (query_data.py) comparison, both computed live in this run:\n")
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
    print(f"Q4 n={q4_n} (raw-SQL n={raw_q4_n})")
    print(f"Q5 {q5_num}/{q5_denom} (raw-SQL {raw_q5_num}/{raw_q5_denom})")
    print(f"Custom Q1 {custom1_contaminated}/{custom1_total} (raw-SQL {raw_custom1_contaminated}/{raw_custom1_total})")

    print()
    if all_match:
        print("All ORM results match the raw-SQL results from query_data.py.")
    else:
        print("MISMATCH DETECTED -- see table above. Not adjusting either query to force a match.")


if __name__ == "__main__":
    main()
