"""User-driven applicant search behind GET /api/applicants.

Every piece of user input is validated before any SQL exists:

- limit: parse_limit() -> an int in [1, 100], bound as a parameter (never inlined).
- sort: looked up in SORT_COLUMNS; only the mapped, fixed column name ever
  becomes an sql.Identifier. Anything else is a ValidationError.
- order: exactly "asc" or "desc", mapped to fixed sql.SQL fragments.
- university: a bound ILIKE parameter, with \\, % and _ escaped so the
  user's text only ever matches literally (a lone "%" can't match everything).

build_applicants_query() only builds (stmt, params); run_applicants_query()
executes them on a read-only connection.
"""

import re

from psycopg2 import sql

from app.db import connect

# The fields returned to the client: a fixed list, never SELECT * (the
# free-text comments column is deliberately left out).
RESULT_COLUMNS = (
    "p_id", "program", "date_added", "url", "status", "term", "us_or_international",
    "gpa", "gre", "gre_v", "gre_aw", "degree", "llm_generated_program", "llm_generated_university",
)

# Public sort key -> table column. Only these values are accepted.
SORT_COLUMNS = {
    "p_id": "p_id",
    "date_added": "date_added",
    "gpa": "gpa",
    "gre": "gre",
    "university": "llm_generated_university",
    "program": "llm_generated_program",
}
ORDER_SQL = {"asc": sql.SQL("ASC"), "desc": sql.SQL("DESC")}
DEFAULT_SORT = "p_id"
DEFAULT_ORDER = "asc"

DEFAULT_LIMIT = 10
LIMIT_RANGE = range(1, 101)  # 1..100 rows per request
# Longer digit strings are far outside LIMIT_RANGE; int() also refuses huge ones.
MAX_LIMIT_DIGITS = 9
LIMIT_TEXT = re.compile(r"(-?)(\d+)")

UNIVERSITY_COLUMN = "llm_generated_university"
MAX_FILTER_LENGTH = 100
LIKE_ESCAPE = "\\"
LIKE_WILDCARD = "%"


class ValidationError(ValueError):
    """A query parameter (limit, sort, order, university) is invalid."""


def parse_limit(text):
    """The `limit` query parameter as a row count in LIMIT_RANGE.

    None (no parameter) means DEFAULT_LIMIT. Out-of-range integers are
    clamped to the nearest end of the range. Anything that isn't an optional
    "-" followed by digits (including surrounding whitespace) is a
    ValidationError.
    """
    if text is None:
        return DEFAULT_LIMIT
    match = LIMIT_TEXT.fullmatch(text)
    if match is None:
        raise ValidationError("limit must be an integer")
    negative, digits = match.groups()
    if len(digits) > MAX_LIMIT_DIGITS:  # far outside the range: its sign decides the end
        return LIMIT_RANGE.start if negative else LIMIT_RANGE[-1]
    return max(LIMIT_RANGE.start, min(int(text), LIMIT_RANGE[-1]))


def escape_like(text):
    """Escape LIKE/ILIKE metacharacters so `text` matches only literally."""
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def build_applicants_query(
    limit=DEFAULT_LIMIT, sort=DEFAULT_SORT, order=DEFAULT_ORDER, university=None
):
    """Validate the search parameters and return (stmt, params).

    limit is a parse_limit() result. Raises ValidationError for a sort key
    outside SORT_COLUMNS, an order other than "asc"/"desc", or a university
    filter longer than MAX_FILTER_LENGTH. A blank university means no filter.
    """
    if sort not in SORT_COLUMNS:
        raise ValidationError(f"sort must be one of: {', '.join(SORT_COLUMNS)}")
    if order not in ORDER_SQL:
        raise ValidationError("order must be 'asc' or 'desc'")

    params = []
    where = sql.SQL("")
    if university is not None and university.strip():
        if len(university) > MAX_FILTER_LENGTH:
            raise ValidationError(f"university must be at most {MAX_FILTER_LENGTH} characters")
        where = sql.SQL("WHERE {} ILIKE %s ESCAPE %s").format(sql.Identifier(UNIVERSITY_COLUMN))
        pattern = "".join((LIKE_WILDCARD, escape_like(university.strip()), LIKE_WILDCARD))
        params += [pattern, LIKE_ESCAPE]

    stmt = sql.SQL(
        "SELECT {columns} FROM {table} {where} "
        "ORDER BY {sort} {order} NULLS LAST, {tiebreak} LIMIT %s"
    ).format(
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in RESULT_COLUMNS),
        table=sql.Identifier("applicants"),
        where=where,
        sort=sql.Identifier(SORT_COLUMNS[sort]),
        order=ORDER_SQL[order],
        tiebreak=sql.Identifier("p_id"),
    )
    params.append(limit)
    return stmt, params


def run_applicants_query(stmt, params, database_url):
    """Execute a build_applicants_query() result on a read-only connection.

    Returns a list of dicts keyed by RESULT_COLUMNS, with dates as ISO strings.
    """
    conn = connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            column: value.isoformat() if column == "date_added" and value is not None else value
            for column, value in zip(RESULT_COLUMNS, row)
        }
        for row in rows
    ]
