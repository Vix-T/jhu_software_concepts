"""
Scrapes graduate school applicant self-reported data from The Grad Cafe
(thegradcafe.com) survey results pages.

Uses urllib3 to fetch raw HTML pages, Selenium to drive a browser for any
pages requiring JavaScript rendering or interaction, and BeautifulSoup to
parse the resulting HTML into structured applicant entry records.
"""

import glob
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

DEBUGGER_ADDRESS = "127.0.0.1:9222"
SURVEY_URL = "https://www.thegradcafe.com/survey/"
RESULTS_TABLE_SELECTOR = "tbody.tw-divide-y.tw-divide-gray-200.tw-bg-white"
SITE_ROOT = "https://www.thegradcafe.com"

# Every field a parsed entry record has, in order (all None until found).
RECORD_FIELDS = (
    "Program Name",
    "University",
    "Comments",
    "Date Added",
    "URL",
    "Applicant Status",
    "Acceptance Date",
    "Rejection Date",
    "Semester and Year",
    "International/American",
    "GRE Score",
    "GRE V Score",
    "GRE AW Score",
    "Masters or PhD",
    "GPA",
    "raw_entry_text",
)

STATUS_PATTERN = re.compile(r"^(Accepted|Rejected|Wait listed|Interview)(?:\s+on\s+(.+))?$")
# Decision status -> the record field its "on <date>" goes into.
STATUS_DATE_FIELDS = {"Accepted": "Acceptance Date", "Rejected": "Rejection Date"}

# Badge text patterns, tried in order (the first match wins): (pattern,
# record field, regex group holding the value; 0 = the whole badge text).
BADGE_PATTERNS = (
    (re.compile(r"^(Fall|Spring|Summer|Winter)\s+\d{4}$"), "Semester and Year", 0),
    (re.compile(r"^(International|American|Other)$"), "International/American", 0),
    (re.compile(r"^GRE V\s+([\d.]+)$"), "GRE V Score", 1),
    (re.compile(r"^GRE AW\s+([\d.]+)$"), "GRE AW Score", 1),
    (re.compile(r"^GRE\s+(\d+)$"), "GRE Score", 1),
    (re.compile(r"^GPA\s+([\d.]+)$"), "GPA", 1),
)


@dataclass(frozen=True)
class BrowserSettings:
    """How survey pages are fetched: where to start, pacing, and crash handling.

    Attributes:
        start_url: The survey URL to start from (when there is no saved
            capture state to resume from).
        delay_seconds: Seconds to sleep between page loads, to avoid
            hammering the site.
        wait_timeout: Seconds to wait for each page's results table before
            using the page anyway.
        driver_factory: Zero-argument callable returning a Selenium
            WebDriver-like object (needs .get() and .page_source). None
            means attach_to_chrome(); tests pass a fake that serves saved HTML.
        max_retries: Consecutive browser-crash retries allowed before
            ScrapeRetriesExhausted is raised.
        crash_retry_wait: Seconds to wait before re-attaching after a crash.
    """

    start_url: str = SURVEY_URL
    delay_seconds: float = 2.5
    wait_timeout: float = 15
    driver_factory: Callable | None = None
    max_retries: int = 3
    crash_retry_wait: float = 5

    def new_driver(self):
        """Attach a fresh browser session (driver_factory, default attach_to_chrome)."""
        return (self.driver_factory or attach_to_chrome)()


@dataclass(frozen=True)
class CaptureFiles:
    """Where capture_pages() keeps its resumability state and the raw page HTML.

    Attributes:
        state_file: JSON file holding {"next_url": ..., "pages_captured": ...}.
        captured_dir: Directory the raw pages are saved into, as
            page_00001.html, page_00002.html, etc.
    """

    state_file: str = "_scrape_state.json"
    captured_dir: str = "_captured_pages"

    def resume_point(self, start_url):
        """(next_url, pages_captured) from state_file, or (start_url, 0) for a fresh capture."""
        if not os.path.exists(self.state_file):
            return start_url, 0
        with open(self.state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state["next_url"], state["pages_captured"]

    def save_page(self, pages_captured, page_source, next_url):
        """Write one captured page's HTML and the updated resumability state."""
        page_path = os.path.join(self.captured_dir, f"page_{pages_captured:05d}.html")
        with open(page_path, "w", encoding="utf-8") as f:
            f.write(page_source)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump({"next_url": next_url, "pages_captured": pages_captured}, f)


@dataclass
class _BrowserSession:
    """The current driver (None until attached) and the consecutive-crash count."""

    driver: object = None
    crashes: int = 0


class ScrapeRetriesExhausted(RuntimeError):
    """The browser session kept crashing; the scrape gave up after max_retries retries."""


def _retries_exhausted(attempts, exc):
    return ScrapeRetriesExhausted(
        f"browser session crashed {attempts} times in a row; giving up. Last error: {exc}"
    )


def _wait_for_results_table(driver, url, wait_timeout):
    """Wait up to wait_timeout seconds for the results table; carry on if it never appears."""
    try:
        WebDriverWait(driver, wait_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, RESULTS_TABLE_SELECTOR))
        )
    except TimeoutException:
        print(
            f"Results table never appeared for {url} "
            "(page may be blank/failed) — continuing anyway"
        )


def attach_to_chrome():
    """Default driver factory: attach to an already-running, verified Chrome session.

    Grad Cafe sits behind a Cloudflare challenge that must be cleared
    manually, so the scraper never launches its own browser; it attaches
    to one started with --remote-debugging-port=9222.
    """
    options = Options()
    options.add_experimental_option("debuggerAddress", DEBUGGER_ADDRESS)
    return ChromeWebDriver(options=options)


def _load_page(driver, url, wait_timeout):
    """Open url in driver, wait for its results table, and return the page HTML."""
    driver.get(url)
    _wait_for_results_table(driver, url, wait_timeout)
    return driver.page_source


def _load_with_retries(session, url, browser):
    """Return url's HTML, re-attaching after a browser crash (WebDriverException).

    Crashes are counted per session and reset after every successful page;
    more than browser.max_retries in a row raises ScrapeRetriesExhausted.
    """
    while True:
        try:
            if session.driver is None:
                session.driver = browser.new_driver()
            html = _load_page(session.driver, url, browser.wait_timeout)
        except WebDriverException as exc:
            session.crashes += 1
            if session.crashes > browser.max_retries:
                raise _retries_exhausted(session.crashes, exc) from exc
            print(f"Browser session crashed loading {url}: {exc}")
            print(
                "Retrying the same page with a fresh browser attach "
                f"({session.crashes}/{browser.max_retries})."
            )
            session.driver = None
            time.sleep(browser.crash_retry_wait)
            continue
        session.crashes = 0
        return html


def _capture_stop_reason(next_url, current_url, pages_captured, target_pages):
    """Why capturing should stop after this page, or None to keep going."""
    if next_url is None:
        return "no next-page link found"
    if next_url == current_url:
        return "next_url did not advance from the current page"
    if target_pages is not None and pages_captured >= target_pages:
        return "reached target_pages"
    return None


def capture_pages(browser=None, files=None, target_pages=None):
    """
    Capture raw HTML for Grad Cafe survey result pages, without parsing them.

    Page capture is kept separate from parsing so that a bug in the
    parsing logic (_group_entry_rows/_parse_entry/etc.) never requires
    re-scraping the live site to fix — the raw HTML for every page
    visited is saved to files.captured_dir, and parse_captured_pages() can
    be re-run against those saved files as many times as needed after a
    fix, independent of any live browser session.

    Resumability:
        Like the original combined scrape, a capture run can be
        interrupted partway through. Progress is persisted to
        files.state_file after every page as {"next_url": ...,
        "pages_captured": ...}. On startup, if state_file exists,
        capturing resumes from its "next_url" (continuing the running
        "pages_captured" count used for output filenames) instead of
        starting over from browser.start_url. If the previously saved
        state already recorded next_url as None (meaning a prior run
        reached the last page), this call captures nothing and returns 0
        immediately.

    Args:
        browser: BrowserSettings (start_url, delay_seconds, wait_timeout,
            driver_factory); default BrowserSettings().
        files: CaptureFiles (state_file, captured_dir); default CaptureFiles().
        target_pages: If not None, stop once pages_captured reaches this
            count (a cumulative count that persists across resumed
            calls, not necessarily the number of pages captured in this
            particular call).

    Returns:
        int: The number of new pages captured during this call (not the
            cumulative total across all resumed runs).
    """
    browser = browser or BrowserSettings()
    files = files or CaptureFiles()
    os.makedirs(files.captured_dir, exist_ok=True)

    next_url, pages_captured = files.resume_point(browser.start_url)
    pages_captured_this_run = 0

    if next_url is None:
        stop_reason = "no next-page link found (already at the last page)"
    else:
        driver = browser.new_driver()
        current_url = next_url
        page_source = _load_page(driver, current_url, browser.wait_timeout)

        while True:
            pages_captured += 1
            pages_captured_this_run += 1
            next_url = _extract_next_url(BeautifulSoup(page_source, "html.parser"))
            files.save_page(pages_captured, page_source, next_url)

            stop_reason = _capture_stop_reason(next_url, current_url, pages_captured, target_pages)
            if stop_reason is not None:
                break

            time.sleep(browser.delay_seconds)
            current_url = next_url
            page_source = _load_page(driver, current_url, browser.wait_timeout)

    print(f"Pages captured this run: {pages_captured_this_run}")
    print(f"Stopped because: {stop_reason}")

    return pages_captured_this_run


def parse_captured_pages(captured_dir="_captured_pages"):
    """
    Parse all previously captured Grad Cafe result pages into entry records.

    Operates entirely on local HTML files already saved by capture_pages()
    (page_*.html inside captured_dir) — it never touches a live browser
    session, so it can be re-run as many times as needed (e.g. after
    fixing a bug in _parse_entry()) without re-scraping the site.

    Args:
        captured_dir: Directory containing the captured page_*.html files.

    Returns:
        list: The full flat list of parsed, filtered applicant entry
            records across all captured pages.
    """
    page_paths = sorted(glob.glob(os.path.join(captured_dir, "page_*.html")))

    all_entries = []
    for page_path in page_paths:
        with open(page_path, "r", encoding="utf-8") as f:
            all_entries.extend(parse_page(f.read()))

    print(f"Pages read: {len(page_paths)}")
    print(f"Total valid entries parsed: {len(all_entries)}")

    return all_entries


def parse_page(html):
    """Parse one survey results page's HTML into filtered applicant entry records."""
    soup = BeautifulSoup(html, "html.parser")
    tbody = soup.find("tbody", class_="tw-divide-y tw-divide-gray-200 tw-bg-white")
    tbody_rows = tbody.find_all("tr", recursive=False) if tbody else []

    non_ad_rows = [row for row in tbody_rows if not _is_ad_row(row)]

    grouped_entries = _group_entry_rows(non_ad_rows)
    parsed_entries = [_parse_entry(entry_rows) for entry_rows in grouped_entries]
    return _filter_valid_entries(parsed_entries)


def _fresh_entries(html, is_known, seen_urls):
    """Entries on a page whose URL is neither in the database nor already collected.

    Entries without a URL are dropped (they can't be loaded). The returned
    URLs are added to seen_urls.
    """
    entries = [e for e in parse_page(html) if e["URL"]]
    known = is_known([e["URL"] for e in entries])
    fresh = [e for e in entries if e["URL"] not in known and e["URL"] not in seen_urls]
    seen_urls.update(e["URL"] for e in fresh)
    return fresh


def _newest_stop_reason(found_new, collected, target_count, url, next_url):
    """Why a Pull Data scrape should stop after this page, or None to keep going."""
    if not found_new:
        return "a page had no new entries"
    if collected >= target_count:
        return f"collected at least {target_count} new entries"
    if next_url is None or next_url == url:
        return "reached the end of pagination"
    return None


def scrape_newest(is_known, browser=None, target_count=300):
    """
    Collect entries newer than what the database already has, newest first.

    Used by Pull Data. Every call starts at browser.start_url (the first,
    newest results page) -- there is no resume state across pulls -- and
    walks forward through pagination, keeping entries whose URL is_known()
    does not report. It stops after the first page that yields no new
    entries (everything older is already loaded), once at least
    target_count new entries are collected, or when pagination ends. Pages
    are parsed in memory; nothing is written to disk.

    Within a single call, a browser crash (WebDriverException) re-attaches
    and retries the page that failed, keeping everything collected so far.
    After browser.max_retries consecutive failed retries it raises
    ScrapeRetriesExhausted.

    Args:
        is_known: Callable taking a list of URLs and returning the set of
            those already in the database.
        browser: BrowserSettings (start_url, delay_seconds, driver_factory,
            max_retries, crash_retry_wait, wait_timeout); default
            BrowserSettings().
        target_count: Stop fetching further pages once at least this many
            new entries have been collected.

    Returns:
        list: New entry records (entries without a URL are dropped, since
            they can't be loaded).
    """
    browser = browser or BrowserSettings()
    session = _BrowserSession()
    new_entries = []
    seen_urls = set()
    url = browser.start_url
    pages_read = 0

    while True:
        html = _load_with_retries(session, url, browser)
        pages_read += 1
        fresh = _fresh_entries(html, is_known, seen_urls)
        new_entries.extend(fresh)

        next_url = _extract_next_url(BeautifulSoup(html, "html.parser"))
        stop_reason = _newest_stop_reason(
            bool(fresh), len(new_entries), target_count, url, next_url
        )
        if stop_reason is not None:
            break

        time.sleep(browser.delay_seconds)
        url = next_url

    print(
        f"Pages read: {pages_read}; new entries: {len(new_entries)}; "
        f"stopped because {stop_reason}."
    )
    return new_entries


def scrape_data(browser=None, files=None, target_count=60000, batch_size=150):
    """
    Scrape Grad Cafe survey results end-to-end, until target_count entries
    have been collected.

    This is a convenience wrapper around capture_pages() and
    parse_captured_pages(), built with crash resilience in mind: real
    overnight runs showed that a single long, uncapped capture_pages()
    call is fragile against the attached Chrome/Selenium session
    crashing partway through (observed as InvalidSessionIdException and
    WebDriverException after a few hundred to a couple thousand pages).
    Rather than one uncapped run, pages are captured in batches of
    batch_size at a time. Between batches this function re-reads
    state_file and re-parses everything captured so far, so a crash in
    one batch can never lose more than that batch's progress — and
    typically loses none, since capture_pages() itself persists
    next_url/pages_captured to state_file after every single page, not
    just at the end of a batch. If a batch call raises a session/driver
    crash, it's caught here, a short wait is taken, and the loop simply
    tries again: the next capture_pages() call re-attaches to Chrome
    (via its own debuggerAddress attach logic) and resumes from
    state_file's last saved position. This makes multi-hour scrapes safe
    to kick off and leave unattended. A batch that keeps crashing is retried
    at most browser.max_retries times in a row; after that
    ScrapeRetriesExhausted is raised instead of looping forever.

    capture_pages() and parse_captured_pages() are also fully usable on
    their own: e.g. parse_captured_pages() can be re-run by itself,
    without a browser at all, after fixing a parsing bug, to re-derive
    entries from HTML that was already captured in an earlier run.

    Args:
        browser: BrowserSettings, passed through to capture_pages(); its
            crash_retry_wait and max_retries also govern batch retries.
        files: CaptureFiles (state_file, captured_dir); default CaptureFiles().
        target_count: Stop once at least this many parsed entries exist
            across all captured pages.
        batch_size: Number of new pages to request per capture_pages()
            call.

    Returns:
        list: The full flat list of parsed applicant entry records.
    """
    browser = browser or BrowserSettings()
    files = files or CaptureFiles()
    crashes = 0
    while True:
        current_pages_captured = files.resume_point(None)[1]

        try:
            capture_pages(browser, files, target_pages=current_pages_captured + batch_size)
        except WebDriverException as exc:
            crashes += 1
            if crashes > browser.max_retries:
                raise _retries_exhausted(crashes, exc) from exc
            print(
                f"Browser session crashed during batch starting at page "
                f"{current_pages_captured + 1} (target "
                f"{current_pages_captured + batch_size}): {exc}"
            )
            print(
                "State was preserved up to the last successfully captured "
                "page; retrying with a fresh browser attach."
            )
            time.sleep(browser.crash_retry_wait)
            continue

        crashes = 0
        entries = parse_captured_pages(captured_dir=files.captured_dir)

        with open(files.state_file, "r", encoding="utf-8") as f:
            state = json.load(f)

        print(
            f"Progress: {state['pages_captured']} pages captured, "
            f"{len(entries)} entries parsed so far."
        )

        if state["next_url"] is None:
            print("Reached natural end of available pages.")
            break

        if len(entries) >= target_count:
            print(f"Target of {target_count} entries reached.")
            break

    return parse_captured_pages(captured_dir=files.captured_dir)


def _extract_next_url(soup):
    """
    Find the "Next" pagination link's URL on a parsed survey results page.

    Args:
        soup: A BeautifulSoup object for a full survey results page.

    Returns:
        str or None: The href of the "Next" link inside the
            <nav aria-label="Results pagination"> element, or None if
            that nav or a matching link isn't found.
    """
    nav = soup.find("nav", attrs={"aria-label": "Results pagination"})
    if nav is None:
        return None

    for link in nav.find_all("a"):
        if "next" in link.get_text(strip=True).lower():
            return link.get("href")

    return None


def _group_entry_rows(tbody_rows):
    """
    Group a flat list of <tr> tags from a <tbody> into per-entry lists.

    Each entry starts with a "main" row (no "tw-border-none" class),
    followed by zero or more "accessory" rows (badges, and optionally a
    comment row) that do have the "tw-border-none" class.

    Args:
        tbody_rows: A list of BeautifulSoup <tr> tags from a <tbody>.

    Returns:
        list: A list of lists, where each inner list is
            [main_row, accessory_row, ...] for one applicant entry.
    """
    entries = []
    for row in tbody_rows:
        classes = row.get("class") or []
        if "tw-border-none" in classes:
            if entries:
                entries[-1].append(row)
        else:
            entries.append([row])
    return entries


def _parse_main_row(cells, record):
    """Fill University, program, degree, Date Added, status and URL from the main row."""
    if len(cells) > 0:
        div = cells[0].find("div")
        if div is not None:
            record["University"] = div.get_text(strip=True)

    if len(cells) > 1:
        spans = cells[1].find_all("span")
        if spans:
            record["Program Name"] = spans[0].get_text(strip=True)
            record["Masters or PhD"] = spans[-1].get_text(strip=True)

    if len(cells) > 2:
        record["Date Added"] = cells[2].get_text(strip=True)

    if len(cells) > 3:
        _parse_status(cells[3].get_text(strip=True), record)

    if len(cells) > 4:
        record["URL"] = _entry_url(cells[4])


def _parse_status(status_text, record):
    """Fill Applicant Status (and its Acceptance/Rejection Date) from e.g. "Accepted on 8 Sep"."""
    match = STATUS_PATTERN.match(status_text)
    if match:
        status, date = match.group(1), match.group(2)
        record["Applicant Status"] = status
        if status in STATUS_DATE_FIELDS:
            record[STATUS_DATE_FIELDS[status]] = date


def _entry_url(cell):
    """The entry's absolute result URL from its link cell, or None if there is no link."""
    link = cell.find("a")
    href = link.get("href") if link is not None else None
    if not href:
        return None
    return SITE_ROOT + href if href.startswith("/") else href


def _parse_badges(badge_row, record):
    """Fill term, nationality, GRE and GPA from the badge row (first matching pattern wins)."""
    for badge in badge_row.find_all("div"):
        text = badge.get_text(strip=True)
        for pattern, field, group in BADGE_PATTERNS:
            match = pattern.match(text)
            if match:
                record[field] = match.group(group)
                break


def _parse_entry(entry_rows):
    """
    Parse a single grouped Grad Cafe result entry into a structured record.

    Args:
        entry_rows: A list of BeautifulSoup <tr> tags for one applicant
            entry, as produced by _group_entry_rows().

    Returns:
        dict: A structured record containing the fields extracted from
            the entry (every RECORD_FIELDS key; None where not found).
    """
    record = dict.fromkeys(RECORD_FIELDS)

    _parse_main_row(entry_rows[0].find_all("td"), record)

    if len(entry_rows) > 1:
        _parse_badges(entry_rows[1], record)

    if len(entry_rows) > 2:
        paragraph = entry_rows[2].find("p")
        if paragraph is not None:
            record["Comments"] = paragraph.get_text(strip=True)

    record["raw_entry_text"] = " | ".join(
        row.get_text(" ", strip=True) for row in entry_rows
    )

    return record


def _is_ad_row(tr):
    """
    Check whether a <tr> tag is an ad-placement row rather than applicant data.

    The Grad Cafe results table interleaves ad-slot rows (e.g. a div with
    id="results-ad-placement-1") among the applicant entry rows. These rows
    don't carry the "tw-border-none" class, so row-grouping mistakes them
    for standalone entries with no real data.

    Args:
        tr: A BeautifulSoup <tr> tag.

    Returns:
        bool: True if the row contains any descendant element whose id
            attribute starts with "results-ad-placement", False otherwise.
    """
    return tr.find(id=re.compile(r'^results-ad-placement')) is not None


def _filter_valid_entries(parsed_entries):
    """
    Filter out non-applicant entries from a list of parsed entry dicts.

    A safety-net filter (in addition to _is_ad_row) applied after
    _parse_entry() has run, in case a non-applicant row slips through
    row-grouping without being caught upstream.

    Args:
        parsed_entries: A list of dicts, as produced by _parse_entry().

    Returns:
        list: A new list containing only the entries whose "University"
            key is not None and not an empty/whitespace-only string.
    """
    return [
        entry
        for entry in parsed_entries
        if entry["University"] is not None and entry["University"].strip() != ""
    ]
