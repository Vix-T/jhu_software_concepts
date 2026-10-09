"""etl/incremental_scraper.py against synthetic Grad Cafe pages, with no browser and no network.

FakeDriver (conftest) serves tests/fixtures/*.html through the driver_factory
seam; Selenium's Chrome class, DNS lookups and socket connects are replaced
only where a test checks how the scraper attaches. Delays and retry waits are 0.

Pull fixture pages, newest first: page 1 has 5010, 5009 (new) and
5008-5006; page 2 has 5005, 5004; page 3 has 5003.
"""

import socket

import pytest
from conftest import (
    PAGE_2_URL,
    PULL_PAGE_URLS,
    SELF_LINK_URL,
    SURVEY_URL,
    DriverFactory,
    FakeDriver,
    Spy,
    load_fixture,
    pull_pages,
    result_url,
    survey_pages,
)
from selenium.common.exceptions import WebDriverException

from etl import incremental_scraper as scraper

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



def _nothing_known(urls):
    return set()


def _without_raw_text(entries):
    return [{k: v for k, v in e.items() if k != "raw_entry_text"} for e in entries]


def _browser(factory, start_url=PULL_PAGE_URLS[0], **settings):
    return scraper.BrowserSettings(driver_factory=factory, start_url=start_url, delay_seconds=0, **settings)


def _urls(entries):
    return [e["URL"] for e in entries]


class AlwaysCrashes:
    """driver_factory whose every attach attempt fails."""

    def __init__(self):
        self.attempts = 0

    def __call__(self):
        self.attempts += 1
        raise WebDriverException(f"session not created (attempt {self.attempts})")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_page_extracts_entries():
    entries = scraper.parse_page(load_fixture("page_1.html"))

    assert _without_raw_text(entries) == PAGE_1_ENTRIES
    assert entries[0]["raw_entry_text"] == (
        "Stanford University Computer Science PhD Sep 08, 2026 Accepted on 5 Sep See More"
        " | Fall 2026 International GRE 330 GRE V 162 GRE AW 4.5 GPA 3.91"
        " | Funded offer, very excited."
    )
    assert not any("Advertisement" in e["raw_entry_text"] for e in entries)
    assert _without_raw_text(scraper.parse_page(load_fixture("page_2.html"))) == PAGE_2_ENTRIES


def test_parse_page_without_results_table():
    assert scraper.parse_page(load_fixture("page_no_results.html")) == []


def test_parse_entry_status_without_known_prefix():
    [entry] = scraper.parse_page(load_fixture("page_self_link.html"))

    assert entry["University"] == "MIT"
    assert entry["Applicant Status"] is None
    assert entry["URL"] == "https://www.thegradcafe.com/result/3001"


# ---------------------------------------------------------------------------
# Attaching to Chrome: CHROME_DEBUGGER_ADDRESS
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_chrome(monkeypatch):
    created = []

    class FakeChrome:
        def __init__(self, options, service=None):
            self.options = options
            self.service = service
            created.append(self)

    monkeypatch.setattr(scraper, "ChromeWebDriver", FakeChrome)
    monkeypatch.delenv("CHROMEDRIVER_PATH", raising=False)
    return created


def test_attach_uses_default_debugger_address(monkeypatch, fake_chrome):
    monkeypatch.delenv("CHROME_DEBUGGER_ADDRESS", raising=False)

    driver = scraper.attach_to_chrome()

    assert fake_chrome == [driver]
    assert driver.options.experimental_options == {"debuggerAddress": "127.0.0.1:9222"}
    assert driver.service is None  # no CHROMEDRIVER_PATH: Selenium Manager finds a driver


@pytest.mark.parametrize("value", ["/usr/local/bin/chromedriver", "  /opt/drivers/chromedriver  "])
def test_attach_uses_the_chromedriver_at_chromedriver_path(monkeypatch, fake_chrome, value):
    monkeypatch.setenv("CHROMEDRIVER_PATH", value)

    driver = scraper.attach_to_chrome()

    assert isinstance(driver.service, scraper.ChromeService)
    assert driver.service.path == value.strip()


def test_blank_chromedriver_path_is_unset(monkeypatch, fake_chrome):
    monkeypatch.setenv("CHROMEDRIVER_PATH", "   ")

    assert scraper.attach_to_chrome().service is None


@pytest.mark.parametrize("address", ["10.0.0.7:9333", "localhost:9222"])
def test_ip_and_localhost_addresses_are_used_as_given(monkeypatch, fake_chrome, address):
    lookups = []
    monkeypatch.setenv("CHROME_DEBUGGER_ADDRESS", address)
    monkeypatch.setattr(socket, "gethostbyname", lookups.append)

    driver = scraper.attach_to_chrome()

    assert driver.options.experimental_options == {"debuggerAddress": address}
    assert lookups == []  # no DNS lookup for an IP literal or localhost


def test_hostname_is_resolved_to_an_ip_before_attaching(monkeypatch, fake_chrome):
    lookups = []

    def resolve(host):
        lookups.append(host)
        return "192.168.65.254"

    monkeypatch.setenv("CHROME_DEBUGGER_ADDRESS", "host.docker.internal:9222")
    monkeypatch.setattr(socket, "gethostbyname", resolve)

    driver = scraper.attach_to_chrome()

    assert lookups == ["host.docker.internal"]
    assert driver.options.experimental_options == {"debuggerAddress": "192.168.65.254:9222"}


def test_unresolvable_hostname_is_a_precondition_error(monkeypatch, fake_chrome, caplog):
    def resolve(host):
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")

    monkeypatch.setenv("CHROME_DEBUGGER_ADDRESS", "chrome.invalid:9222")
    monkeypatch.setattr(socket, "gethostbyname", resolve)

    with pytest.raises(scraper.PullPreconditionError, match="cannot resolve Chrome debugger host 'chrome.invalid'"):
        scraper.attach_to_chrome()

    assert fake_chrome == []
    assert "Cannot resolve the Chrome debugger host 'chrome.invalid'" in caplog.text


@pytest.mark.parametrize("address", ["9222", "chrome:", ":9222", "chrome:port", "chrome:-1"])
def test_malformed_address_is_a_precondition_error(monkeypatch, address):
    monkeypatch.setenv("CHROME_DEBUGGER_ADDRESS", address)

    with pytest.raises(scraper.PullPreconditionError, match="CHROME_DEBUGGER_ADDRESS must be host:port"):
        scraper.debugger_address()


def test_blank_address_means_the_default(monkeypatch):
    monkeypatch.setenv("CHROME_DEBUGGER_ADDRESS", "  ")
    assert scraper.debugger_address() == ("127.0.0.1", 9222)


def test_port_open_when_something_listens():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert scraper._debugger_port_open("127.0.0.1", port) is True


def test_port_closed_when_nothing_listens():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert scraper._debugger_port_open("127.0.0.1", port) is False


def test_chrome_is_listening_checks_the_configured_address(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        monkeypatch.setenv("CHROME_DEBUGGER_ADDRESS", f"127.0.0.1:{server.getsockname()[1]}")
        assert scraper.chrome_is_listening() is True


# ---------------------------------------------------------------------------
# scrape_new_entries: the precondition and the watermark
# ---------------------------------------------------------------------------


def test_scrape_requires_chrome():
    with pytest.raises(scraper.PullPreconditionError) as excinfo:
        scraper.scrape_new_entries(5000, port_check=lambda: False)

    assert str(excinfo.value) == scraper.CLOUDFLARE_PRECONDITION_MESSAGE


def test_driver_factory_skips_port_check():
    port_check = Spy(lambda: False)
    factory = DriverFactory(survey_pages())

    entries = scraper.scrape_new_entries(None, browser=_browser(factory, SURVEY_URL), port_check=port_check)

    assert port_check.calls == []
    assert [e["University"] for e in entries] == [
        "Stanford University", "Johns Hopkins University", "Georgetown University",
    ]
    # Page 2's entries have no URL, so it yields nothing new and the scrape stops there.
    assert factory.visited == [SURVEY_URL, PAGE_2_URL]


def test_entries_above_the_watermark_are_new_and_it_stops_at_a_known_page(capsys):
    factory = DriverFactory(pull_pages())

    entries = scraper.scrape_new_entries(5008, browser=_browser(factory))

    assert _urls(entries) == [result_url(5010), result_url(5009)]
    assert factory.visited == PULL_PAGE_URLS[:2]  # page 3 never fetched
    assert "stopped because a page had no new entries" in capsys.readouterr().out


def test_without_a_watermark_everything_is_new():
    factory = DriverFactory(pull_pages())

    entries = scraper.scrape_new_entries(None, browser=_browser(factory))

    assert _urls(entries) == [result_url(rid) for rid in range(5010, 5002, -1)]
    assert factory.visited == PULL_PAGE_URLS


def test_scrape_stops_at_target_count(capsys):
    factory = DriverFactory(pull_pages())

    entries = scraper.scrape_new_entries(None, target_count=3, browser=_browser(factory))

    assert len(entries) == 5  # the whole first page
    assert factory.visited == PULL_PAGE_URLS[:1]
    assert "stopped because collected at least 3 new entries" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("watermark", "known"),
    [(None, set()), (5008, {5008, 5004}), (5004, {5004}), (0, set())],
)
def test_known_by_watermark(watermark, known):
    urls = [result_url(5010), result_url(5008), result_url(5004), "https://www.thegradcafe.com/result/abc"]

    assert scraper.known_by_watermark(watermark)(urls) == {result_url(rid) for rid in known}


# ---------------------------------------------------------------------------
# scrape_newest: pagination and crashes
# ---------------------------------------------------------------------------


def test_scrape_newest_stops_at_end_of_pagination(capsys):
    factory = DriverFactory(pull_pages())

    entries = scraper.scrape_newest(_nothing_known, _browser(factory, PULL_PAGE_URLS[2]))

    assert _urls(entries) == [result_url(5003)]
    assert factory.visited == PULL_PAGE_URLS[2:]
    assert "stopped because reached the end of pagination" in capsys.readouterr().out


def test_scrape_newest_stops_when_next_link_does_not_advance(capsys):
    factory = DriverFactory({SELF_LINK_URL: load_fixture("page_self_link.html")})

    entries = scraper.scrape_newest(_nothing_known, _browser(factory, SELF_LINK_URL))

    assert _urls(entries) == ["https://www.thegradcafe.com/result/3001"]
    assert factory.visited == [SELF_LINK_URL]
    assert "stopped because reached the end of pagination" in capsys.readouterr().out


def test_page_without_results_table_is_waited_for_then_used(capsys):
    factory = DriverFactory({SURVEY_URL: load_fixture("page_no_results.html")})

    entries = scraper.scrape_newest(_nothing_known, _browser(factory, SURVEY_URL, wait_timeout=0))

    assert entries == []
    out = capsys.readouterr().out
    assert f"Results table never appeared for {SURVEY_URL}" in out
    assert "stopped because a page had no new entries" in out


def test_scrape_newest_skips_entries_repeated_across_pages():
    # New results arriving mid-scrape shift entries onto the next page; a URL
    # already collected on page 1 must not be collected twice.
    pages = pull_pages()
    pages[PULL_PAGE_URLS[1]] = pages[PULL_PAGE_URLS[0]].replace(
        "https://www.thegradcafe.com/survey/?page=2", "https://www.thegradcafe.com/survey/?page=3"
    )
    factory = DriverFactory(pages)

    entries = scraper.scrape_newest(_nothing_known, _browser(factory))

    assert _urls(entries) == [result_url(rid) for rid in (5010, 5009, 5008, 5007, 5006)]
    assert factory.visited == PULL_PAGE_URLS[:2]


def test_crash_mid_scrape_resumes_from_failed_page(capsys):
    failures = {PULL_PAGE_URLS[1]: 1}  # page 2 fails once

    class FlakyDriver(FakeDriver):
        def get(self, url):
            if failures.get(url):
                failures[url] -= 1
                self.visited.append(url)
                raise WebDriverException("tab crashed")
            super().get(url)

    drivers = []

    def factory():
        drivers.append(FlakyDriver(pull_pages()))
        return drivers[-1]

    entries = scraper.scrape_new_entries(5008, browser=_browser(factory, crash_retry_wait=0))

    assert _urls(entries) == [result_url(5010), result_url(5009)]
    assert len(drivers) == 2
    assert drivers[0].visited == PULL_PAGE_URLS[:2]  # page 1, then page 2 crashed
    assert drivers[1].visited == [PULL_PAGE_URLS[1]]  # re-attach resumes at page 2, not page 1
    assert "Retrying the same page with a fresh browser attach (1/3)." in capsys.readouterr().out


def test_scrape_newest_gives_up_after_max_retries():
    factory = AlwaysCrashes()

    with pytest.raises(scraper.ScrapeRetriesExhausted) as excinfo:
        scraper.scrape_newest(
            _nothing_known, scraper.BrowserSettings(driver_factory=factory, max_retries=2, crash_retry_wait=0)
        )

    assert factory.attempts == 3
    assert str(excinfo.value) == (
        "browser session crashed 3 times in a row; giving up. "
        "Last error: Message: session not created (attempt 3)\n"
    )
