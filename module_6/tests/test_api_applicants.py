"""GET /api/applicants: validated limit/sort/order/university, injection attempts, DB-down handling."""

import socket

import pytest
from conftest import make_record, make_records

from app import create_app
from app.applicant_search import RESULT_COLUMNS

pytestmark = [pytest.mark.web, pytest.mark.db]

UNIVERSITIES = [
    "Stanford University",
    "Stanford University",
    "Stanford University",
    "Fifty%State University",
    "Test_U",
    "TestXU",
    "Georgetown University",
]
DATES = ["Jan 05, 2026", "Mar 10, 2026", "Feb 01, 2026", "Dec 31, 2025", "Jul 04, 2026", "Jun 15, 2026", None]
GPAS = ["3.10", "3.95", "3.50", "2.80", "3.70", "3.20", None]

INJECTION_REQUESTS = [
    {"limit": "10; DROP TABLE applicants"},
    {"limit": "1 OR 1=1"},
    {"sort": "date_added; DROP TABLE applicants"},
    {"sort": "url--"},
    {"sort": "1"},
    {"order": "asc; DROP TABLE applicants"},
    {"university": "' OR '1'='1"},
    {"university": "x'; DROP TABLE applicants; --"},
    {"university": "%' ; TRUNCATE applicants; --"},
]


@pytest.fixture
def api(make_app):
    return make_app().test_client()


@pytest.fixture
def seeded(seed):
    records = [
        make_record(i, **{"llm-generated-university": university, "Date Added": date, "GPA": gpa})
        for i, (university, date, gpa) in enumerate(zip(UNIVERSITIES, DATES, GPAS))
    ]
    assert seed(records) == (len(records), 0, [])
    return records


def _get(api, **params):
    response = api.get("/api/applicants", query_string=params)
    return response.status_code, response.get_json()


def _universities(body):
    return [row["llm_generated_university"] for row in body["rows"]]


def test_default_request_returns_fixed_columns_in_p_id_order(api, seeded):
    status, body = _get(api)

    assert status == 200
    assert (body["count"], body["limit"], body["sort"], body["order"]) == (len(seeded), 10, "p_id", "asc")
    assert [row["p_id"] for row in body["rows"]] == list(range(1, len(seeded) + 1))
    for row in body["rows"]:
        assert set(row) == set(RESULT_COLUMNS)
        assert "comments" not in row
    assert body["rows"][0]["date_added"] == "2026-01-05"
    assert body["rows"][0]["url"] == seeded[0]["URL"]


@pytest.mark.parametrize(("limit", "expected_rows"), [("-5", 1), ("0", 1), ("1", 1), ("3", 3)])
def test_small_and_negative_limits_clamp_to_at_least_one(api, seeded, limit, expected_rows):
    status, body = _get(api, limit=limit)

    assert status == 200
    assert body["count"] == len(body["rows"]) == expected_rows
    assert body["limit"] == expected_rows


def test_huge_limit_is_capped_at_100(api, seed):
    seed(make_records(105))

    status, body = _get(api, limit="99999")

    assert status == 200
    assert body["limit"] == 100
    assert len(body["rows"]) == 100


@pytest.mark.parametrize("limit", ["abc", "10; DROP TABLE applicants", "true", "1.5", " 5", ""])
def test_non_integer_limit_is_400(api, seeded, limit):
    status, body = _get(api, limit=limit)

    assert status == 400
    assert body == {"error": "limit must be an integer"}


@pytest.mark.parametrize("sort", ["date_added; DROP TABLE applicants", "1", "url--", "comments", "", "P_ID"])
def test_unknown_sort_is_400(api, seeded, sort):
    status, body = _get(api, sort=sort)

    assert status == 400
    assert body == {"error": "sort must be one of: p_id, date_added, gpa, gre, university, program"}


@pytest.mark.parametrize("order", ["asc; DROP TABLE applicants", "up", "ASC", ""])
def test_unknown_order_is_400(api, seeded, order):
    status, body = _get(api, order=order)

    assert status == 400
    assert body == {"error": "order must be 'asc' or 'desc'"}


def test_sort_by_date_descending_puts_nulls_last(api, seeded):
    status, body = _get(api, sort="date_added", order="desc")

    assert status == 200
    dates = [row["date_added"] for row in body["rows"]]
    assert dates == ["2026-07-04", "2026-06-15", "2026-03-10", "2026-02-01", "2026-01-05", "2025-12-31", None]


def test_sort_by_gpa_ascending(api, seeded):
    status, body = _get(api, sort="gpa", order="asc", limit="3")

    assert status == 200
    assert [row["gpa"] for row in body["rows"]] == [2.8, 3.1, 3.2]


@pytest.mark.parametrize(
    ("university", "expected"),
    [
        ("' OR '1'='1", []),
        ("x'; DROP TABLE applicants; --", []),
        ("%", ["Fifty%State University"]),
        ("_", ["Test_U"]),
        ("test_u", ["Test_U"]),
        ("stanford", ["Stanford University"] * 3),
        ("GEORGETOWN", ["Georgetown University"]),
    ],
)
def test_university_filter_matches_literally(api, seeded, university, expected):
    status, body = _get(api, university=university)

    assert status == 200
    assert _universities(body) == expected


def test_blank_university_means_no_filter(api, seeded):
    status, body = _get(api, university="   ")

    assert status == 200
    assert body["count"] == len(seeded)


def test_overlong_university_is_400(api, seeded):
    status, body = _get(api, university="a" * 101)

    assert status == 400
    assert body == {"error": "university must be at most 100 characters"}


def test_table_survives_injection_attempts(api, seeded, db_conn, row_count):
    before = row_count()

    statuses = [_get(api, **params)[0] for params in INJECTION_REQUESTS]

    assert statuses == [400, 400, 400, 400, 400, 400, 200, 200, 200]
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('applicants')")
        assert cur.fetchone()[0] == "applicants"
    assert row_count() == before == len(seeded)


def test_missing_table_is_503_and_web_does_not_create_it(make_app, db_conn, caplog):
    with db_conn, db_conn.cursor() as cur:
        cur.execute("DROP TABLE applicants")

    status, body = _get(make_app().test_client())

    assert status == 503
    assert body == {"error": "database initializing"}
    assert "Applicant search: database initializing" in caplog.text
    with db_conn, db_conn.cursor() as cur:  # the web never runs DDL: the worker creates tables
        cur.execute("SELECT to_regclass('applicants')")
        assert cur.fetchone()[0] is None


@pytest.fixture
def unreachable_api():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    app = create_app(
        {"DATABASE_URL": f"postgresql://nobody@127.0.0.1:{closed_port}/unreachable_test", "TESTING": True}
    )
    return app.test_client()


def test_database_down_is_503_json(unreachable_api):
    response = unreachable_api.get("/api/applicants", query_string={"limit": "5"})

    assert response.status_code == 503
    assert response.get_json() == {"error": "database unavailable"}
    assert b"Traceback" not in response.data


def test_validation_runs_before_any_database_access(unreachable_api):
    response = unreachable_api.get("/api/applicants", query_string={"limit": "abc"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "limit must be an integer"}


@pytest.mark.parametrize(
    ("limit", "expected"),
    [("99999999999", 100), ("-99999999999", 1), ("0000000000005", 100), ("9" * 5000, 100)],
)
def test_limits_too_long_to_parse_clamp_by_sign(api, seed, limit, expected):
    seed(make_records(105))

    status, body = _get(api, limit=limit)

    assert status == 200
    assert body["limit"] == expected
    assert len(body["rows"]) == expected
