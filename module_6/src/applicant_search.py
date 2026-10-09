"""User-driven applicant search behind GET /api/applicants.

Every piece of user input is validated before any SQL exists:

- limit: clamp_limit() -> bound parameter (never inlined).
- sort: looked up in SORT_COLUMNS; only the mapped, fixed column name ever
  becomes an sql.Identifier. Anything else is a ValidationError.
- order: exactly "asc" or "desc", mapped to fixed sql.SQL fragments.
- university: a bound ILIKE parameter, with \\, % and _ escaped so the
  user's text only ever matches literally (a lone "%" can't match everything).

build_applicants_query() only builds (stmt, params); run_applicants_query()
executes them on a read-only connection.
"""

from psycopg2 import sql

import load_data
from sql_utils import APPLICANT_COLUMNS, APPLICANTS, ValidationError, clamp_limit

# Columns returned to the client: a fixed list, never SELECT * (the
# free-text comments column is deliberately left out).
RESULT_COLUMNS = tuple(column for column in APPLICANT_COLUMNS if column != "comments")

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

UNIVERSITY_COLUMN = "llm_generated_university"
MAX_FILTER_LENGTH = 100
LIKE_ESCAPE = "\\"
LIKE_WILDCARD = "%"


def escape_like(text):
    """Escape LIKE/ILIKE metacharacters so `text` matches only literally."""
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def build_applicants_query(limit=None, sort=DEFAULT_SORT, order=DEFAULT_ORDER, university=None):
    """Validate the search parameters and return (stmt, params).

    Raises ValidationError for a non-integer limit, a sort key outside
    SORT_COLUMNS, an order other than "asc"/"desc", or a university filter
    longer than MAX_FILTER_LENGTH. A blank university means no filter.
    """
    row_limit = clamp_limit(limit)
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
        table=APPLICANTS,
        where=where,
        sort=sql.Identifier(SORT_COLUMNS[sort]),
        order=ORDER_SQL[order],
        tiebreak=sql.Identifier("p_id"),
    )
    params.append(row_limit)
    return stmt, params


def run_applicants_query(stmt, params, database_url=None):
    """Execute a build_applicants_query() result on a read-only connection.

    Returns a list of dicts keyed by RESULT_COLUMNS, with dates as ISO strings.
    """
    conn = load_data.connect(database_url)
    try:
        conn.set_session(readonly=True)
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
