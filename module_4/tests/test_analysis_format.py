"""Rendered analysis output: "Answer:" labels and two-decimal percentages."""

import re

import pytest
from bs4 import BeautifulSoup
from conftest import make_record

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
