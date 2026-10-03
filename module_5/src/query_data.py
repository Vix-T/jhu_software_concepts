"""Run Q7, Q8, Q9 analysis queries against the applicants table."""

import psycopg2

from config import psycopg2_dsn

# Word-boundary (\y) regex patterns, matched case-insensitively (~*).
# See conversation history / query_results write-up for the false-positive
# and false-negative checks that justify each pattern against real data.
JHU_PATTERN = r"\y(Johns? Hopkins|JHU)\y"  # also the "John Hopkins" (missing "s") typo
CS_PATTERN = r"\y(Computer Sciences?|CS)\y"
MIT_PATTERN = r"\y(MIT|Massachusetts Institute of Technology)\y"
STANFORD_PATTERN = r"\yStanford\y"
CARNEGIE_MELLON_PATTERN = r"\yCarnegie Mellon\y"  # deliberately NOT tolerant of the
                                                   # LLM's "Carnegie Melon" misspelling —
                                                   # Q9 is supposed to expose that gap.
GEORGETOWN_PATTERN = r"\yGeorgetown\y(?!\s+College\y)"  # "Georgetown" or "Georgetown University",
                                                           # but not the unrelated "Georgetown College" (KY)

Q8_Q9_UNIVERSITIES = [
    ("Georgetown", GEORGETOWN_PATTERN),
    ("MIT", MIT_PATTERN),
    ("Stanford", STANFORD_PATTERN),
    ("Carnegie Mellon", CARNEGIE_MELLON_PATTERN),
]


NO_DATA = "N/A (no data)"


def percent_or_none(numerator, denominator):
    """100 * numerator / denominator, or None when there is nothing to divide by."""
    if not denominator:
        return None
    return 100.0 * numerator / denominator


def _num(value, spec):
    """Format a number for main()'s report, or NO_DATA when there is none."""
    return NO_DATA if value is None else format(value, spec)


def _pct(value):
    return NO_DATA if value is None else f"{value:.2f}%"


def q1(cur):
    """Count of Fall 2026 entries, by term."""
    cur.execute("SELECT COUNT(*) FROM applicants WHERE term ILIKE 'Fall 2026'")
    return cur.fetchone()[0]


def q2(cur):
    """Percent international among entries with a usable nationality classification."""
    cur.execute(
        """
        SELECT
            SUM(CASE WHEN us_or_international ILIKE 'International' THEN 1 ELSE 0 END),
            COUNT(*)
        FROM applicants
        WHERE us_or_international IS NOT NULL AND TRIM(us_or_international) <> ''
        """
    )
    numerator, denominator = cur.fetchone()
    numerator = numerator or 0
    return numerator, denominator, percent_or_none(numerator, denominator)


def q3(cur):
    """Average GPA, GRE, GRE V, GRE AW, each independently over entries that provide it.

    GRE, GRE V, and GRE AW are each restricted to their plausible current-scale
    range (130-170 for GRE/GRE V, 0-6 for GRE AW) to exclude misplaced-value and
    sentinel contamination identified by inspection (see query_results_draft.md
    Q3 explanation) -- e.g. combined V+Q sums or AW scores typed into the GRE
    quant field, and placeholder values like 999 or 99.99. GPA has no such
    known contamination and is left unrestricted.
    """
    averages = {}

    cur.execute("SELECT AVG(gpa), COUNT(gpa) FROM applicants WHERE gpa IS NOT NULL")
    averages["GPA"] = cur.fetchone()

    cur.execute("SELECT AVG(gre), COUNT(gre) FROM applicants WHERE gre BETWEEN 130 AND 170")
    averages["GRE"] = cur.fetchone()

    cur.execute("SELECT AVG(gre_v), COUNT(gre_v) FROM applicants WHERE gre_v BETWEEN 130 AND 170")
    averages["GRE V"] = cur.fetchone()

    cur.execute("SELECT AVG(gre_aw), COUNT(gre_aw) FROM applicants WHERE gre_aw BETWEEN 0 AND 6")
    averages["GRE AW"] = cur.fetchone()

    return averages


def q4(cur):
    """Average GPA of American applicants who applied for Fall 2026."""
    cur.execute(
        """
        SELECT AVG(gpa), COUNT(gpa) FROM applicants
        WHERE term ILIKE 'Fall 2026'
          AND us_or_international ILIKE 'American'
          AND gpa IS NOT NULL
        """
    )
    return cur.fetchone()


def q5(cur):
    """Percentage of Fall 2025 entries that are acceptances."""
    cur.execute(
        """
        SELECT
            SUM(CASE WHEN status ILIKE 'Accepted' THEN 1 ELSE 0 END),
            COUNT(*)
        FROM applicants
        WHERE term ILIKE 'Fall 2025'
        """
    )
    numerator, denominator = cur.fetchone()
    numerator = numerator or 0
    return numerator, denominator, percent_or_none(numerator, denominator)


def q6(cur):
    """Average GPA of accepted applicants who applied for Fall 2026."""
    cur.execute(
        """
        SELECT AVG(gpa), COUNT(gpa) FROM applicants
        WHERE term ILIKE 'Fall 2026'
          AND status ILIKE 'Accepted'
          AND gpa IS NOT NULL
        """
    )
    return cur.fetchone()


def q7(cur):
    """JHU + master's degree + Computer Science, all-time, one combined count."""
    cur.execute(
        """
        SELECT COUNT(*) FROM applicants
        WHERE program ~* %s
          AND program ~* %s
          AND degree = 'Masters'
        """,
        (JHU_PATTERN, CS_PATTERN),
    )
    return cur.fetchone()[0]


def q8(cur):
    """Fall 2026 + Accepted + PhD + CS, at one of 4 universities (original fields)."""
    university_clause = " OR ".join(
        "program ~* %s" for _ in Q8_Q9_UNIVERSITIES
    )
    params = [pattern for _, pattern in Q8_Q9_UNIVERSITIES]
    cur.execute(
        f"""
        SELECT COUNT(*) FROM applicants
        WHERE term = 'Fall 2026'
          AND status = 'Accepted'
          AND degree = 'PhD'
          AND program ~* %s
          AND ({university_clause})
        """,
        [CS_PATTERN] + params,
    )
    return cur.fetchone()[0]


def q9(cur):
    """Same as Q8, but university/program matched via the LLM-generated fields."""
    university_clause = " OR ".join(
        "llm_generated_university ~* %s" for _ in Q8_Q9_UNIVERSITIES
    )
    params = [pattern for _, pattern in Q8_Q9_UNIVERSITIES]
    cur.execute(
        f"""
        SELECT COUNT(*) FROM applicants
        WHERE term = 'Fall 2026'
          AND status = 'Accepted'
          AND degree = 'PhD'
          AND llm_generated_program ~* %s
          AND ({university_clause})
        """,
        [CS_PATTERN] + params,
    )
    return cur.fetchone()[0]


def q8_q9_university_breakdown(cur):
    """Per-university sub-counts under the Q8/Q9 filters, for write-up evidence."""
    rows = []
    for name, pattern in Q8_Q9_UNIVERSITIES:
        cur.execute(
            """
            SELECT COUNT(*) FROM applicants
            WHERE term = 'Fall 2026' AND status = 'Accepted' AND degree = 'PhD'
              AND program ~* %s AND program ~* %s
            """,
            (CS_PATTERN, pattern),
        )
        orig_count = cur.fetchone()[0]

        cur.execute(
            """
            SELECT COUNT(*) FROM applicants
            WHERE term = 'Fall 2026' AND status = 'Accepted' AND degree = 'PhD'
              AND llm_generated_program ~* %s AND llm_generated_university ~* %s
            """,
            (CS_PATTERN, pattern),
        )
        llm_count = cur.fetchone()[0]

        rows.append((name, orig_count, llm_count))
    return rows


def custom1(cur):
    """Percent of non-null GRE Quant entries outside the plausible [130,170] range.

    Mirrors the BETWEEN 130 AND 170 filter already applied in Q3, but reports
    the contamination rate itself rather than filtering it out.
    """
    cur.execute(
        """
        SELECT
            SUM(CASE WHEN gre NOT BETWEEN 130 AND 170 THEN 1 ELSE 0 END),
            COUNT(*)
        FROM applicants
        WHERE gre IS NOT NULL
        """
    )
    contaminated, total = cur.fetchone()
    contaminated = contaminated or 0
    return contaminated, total, percent_or_none(contaminated, total)


def custom2(cur):
    """Acceptance rate by degree type (PhD vs. Masters, plus all other degree values).

    Ordered by entry count, largest first; degree types tied on count are
    ordered alphabetically, so the order is deterministic.
    """
    cur.execute(
        """
        SELECT
            degree,
            SUM(CASE WHEN status ILIKE 'Accepted' THEN 1 ELSE 0 END) AS accepted,
            COUNT(*) AS total
        FROM applicants
        WHERE degree IS NOT NULL
        GROUP BY degree
        ORDER BY total DESC, degree
        """
    )
    return [
        (degree, accepted, total, percent_or_none(accepted, total))
        for degree, accepted, total in cur.fetchall()
    ]


def main():
    """CLI: run every analysis question with raw SQL against the DB_* database and print the answers."""
    conn = psycopg2.connect(psycopg2_dsn())

    try:
        with conn.cursor() as cur:
            q1_count = q1(cur)
            q2_num, q2_denom, q2_pct = q2(cur)
            q3_averages = q3(cur)
            q4_avg, q4_n = q4(cur)
            q5_num, q5_denom, q5_pct = q5(cur)
            q6_avg, q6_n = q6(cur)
            q7_count = q7(cur)
            q8_count = q8(cur)
            q9_count = q9(cur)
            breakdown = q8_q9_university_breakdown(cur)
            custom1_contaminated, custom1_total, custom1_pct = custom1(cur)
            custom2_rows = custom2(cur)
    finally:
        conn.close()

    print(f"Fall 2026 applicant count: {q1_count}")
    print()
    print(f"Percent international: {_pct(q2_pct)} ({q2_num}/{q2_denom} entries with a usable classification)")
    print()
    print("Average scores (each computed over entries that provide that metric):")
    for label, (avg, count) in q3_averages.items():
        print(f"  Average {label}: {_num(avg, '.2f')} (n={count})")
    print()
    print(f"Average GPA, American applicants, Fall 2026: {_num(q4_avg, '.2f')} (n={q4_n})")
    print()
    print(f"Fall 2025 acceptance percentage: {_pct(q5_pct)} ({q5_num}/{q5_denom} entries)")
    print()
    print(f"Average GPA, accepted applicants, Fall 2026: {_num(q6_avg, '.2f')} (n={q6_n})")
    print()
    print(f"Q7 (JHU, Masters, Computer Science, all-time): {q7_count}")
    print()
    print(f"Q8 (Fall 2026, Accepted, PhD, Computer Science, original fields): {q8_count}")
    print(f"Q9 (same filters, llm_generated_program/university):             {q9_count}")
    print(f"Difference (Q8 - Q9):                                            {q8_count - q9_count}")
    print()
    print("Per-university breakdown under Q8/Q9 filters (original vs. LLM-generated fields):")
    for name, orig_count, llm_count in breakdown:
        print(f"  {name:<16} original={orig_count:<4} llm={llm_count:<4} diff={orig_count - llm_count}")
    print()
    print(
        f"Custom Q1 (GRE Quant contamination rate): {_pct(custom1_pct)} "
        f"({custom1_contaminated}/{custom1_total} entries outside the plausible 130-170 range)"
    )
    print()
    print("Custom Q2 (acceptance rate by degree type):")
    for degree, accepted, total, pct in custom2_rows:
        print(f"  {degree:<10} {pct:.2f}% ({accepted}/{total})")
    if not custom2_rows:
        print(f"  {NO_DATA}")


if __name__ == "__main__":
    main()
