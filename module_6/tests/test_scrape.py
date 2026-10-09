"""scrape.py against synthetic Grad Cafe pages, through the driver_factory seam.

No browser and no network: FakeDriver (conftest) serves tests/fixtures/*.html.
State files and captured pages live in tmp_path; delays and retry waits are 0.
"""

import json
import os

import pytest
from conftest import (
    PAGE_2_URL,
    SELF_LINK_URL,
    SURVEY_URL,
    DriverFactory,
    load_fixture,
    survey_pages,
)
from selenium.common.exceptions import WebDriverException

import scrape

pytestmark = pytest.mark.integration

EMPTY = {
    "Program Name": None, "University": None, "Comments": None, "Date Added": None, "URL": None,
    "Applicant Status": None, "Acceptance Date": None, "Rejection Date": None,
    "Semester and Year": None, "International/American": None, "GRE Score": None,
    "GRE V Score": None, "GRE AW Score": None, "Masters or PhD": None, "GPA": None,
}

PAGE_1_ENTRIES = [
    {**EMPTY, "University": "Stanford University", "Program Name": "Computer Science",
     "Masters or PhD": "PhD", "Date Added": "Sep 08, 2026", "Applicant Status": "Accepted",
     "Acceptance Date": "5 Sep", "URL": "https://www.thegradcafe.com/result/1001",
     "Semester and Year": "Fall 2026", "International/American": "International",
     "GRE Score": "330", "GRE V Score": "162", "GRE AW Score": "4.5", "GPA": "3.91",
     "Comments": "Funded offer, very excited."},
    {**EMPTY, "University": "Johns Hopkins University", "Program Name": "Computer Science",
     "Masters or PhD": "Masters", "Date Added": "Sep 07, 2026", "Applicant Status": "Rejected",
     "Rejection Date": "1 Sep", "URL": "https://www.thegradcafe.com/result/1002",
     "Semester and Year": "Spring 2026", "International/American": "American"},
    {**EMPTY, "University": "Georgetown University", "Program Name": "Economics",
     "Masters or PhD": "PhD", "Date Added": "Sep 06, 2026", "Applicant Status": "Interview",
     "URL": "https://www.thegradcafe.com/result/1003", "International/American": "Other"},
]

PAGE_2_ENTRIES = [
    # Status without a date; the link has no href, so no URL.
    {**EMPTY, "University": "Carnegie Mellon University", "Program Name": "Robotics",
     "Masters or PhD": "PhD", "Date Added": "Sep 05, 2026", "Applicant Status": "Wait listed"},
    # The blank-university row is filtered out; the truncated row keeps what it has.
    {**EMPTY, "University": "Truncated Row University", "Program Name": "History",
     "Masters or PhD": "Masters"},
]


def _without_raw_text(entries):
    return [{k: v for k, v in e.items() if k != "raw_entry_text"} for e in entries]


def _paths(tmp_path):
    return scrape.CaptureFiles(state_file=str(tmp_path / "state.json"), captured_dir=str(tmp_path / "pages"))


def _browser(**settings):
    return scrape.BrowserSettings(**settings)


def _state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text())


# --- attach_to_chrome -------------------------------------------------------


def test_attach_to_chrome_uses_debugger_address(monkeypatch):
    created = []

    class FakeChrome:
        def __init__(self, options):
            self.options = options
            created.append(self)

    monkeypatch.setattr(scrape, "ChromeWebDriver", FakeChrome)

    driver = scrape.attach_to_chrome()
    assert created == [driver]
    assert driver.options.experimental_options == {"debuggerAddress": "127.0.0.1:9222"}


# --- capture_pages ------------------------------------------------------------


def test_capture_pages_until_no_next_link(tmp_path, capsys):
    factory = DriverFactory(survey_pages())

    captured = scrape.capture_pages(
        _browser(start_url=SURVEY_URL, delay_seconds=0, driver_factory=factory), _paths(tmp_path)
    )

    assert captured == 2
    assert factory.visited == [SURVEY_URL, PAGE_2_URL]
    assert (tmp_path / "pages" / "page_00001.html").read_text() == load_fixture("page_1.html")
    assert (tmp_path / "pages" / "page_00002.html").read_text() == load_fixture("page_2.html")
    assert _state(tmp_path) == {"next_url": None, "pages_captured": 2}
    assert "Stopped because: no next-page link found" in capsys.readouterr().out


def test_capture_pages_target_then_resume(tmp_path, capsys):
    factory = DriverFactory(survey_pages())
    browser = _browser(start_url=SURVEY_URL, delay_seconds=0, driver_factory=factory)

    assert scrape.capture_pages(browser, _paths(tmp_path), target_pages=1) == 1
    assert _state(tmp_path) == {"next_url": PAGE_2_URL, "pages_captured": 1}
    assert "Stopped because: reached target_pages" in capsys.readouterr().out

    # Resumes from the saved next_url and keeps numbering pages from 2.
    assert scrape.capture_pages(browser, _paths(tmp_path)) == 1
    assert factory.visited == [SURVEY_URL, PAGE_2_URL]
    assert (tmp_path / "pages" / "page_00002.html").read_text() == load_fixture("page_2.html")
    assert _state(tmp_path) == {"next_url": None, "pages_captured": 2}


def test_capture_pages_stops_when_next_link_does_not_advance(tmp_path, capsys):
    factory = DriverFactory({SELF_LINK_URL: load_fixture("page_self_link.html")})

    captured = scrape.capture_pages(
        _browser(start_url=SELF_LINK_URL, delay_seconds=0, driver_factory=factory), _paths(tmp_path)
    )

    assert captured == 1
    assert factory.visited == [SELF_LINK_URL]
    assert _state(tmp_path) == {"next_url": SELF_LINK_URL, "pages_captured": 1}
    assert "Stopped because: next_url did not advance from the current page" in capsys.readouterr().out


def test_capture_pages_saves_page_without_results_table(tmp_path, capsys):
    factory = DriverFactory({SURVEY_URL: load_fixture("page_no_results.html")})

    captured = scrape.capture_pages(
        _browser(start_url=SURVEY_URL, delay_seconds=0, wait_timeout=0, driver_factory=factory), _paths(tmp_path)
    )

    assert captured == 1
    assert (tmp_path / "pages" / "page_00001.html").read_text() == load_fixture("page_no_results.html")
    out = capsys.readouterr().out
    assert f"Results table never appeared for {SURVEY_URL}" in out
    assert "Stopped because: no next-page link found" in out


def test_capture_pages_already_at_last_page(tmp_path, capsys):
    (tmp_path / "state.json").write_text(json.dumps({"next_url": None, "pages_captured": 2}))
    factory = DriverFactory(survey_pages())

    assert scrape.capture_pages(_browser(delay_seconds=0, driver_factory=factory), _paths(tmp_path)) == 0
    assert factory.drivers == []
    assert "already at the last page" in capsys.readouterr().out


# --- parse_captured_pages -----------------------------------------------------


def test_parse_captured_pages_extracts_entries(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page_00001.html").write_text(load_fixture("page_1.html"))
    (pages / "page_00002.html").write_text(load_fixture("page_2.html"))

    entries = scrape.parse_captured_pages(captured_dir=str(pages))

    assert _without_raw_text(entries) == PAGE_1_ENTRIES + PAGE_2_ENTRIES
    assert entries[0]["raw_entry_text"] == (
        "Stanford University Computer Science PhD Sep 08, 2026 Accepted on 5 Sep See More"
        " | Fall 2026 International GRE 330 GRE V 162 GRE AW 4.5 GPA 3.91"
        " | Funded offer, very excited."
    )
    assert not any("Advertisement" in e["raw_entry_text"] for e in entries)


def test_parse_captured_pages_without_results_table(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page_00001.html").write_text(load_fixture("page_no_results.html"))

    assert scrape.parse_captured_pages(captured_dir=str(pages)) == []


def test_parse_entry_status_without_known_prefix(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page_00001.html").write_text(load_fixture("page_self_link.html"))

    [entry] = scrape.parse_captured_pages(captured_dir=str(pages))
    assert entry["University"] == "MIT"
    assert entry["Applicant Status"] is None
    assert entry["URL"] == "https://www.thegradcafe.com/result/3001"


# --- scrape_data ----------------------------------------------------------------


def test_scrape_data_batches_until_natural_end(tmp_path, capsys):
    factory = DriverFactory(survey_pages())

    entries = scrape.scrape_data(
        _browser(start_url=SURVEY_URL, delay_seconds=0, driver_factory=factory), _paths(tmp_path),
        target_count=100, batch_size=1,
    )

    assert _without_raw_text(entries) == PAGE_1_ENTRIES + PAGE_2_ENTRIES
    assert len(factory.drivers) == 2  # one attach per batch
    assert factory.visited == [SURVEY_URL, PAGE_2_URL]
    out = capsys.readouterr().out
    assert "Progress: 1 pages captured, 3 entries parsed so far." in out
    assert "Reached natural end of available pages." in out


def test_scrape_data_stops_at_target_count(tmp_path, capsys):
    factory = DriverFactory(survey_pages())

    entries = scrape.scrape_data(
        _browser(start_url=SURVEY_URL, delay_seconds=0, driver_factory=factory), _paths(tmp_path),
        target_count=3, batch_size=1,
    )

    assert _without_raw_text(entries) == PAGE_1_ENTRIES
    assert factory.visited == [SURVEY_URL]
    assert "Target of 3 entries reached." in capsys.readouterr().out


def test_scrape_data_retries_after_browser_crash(tmp_path, capsys):
    pages_factory = DriverFactory(survey_pages())
    attempts = []

    def flaky_factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise WebDriverException("session crashed")
        return pages_factory()

    entries = scrape.scrape_data(
        _browser(start_url=SURVEY_URL, delay_seconds=0, crash_retry_wait=0, driver_factory=flaky_factory),
        _paths(tmp_path),
        target_count=100,
    )

    assert len(attempts) == 2
    assert _without_raw_text(entries) == PAGE_1_ENTRIES + PAGE_2_ENTRIES
    out = capsys.readouterr().out
    assert "Browser session crashed during batch starting at page 1" in out
    assert "retrying with a fresh browser attach" in out
