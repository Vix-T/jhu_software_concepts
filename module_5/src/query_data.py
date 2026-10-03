"""Run the analysis questions (Q1-Q9 + 2 custom) with raw SQL against the applicants table.

Every statement is a psycopg2.sql composed object built by a *_query()
function that returns (stmt, params); the q*() functions only execute what
the builder returns. The table name is sql_utils.APPLICANTS (an
sql.Identifier), runtime-chosen column names are sql.Identifier, every value
-- including the fixed filter values below -- is a bound parameter, and every
SELECT ends in LIMIT %s with a clamp_limit()-ed value.
"""

import psycopg2
from psycopg2 import sql

from config import psycopg2_dsn
from sql_utils import APPLICANTS, MAX_LIMIT, SINGLE_ROW, clamp_limit

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

# Fixed filter values, passed as bound parameters (never written into SQL text).
FALL_2026 = "Fall 2026"
FALL_2025 = "Fall 2025"
ACCEPTED = "Accepted"
INTERNATIONAL = "International"
AMERICAN = "American"
MASTERS = "Masters"
PHD = "PhD"
GRE_MIN, GRE_MAX = 130, 170  # plausible GRE / GRE V range
GRE_AW_MIN, GRE_AW_MAX = 0, 6  # plausible GRE AW range

# (score column, low, high) for Q3: GPA is unrestricted (see q3's docstring).
Q3_METRICS = [
    ("GPA", "gpa", None),
    ("GRE", "gre", (GRE_MIN, GRE_MAX)),
    ("GRE V", "gre_v", (GRE_MIN, GRE_MAX)),
    ("GRE AW", "gre_aw", (GRE_AW_MIN, GRE_AW_MAX)),
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


def _one_row_limit():
    return clamp_limit(SINGLE_ROW)


# ---------------------------------------------------------------------------
# Query builders: each returns (stmt, params); nothing here touches a cursor.
# ---------------------------------------------------------------------------


def q1_query():
    """Count of Fall 2026 entries, by term."""
    stmt = sql.SQL("SELECT COUNT(*) FROM {} WHERE term ILIKE %s LIMIT %s").format(APPLICANTS)
    return stmt, [FALL_2026, _one_row_limit()]


def q2_query():
    """International count and usable-classification count."""
    stmt = sql.SQL(
        """
        SELECT
            SUM(CASE WHEN us_or_international ILIKE %s THEN 1 ELSE 0 END),
            COUNT(*)
        FROM {}
        WHERE us_or_international IS NOT NULL AND TRIM(us_or_international) <> %s
        LIMIT %s
        """
    ).format(APPLICANTS)
    return stmt, [INTERNATIONAL, "", _one_row_limit()]


def q3_query(column, value_range):
    """AVG and COUNT of one score column, optionally restricted to [low, high]."""
    if value_range is None:
        stmt = sql.SQL("SELECT AVG({col}), COUNT({col}) FROM {table} WHERE {col} IS NOT NULL LIMIT %s").format(
            col=sql.Identifier(column), table=APPLICANTS
        )
        return stmt, [_one_row_limit()]
    stmt = sql.SQL(
        "SELECT AVG({col}), COUNT({col}) FROM {table} WHERE {col} BETWEEN %s AND %s LIMIT %s"
    ).format(col=sql.Identifier(column), table=APPLICANTS)
    low, high = value_range
    return stmt, [low, high, _one_row_limit()]


def q4_query():
    """Average GPA of American applicants for Fall 2026."""
    stmt = sql.SQL(
        """
        SELECT AVG(gpa), COUNT(gpa) FROM {}
        WHERE term ILIKE %s
          AND us_or_international ILIKE %s
          AND gpa IS NOT NULL
        LIMIT %s
        """
    ).format(APPLICANTS)
    return stmt, [FALL_2026, AMERICAN, _one_row_limit()]


def q5_query():
    """Accepted count and total count for Fall 2025."""
    stmt = sql.SQL(
        """
        SELECT
            SUM(CASE WHEN status ILIKE %s THEN 1 ELSE 0 END),
            COUNT(*)
        FROM {}
        WHERE term ILIKE %s
        LIMIT %s
        """
    ).format(APPLICANTS)
    return stmt, [ACCEPTED, FALL_2025, _one_row_limit()]


def q6_query():
    """Average GPA of accepted applicants for Fall 2026."""
    stmt = sql.SQL(
        """
        SELECT AVG(gpa), COUNT(gpa) FROM {}
        WHERE term ILIKE %s
          AND status ILIKE %s
          AND gpa IS NOT NULL
        LIMIT %s
        """
    ).format(APPLICANTS)
    return stmt, [FALL_2026, ACCEPTED, _one_row_limit()]


def q7_query():
    """JHU + Masters + Computer Science, all-time."""
    stmt = sql.SQL(
        """
        SELECT COUNT(*) FROM {}
        WHERE program ~* %s
          AND program ~* %s
          AND degree = %s
        LIMIT %s
        """
    ).format(APPLICANTS)
    return stmt, [JHU_PATTERN, CS_PATTERN, MASTERS, _one_row_limit()]


def q8_q9_query(program_column, university_column):
    """Fall 2026 + Accepted + PhD + CS at any Q8/Q9 university, matched on the given columns.

    The OR clause has one "<university_column> ~* %s" per university, joined
    with sql.SQL(" OR "); the patterns themselves are bound parameters.
    """
    university_clause = sql.SQL(" OR ").join(
        sql.SQL("{} ~* %s").format(sql.Identifier(university_column)) for _ in Q8_Q9_UNIVERSITIES
    )
    stmt = sql.SQL(
        """
        SELECT COUNT(*) FROM {table}
        WHERE term = %s
          AND status = %s
          AND degree = %s
          AND {program} ~* %s
          AND ({universities})
        LIMIT %s
        """
    ).format(table=APPLICANTS, program=sql.Identifier(program_column), universities=university_clause)
    params = [FALL_2026, ACCEPTED, PHD, CS_PATTERN]
    params += [pattern for _, pattern in Q8_Q9_UNIVERSITIES]
    params.append(_one_row_limit())
    return stmt, params


def breakdown_query(program_column, university_column, pattern):
    """Q8/Q9 filters for a single university pattern, matched on the given columns."""
    stmt = sql.SQL(
        """
        SELECT COUNT(*) FROM {table}
        WHERE term = %s AND status = %s AND degree = %s
          AND {program} ~* %s AND {university} ~* %s
        LIMIT %s
        """
    ).format(
        table=APPLICANTS,
        program=sql.Identifier(program_column),
        university=sql.Identifier(university_column),
    )
    return stmt, [FALL_2026, ACCEPTED, PHD, CS_PATTERN, pattern, _one_row_limit()]


def custom1_query():
    """Out-of-range and total counts of non-null GRE Quant scores."""
    stmt = sql.SQL(
        """
        SELECT
            SUM(CASE WHEN gre NOT BETWEEN %s AND %s THEN 1 ELSE 0 END),
            COUNT(*)
        FROM {}
        WHERE gre IS NOT NULL
        LIMIT %s
        """
    ).format(APPLICANTS)
    return stmt, [GRE_MIN, GRE_MAX, _one_row_limit()]


def custom2_query():
    """Accepted and total counts per degree type, largest first, ties alphabetical.

    One row per distinct degree value; LIMIT is MAX_LIMIT (the data has 8
    degree types), so more than MAX_LIMIT distinct values would be truncated.
    """
    stmt = sql.SQL(
        """
        SELECT
            degree,
            SUM(CASE WHEN status ILIKE %s THEN 1 ELSE 0 END) AS accepted,
            COUNT(*) AS total
        FROM {}
        WHERE degree IS NOT NULL
        GROUP BY degree
        ORDER BY total DESC, degree
        LIMIT %s
        """
    ).format(APPLICANTS)
    return stmt, [ACCEPTED, clamp_limit(MAX_LIMIT)]


# ---------------------------------------------------------------------------
# Execution: run a builder's (stmt, params) and shape the result.
# ---------------------------------------------------------------------------


def q1(cur):
    """Count of Fall 2026 entries, by term."""
    stmt, params = q1_query()
    cur.execute(stmt, params)
    return cur.fetchone()[0]


def q2(cur):
    """Percent international among entries with a usable nationality classification."""
    stmt, params = q2_query()
    cur.execute(stmt, params)
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
    for label, column, value_range in Q3_METRICS:
        stmt, params = q3_query(column, value_range)
        cur.execute(stmt, params)
        averages[label] = cur.fetchone()
    return averages


def q4(cur):
    """Average GPA of American applicants who applied for Fall 2026."""
    stmt, params = q4_query()
    cur.execute(stmt, params)
    return cur.fetchone()


def q5(cur):
    """Percentage of Fall 2025 entries that are acceptances."""
    stmt, params = q5_query()
    cur.execute(stmt, params)
    numerator, denominator = cur.fetchone()
    numerator = numerator or 0
    return numerator, denominator, percent_or_none(numerator, denominator)


def q6(cur):
    """Average GPA of accepted applicants who applied for Fall 2026."""
    stmt, params = q6_query()
    cur.execute(stmt, params)
    return cur.fetchone()


def q7(cur):
    """JHU + master's degree + Computer Science, all-time, one combined count."""
    stmt, params = q7_query()
    cur.execute(stmt, params)
    return cur.fetchone()[0]


def q8(cur):
    """Fall 2026 + Accepted + PhD + CS, at one of 4 universities (original fields)."""
    stmt, params = q8_q9_query("program", "program")
    cur.execute(stmt, params)
    return cur.fetchone()[0]


def q9(cur):
    """Same as Q8, but university/program matched via the LLM-generated fields."""
    stmt, params = q8_q9_query("llm_generated_program", "llm_generated_university")
    cur.execute(stmt, params)
    return cur.fetchone()[0]


def q8_q9_university_breakdown(cur):
    """Per-university sub-counts under the Q8/Q9 filters, for write-up evidence."""
    rows = []
    for name, pattern in Q8_Q9_UNIVERSITIES:
        stmt, params = breakdown_query("program", "program", pattern)
        cur.execute(stmt, params)
        orig_count = cur.fetchone()[0]

        stmt, params = breakdown_query("llm_generated_program", "llm_generated_university", pattern)
        cur.execute(stmt, params)
        llm_count = cur.fetchone()[0]

        rows.append((name, orig_count, llm_count))
    return rows


def custom1(cur):
    """Percent of non-null GRE Quant entries outside the plausible [130,170] range.

    Mirrors the BETWEEN 130 AND 170 filter already applied in Q3, but reports
    the contamination rate itself rather than filtering it out.
    """
    stmt, params = custom1_query()
    cur.execute(stmt, params)
    contaminated, total = cur.fetchone()
    contaminated = contaminated or 0
    return contaminated, total, percent_or_none(contaminated, total)


def custom2(cur):
    """Acceptance rate by degree type (PhD vs. Masters, plus all other degree values).

    Ordered by entry count, largest first; degree types tied on count are
    ordered alphabetically, so the order is deterministic.
    """
    stmt, params = custom2_query()
    cur.execute(stmt, params)
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
