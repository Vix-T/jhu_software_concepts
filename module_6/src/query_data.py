"""Run the analysis questions (Q1-Q9 + 2 custom) with raw SQL against the applicants table.

Every statement is a psycopg2.sql composed object built by a ``*_query()``
function that returns (stmt, params); the q*() functions only execute what
the builder returns. The table name is sql_utils.APPLICANTS (an
sql.Identifier), runtime-chosen column names are sql.Identifier, every value
-- including the fixed filter values below -- is a bound parameter, and every
SELECT ends in LIMIT %s with a clamp_limit()-ed value.

refresh_summary() runs every question and stores the answers, shaped for
the web page, in the analysis_summary table (load_data.store_summary).
"""

from psycopg2 import sql

import load_data
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
# "Georgetown" or "Georgetown University", but not the unrelated "Georgetown College" (KY).
GEORGETOWN_PATTERN = r"\yGeorgetown\y(?!\s+College\y)"

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
        stmt = sql.SQL(
            "SELECT AVG({col}), COUNT({col}) FROM {table} WHERE {col} IS NOT NULL LIMIT %s"
        ).format(col=sql.Identifier(column), table=APPLICANTS)
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
    ).format(
        table=APPLICANTS,
        program=sql.Identifier(program_column),
        universities=university_clause,
    )
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




# Every question main() reports: name -> the q*() function answering it.
REPORT_QUESTIONS = {
    "q1": q1,
    "q2": q2,
    "q3": q3,
    "q4": q4,
    "q5": q5,
    "q6": q6,
    "q7": q7,
    "q8": q8,
    "q9": q9,
    "breakdown": q8_q9_university_breakdown,
    "custom1": custom1,
    "custom2": custom2,
}


def collect_answers(cur):
    """Run every REPORT_QUESTIONS query on `cur`, keyed by question name."""
    return {name: question(cur) for name, question in REPORT_QUESTIONS.items()}


def summary_from_answers(answers):
    """The values the analysis page renders, keyed by template name, from collect_answers()."""
    summary = {"q1_count": answers["q1"]}
    summary["q2_num"], summary["q2_denom"], summary["q2_pct"] = answers["q2"]
    summary["q3"] = answers["q3"]
    summary["q4_avg"], summary["q4_n"] = answers["q4"]
    summary["q5_num"], summary["q5_denom"], summary["q5_pct"] = answers["q5"]
    summary["q6_avg"], summary["q6_n"] = answers["q6"]
    summary["q7_count"] = answers["q7"]
    summary["q8_count"] = answers["q8"]
    summary["q9_count"] = answers["q9"]
    summary["q8_q9_diff"] = answers["q8"] - answers["q9"]
    (
        summary["custom1_contaminated"],
        summary["custom1_total"],
        summary["custom1_pct"],
    ) = answers["custom1"]
    summary["custom2_rows"] = answers["custom2"]
    return summary


def row_count_query():
    """Total applicants rows (one aggregate row)."""
    stmt = sql.SQL("SELECT COUNT(*) FROM {} LIMIT %s").format(APPLICANTS)
    return stmt, [_one_row_limit()]


def refresh_summary(cur):
    """Recompute every answer and replace the stored analysis_summary row (no commit).

    Returns the stored summary dict.
    """
    summary = summary_from_answers(collect_answers(cur))
    cur.execute(*row_count_query())
    load_data.store_summary(cur, summary, cur.fetchone()[0])
    return summary


def _print_overview(answers):
    """Q1-Q6: counts, percentages and averages."""
    q2_num, q2_denom, q2_pct = answers["q2"]
    q4_avg, q4_n = answers["q4"]
    q5_num, q5_denom, q5_pct = answers["q5"]
    q6_avg, q6_n = answers["q6"]

    print(f"Fall 2026 applicant count: {answers['q1']}")
    print()
    print(
        f"Percent international: {_pct(q2_pct)} "
        f"({q2_num}/{q2_denom} entries with a usable classification)"
    )
    print()
    print("Average scores (each computed over entries that provide that metric):")
    for label, (avg, count) in answers["q3"].items():
        print(f"  Average {label}: {_num(avg, '.2f')} (n={count})")
    print()
    print(f"Average GPA, American applicants, Fall 2026: {_num(q4_avg, '.2f')} (n={q4_n})")
    print()
    print(f"Fall 2025 acceptance percentage: {_pct(q5_pct)} ({q5_num}/{q5_denom} entries)")
    print()
    print(f"Average GPA, accepted applicants, Fall 2026: {_num(q6_avg, '.2f')} (n={q6_n})")
    print()


def _print_program_counts(answers):
    """Q7, Q8/Q9 and the per-university breakdown."""
    q8_count, q9_count = answers["q8"], answers["q9"]
    print(f"Q7 (JHU, Masters, Computer Science, all-time): {answers['q7']}")
    print()
    print(f"Q8 (Fall 2026, Accepted, PhD, Computer Science, original fields): {q8_count}")
    print(f"Q9 (same filters, llm_generated_program/university):             {q9_count}")
    print(f"Difference (Q8 - Q9):                                            {q8_count - q9_count}")
    print()
    print("Per-university breakdown under Q8/Q9 filters (original vs. LLM-generated fields):")
    for name, orig_count, llm_count in answers["breakdown"]:
        print(
            f"  {name:<16} original={orig_count:<4} llm={llm_count:<4} "
            f"diff={orig_count - llm_count}"
        )
    print()


def _print_custom(answers):
    """The two custom questions."""
    contaminated, total, pct = answers["custom1"]
    print(
        f"Custom Q1 (GRE Quant contamination rate): {_pct(pct)} "
        f"({contaminated}/{total} entries outside the plausible 130-170 range)"
    )
    print()
    print("Custom Q2 (acceptance rate by degree type):")
    for degree, accepted, degree_total, degree_pct in answers["custom2"]:
        print(f"  {degree:<10} {degree_pct:.2f}% ({accepted}/{degree_total})")
    if not answers["custom2"]:
        print(f"  {NO_DATA}")


def print_report(answers):
    """Print collect_answers()'s results as main()'s plain-text report."""
    _print_overview(answers)
    _print_program_counts(answers)
    _print_custom(answers)


def main():
    """CLI: run every analysis question with raw SQL on $DATABASE_URL and print the answers."""
    conn = load_data.connect()
    try:
        with conn.cursor() as cur:
            answers = collect_answers(cur)
    finally:
        conn.close()
    print_report(answers)


if __name__ == "__main__":
    main()
