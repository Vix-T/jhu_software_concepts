"""Rendered analysis output: "Answer:" labels and two-decimal percentages."""

import re

import pytest
from bs4 import BeautifulSoup
from conftest import make_record

import orm_queries
import query_data
from models import make_session_factory

pytestmark = pytest.mark.analysis

LOOSE_PERCENT = re.compile(r"\d+(?:\.\d+)?%")
STRICT_PERCENT = re.compile(r"\d+\.\d{2}%")


def _seed_rows():
    # Fall 2025: 1 of 3 accepted -> Q5 = 33.33%.
    # Nationality: 1 International of 3 -> Q2 = 33.33%.
    # Degrees: 2 PhD (1 accepted = 50.00%), 1 Masters (0 accepted = 0.00%).
    return [
        make_record(0, **{"Semester and Year": "Fall 2025", "Applicant Status": "Accepted",
                          "International/American": "International", "Masters or PhD": "PhD"}),
        make_record(1, **{"Semester and Year": "Fall 2025", "Applicant Status": "Rejected",
                          "International/American": "American", "Masters or PhD": "PhD"}),
        make_record(2, **{"Semester and Year": "Fall 2025", "Applicant Status": "Wait listed",
                          "International/American": "American", "Masters or PhD": "Masters"}),
    ]


def _page(client):
    response = client.get("/analysis")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_every_result_has_answer_label(client, seed):
    seed(_seed_rows())
    soup = BeautifulSoup(_page(client), "html.parser")

    sections = soup.select("section.question")
    assert len(sections) > 0
    for section in sections:
        assert "Answer:" in section.get_text(), section.find("h2").get_text()
    assert soup.get_text().count("Answer:") == len(sections)


def test_percentages_two_decimals(client, seed):
    seed(_seed_rows())
    text = BeautifulSoup(_page(client), "html.parser").get_text()

    percentages = LOOSE_PERCENT.findall(text)
    assert percentages
    for value in percentages:
        assert STRICT_PERCENT.fullmatch(value), value
    assert "33.33%" in percentages
    assert "50.00%" in percentages
    assert "0.00%" in percentages


def test_empty_database_shows_na(client):
    text = BeautifulSoup(_page(client), "html.parser").get_text()

    assert "N/A" in text
    for value in LOOSE_PERCENT.findall(text):
        assert STRICT_PERCENT.fullmatch(value), value


def test_custom2_ties_ordered_by_degree(client, seed, db_conn, test_database_url):
    # Three degree types tied at 2 entries each, inserted in reverse alphabetical
    # order; ties must come back alphabetically by degree, everywhere.
    rows = []
    for degree in ("PhD", "Masters", "EdD"):
        rows += [make_record(len(rows), **{"Masters or PhD": degree, "Applicant Status": "Accepted"}),
                 make_record(len(rows) + 1, **{"Masters or PhD": degree, "Applicant Status": "Rejected"})]
    rows.append(make_record(len(rows), **{"Masters or PhD": "MFA"}))  # count 1, sorts last
    seed(rows)
    expected = [("EdD", 1, 2), ("Masters", 1, 2), ("PhD", 1, 2), ("MFA", 1, 1)]

    with db_conn, db_conn.cursor() as cur:
        assert [(d, a, t) for d, a, t, _ in query_data.custom2(cur)] == expected
    with make_session_factory(test_database_url)() as session:
        assert [(d, a, t) for d, a, t, _ in orm_queries.custom2_acceptance_by_degree(session)] == expected

    table = BeautifulSoup(_page(client), "html.parser").select("section.question table tbody tr")
    assert [tr.find("td").get_text() for tr in table] == ["EdD", "Masters", "PhD", "MFA"]
