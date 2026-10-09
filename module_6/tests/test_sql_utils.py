"""sql_utils.clamp_limit(): every LIMIT in the project goes through it."""

import pytest

from sql_utils import DEFAULT_LIMIT, MAX_LIMIT, MIN_LIMIT, ValidationError, clamp_limit

pytestmark = pytest.mark.db


def test_limit_constants():
    assert (MIN_LIMIT, DEFAULT_LIMIT, MAX_LIMIT) == (1, 10, 100)
    assert issubclass(ValidationError, ValueError)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_LIMIT),
        (5, 5),
        (1, 1),
        (100, 100),
        (0, 1),
        (-5, 1),
        (101, 100),
        (99999, 100),
        ("7", 7),
        ("0", 1),
        ("-5", 1),
        ("99999", 100),
        ("9" * 5000, 100),
        ("-" + "9" * 5000, 1),
    ],
)
def test_clamps_to_range(value, expected):
    assert clamp_limit(value) == expected


@pytest.mark.parametrize(
    "value",
    [True, False, 5.0, [5], "abc", "", " 5", "5 ", "1.5", "1e3", "+5", "10; DROP TABLE applicants", "true"],
)
def test_rejects_non_integers(value):
    with pytest.raises(ValidationError, match="^limit must be an integer"):
        clamp_limit(value)


def test_custom_bounds_and_default():
    assert clamp_limit(None, default=3) == 3
    assert clamp_limit(50, maximum=20) == 20
    assert clamp_limit(2, minimum=5) == 5
