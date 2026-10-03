"""Shared pieces for safe SQL: the table identifier and LIMIT clamping.

Every raw-SQL statement in this project is a psycopg2.sql composed object:
table and runtime-chosen column names go in as sql.Identifier, values go in
only as bound parameters (%s / sql.Placeholder), and every SELECT ends in a
LIMIT whose value is a bound parameter that has been through clamp_limit().
"""

import re

from psycopg2 import sql

APPLICANTS = sql.Identifier("applicants")
# The applicants table's columns, in table order (see load_data.CREATE_TABLE_SQL).
APPLICANT_COLUMNS = (
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

MIN_LIMIT = 1
DEFAULT_LIMIT = 10
MAX_LIMIT = 100
SINGLE_ROW = 1  # aggregate queries (COUNT/AVG/SUM) return exactly one row

_INTEGER_TEXT = re.compile(r"-?\d+")
# int() refuses strings over ~4300 digits; anything longer than this is
# already far outside [minimum, maximum], so it's clamped by its sign alone.
_MAX_PARSED_DIGITS = 9


class ValidationError(ValueError):
    """A user-supplied query parameter (limit, sort, order, filter) is invalid."""


def clamp_limit(value, default=DEFAULT_LIMIT, minimum=MIN_LIMIT, maximum=MAX_LIMIT):
    """Return `value` as an int clamped to [minimum, maximum].

    None means `default`. Accepts an int or a string of decimal digits with
    an optional leading "-"; booleans, floats, and any other text (including
    surrounding whitespace) raise ValidationError.
    """
    if value is None:
        number = default
    elif isinstance(value, bool):
        raise ValidationError("limit must be an integer, not a boolean")
    elif isinstance(value, int):
        number = value
    elif isinstance(value, str) and _INTEGER_TEXT.fullmatch(value):
        digits = value.lstrip("-")
        if len(digits) > _MAX_PARSED_DIGITS:
            number = minimum if value.startswith("-") else maximum
        else:
            number = int(value)
    else:
        raise ValidationError("limit must be an integer")
    return max(minimum, min(maximum, number))
