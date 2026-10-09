"""Flask app factory, routes, and the rendered analysis page."""

import pytest
from bs4 import BeautifulSoup
from flask import Flask

pytestmark = pytest.mark.web


def _rules(app):
    return {rule.rule: rule.methods for rule in app.url_map.iter_rules()}


def test_create_app_registers_routes(app):
    assert isinstance(app, Flask)
    rules = _rules(app)
    assert "GET" in rules["/"]
    assert "GET" in rules["/analysis"]
    assert "POST" in rules["/pull-data"]
    assert "GET" not in rules["/pull-data"]
    assert "POST" in rules["/update-analysis"]
    assert "GET" not in rules["/update-analysis"]


def test_analysis_page_renders(client):
    response = client.get("/analysis")
    assert response.status_code == 200

    soup = BeautifulSoup(response.data, "html.parser")
    assert len(soup.select('[data-testid="pull-data-btn"]')) == 1
    assert len(soup.select('[data-testid="update-analysis-btn"]')) == 1
    text = soup.get_text()
    assert "Analysis" in text
    assert "Answer:" in text


def test_root_renders_same_page(client):
    root = client.get("/")
    analysis = client.get("/analysis")
    assert root.status_code == 200

    root_soup = BeautifulSoup(root.data, "html.parser")
    analysis_soup = BeautifulSoup(analysis.data, "html.parser")
    assert root_soup.find("main") == analysis_soup.find("main")
    assert root_soup.find("div", class_="actions") == analysis_soup.find("div", class_="actions")


def test_first_run_without_applicants_table(make_app, db_conn):
    with db_conn, db_conn.cursor() as cur:
        cur.execute("DROP TABLE applicants")
        cur.execute("SELECT to_regclass('applicants')")
        assert cur.fetchone()[0] is None

    response = make_app().test_client().get("/analysis")

    assert response.status_code == 200
    soup = BeautifulSoup(response.data, "html.parser")
    text = soup.get_text(" ", strip=True)
    assert "Fall 2026 applicant count: 0" in text
    assert "Percent international: N/A" in text
    assert text.count("Answer:") == len(soup.select("section.question"))
    with db_conn, db_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('applicants')")
        assert cur.fetchone()[0] == "applicants"


def test_subtitle_describes_cached_analysis(client):
    soup = BeautifulSoup(client.get("/analysis").data, "html.parser")
    subtitle = soup.select_one("p.subtitle").get_text(" ", strip=True)

    assert subtitle == "SQL/ORM query results from the applicants table, as of the last Update Analysis."
    assert "computed live" not in soup.get_text()
