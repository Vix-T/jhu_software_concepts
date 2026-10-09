"""Pull Data fetches the NEWEST entries and gives up cleanly after repeated browser crashes.

Fixture pages (conftest.pull_pages): page 1 has 2 new and 3 known URLs,
page 2 is all known, page 3 must never be reached. "Known" means already
in the test database.
"""

import functools

import pytest
from conftest import PULL_PAGE_URLS, DriverFactory, FakeDriver, make_record, pull_pages, result_url
from selenium.common.exceptions import WebDriverException

import load_data
import pull_data
import scrape
from busy_state import FileLockBusyState

pytestmark = pytest.mark.integration

KNOWN_IDS = [5003, 5004, 5005, 5006, 5007]


def _seed_known(seed):
    records = [make_record(i, URL=result_url(rid)) for i, rid in enumerate(KNOWN_IDS)]
    assert seed(records) == (5, 0, [])


def _scraper(factory, target_count=pull_data.TARGET_COUNT, **settings):
    browser = scrape.BrowserSettings(driver_factory=factory, start_url=PULL_PAGE_URLS[0], delay_seconds=0, **settings)
    return functools.partial(pull_data.scrape_new_entries, target_count=target_count, browser=browser)


class AlwaysCrashes:
    """driver_factory whose every attach attempt fails."""

    def __init__(self):
        self.attempts = 0

    def __call__(self):
        self.attempts += 1
        raise WebDriverException(f"session not created (attempt {self.attempts})")


def test_pull_inserts_only_new_entries_and_stops_at_known_page(seed, make_app, fetch_rows):
    _seed_known(seed)
    factory = DriverFactory(pull_pages())
    client = make_app(scraper=_scraper(factory)).test_client()

    response = client.post("/pull-data")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "inserted": 2}
    assert factory.visited == PULL_PAGE_URLS[:2]  # page 3 never fetched
    urls = {row["url"] for row in fetch_rows()}
    assert urls == {result_url(rid) for rid in KNOWN_IDS + [5001, 5002]}


def test_repeat_pull_finds_nothing_new(seed, make_app, row_count):
    _seed_known(seed)
    factory = DriverFactory(pull_pages())
    client = make_app(scraper=_scraper(factory)).test_client()

    assert client.post("/pull-data").get_json() == {"ok": True, "inserted": 2}
    second = client.post("/pull-data")

    assert second.get_json() == {"ok": True, "inserted": 0}
    # The second pull starts again at page 1 (newest), finds nothing new there, and stops.
    assert factory.visited == PULL_PAGE_URLS[:2] + PULL_PAGE_URLS[:1]
    assert row_count() == 7


def test_pull_stops_at_target_count(make_app, row_count, capsys):
    factory = DriverFactory(pull_pages())
    client = make_app(scraper=_scraper(factory, target_count=3)).test_client()

    response = client.post("/pull-data")

    assert response.get_json() == {"ok": True, "inserted": 5}
    assert factory.visited == PULL_PAGE_URLS[:1]
    assert row_count() == 5
    assert "stopped because collected at least 3 new entries" in capsys.readouterr().out


def test_scrape_newest_stops_at_end_of_pagination(capsys):
    factory = DriverFactory(pull_pages())

    entries = scrape.scrape_newest(
        lambda urls: set(),
        scrape.BrowserSettings(start_url=PULL_PAGE_URLS[2], delay_seconds=0, driver_factory=factory),
    )

    assert [e["URL"] for e in entries] == [result_url(5008)]
    assert factory.visited == PULL_PAGE_URLS[2:]
    assert "stopped because reached the end of pagination" in capsys.readouterr().out


def test_scrape_newest_skips_entries_repeated_across_pages():
    # New results arriving mid-pull shift entries onto the next page; a URL
    # already collected on page 1 must not be collected twice.
    pages = pull_pages()
    pages[PULL_PAGE_URLS[1]] = pages[PULL_PAGE_URLS[0]].replace(
        "https://www.thegradcafe.com/survey/?page=2", "https://www.thegradcafe.com/survey/?page=3"
    )
    factory = DriverFactory(pages)

    entries = scrape.scrape_newest(
        lambda urls: set(),
        scrape.BrowserSettings(start_url=PULL_PAGE_URLS[0], delay_seconds=0, driver_factory=factory),
    )

    assert [e["URL"] for e in entries] == [result_url(rid) for rid in (5001, 5002, 5003, 5004, 5005)]
    assert factory.visited == PULL_PAGE_URLS[:2]


def test_crash_mid_pull_resumes_from_failed_page(seed, capsys):
    _seed_known(seed)
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

    entries = pull_data.scrape_new_entries(
        browser=scrape.BrowserSettings(
            driver_factory=factory, start_url=PULL_PAGE_URLS[0], delay_seconds=0, crash_retry_wait=0
        )
    )

    assert [e["URL"] for e in entries] == [result_url(5001), result_url(5002)]
    assert len(drivers) == 2
    assert drivers[0].visited == PULL_PAGE_URLS[:2]  # page 1, then page 2 crashed
    assert drivers[1].visited == [PULL_PAGE_URLS[1]]  # re-attach resumes at page 2, not page 1
    assert "Retrying the same page with a fresh browser attach (1/3)." in capsys.readouterr().out


def test_scrape_newest_gives_up_after_max_retries():
    factory = AlwaysCrashes()

    with pytest.raises(scrape.ScrapeRetriesExhausted) as excinfo:
        scrape.scrape_newest(
            lambda urls: set(), scrape.BrowserSettings(driver_factory=factory, max_retries=2, crash_retry_wait=0)
        )

    assert factory.attempts == 3
    assert str(excinfo.value) == (
        "browser session crashed 3 times in a row; giving up. "
        "Last error: Message: session not created (attempt 3)\n"
    )


def test_scrape_data_gives_up_after_max_retries(tmp_path):
    factory = AlwaysCrashes()

    with pytest.raises(scrape.ScrapeRetriesExhausted):
        scrape.scrape_data(
            scrape.BrowserSettings(driver_factory=factory, max_retries=2, crash_retry_wait=0),
            scrape.CaptureFiles(state_file=str(tmp_path / "state.json"), captured_dir=str(tmp_path / "pages")),
        )

    assert factory.attempts == 3


def test_pull_cli_records_retries_exhausted(pull_result_path, capsys):
    factory = AlwaysCrashes()

    with pytest.raises(SystemExit) as excinfo:
        pull_data.main(scraper=_scraper(factory, max_retries=3, crash_retry_wait=0))

    assert excinfo.value.code == 1
    assert factory.attempts == 4
    result = pull_data.read_pull_result(str(pull_result_path))
    assert result["ok"] is False
    assert result["error"].startswith("ScrapeRetriesExhausted: browser session crashed 4 times in a row")
    assert "PULL FAILED: ScrapeRetriesExhausted" in capsys.readouterr().out


def test_in_process_pull_retries_exhausted_releases_lock(make_app, tmp_path, pull_result_path, row_count):
    lock = FileLockBusyState(str(tmp_path / "pull.lock"))
    factory = AlwaysCrashes()
    client = make_app(scraper=_scraper(factory, max_retries=1, crash_retry_wait=0), busy_state=lock).test_client()

    response = client.post("/pull-data")

    assert response.status_code == 500
    body = response.get_json()
    assert body["ok"] is False
    assert body["error"].startswith("browser session crashed 2 times in a row")
    assert factory.attempts == 2
    assert lock.is_busy() is False
    assert pull_data.read_pull_result(str(pull_result_path))["ok"] is False
    assert row_count() == 0


def test_existing_urls_lookup(seed, test_database_url):
    _seed_known(seed)

    assert load_data.existing_urls([], test_database_url) == set()
    assert load_data.existing_urls([result_url(5001), result_url(5003)], test_database_url) == {result_url(5003)}
