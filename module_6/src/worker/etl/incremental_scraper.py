"""Incremental Grad Cafe scraper for the worker's scrape_new_data task.

scrape_newest() walks the survey results pages newest first, keeping
entries whose URL is_known() doesn't report, and stops at the first page
with nothing new (everything older is already loaded), once target_count
new entries are collected, or at the end of pagination. The worker's
is_known is known_by_watermark(): an entry is known when its result ID
(the <id> in /result/<id>) is at or below the ingestion watermark.

Grad Cafe sits behind a Cloudflare challenge that must be cleared by hand,
so the scraper never launches a browser: it attaches to a Chrome already
running with remote debugging, at CHROME_DEBUGGER_ADDRESS (host:port,
default 127.0.0.1:9222). Chrome's DevTools endpoint rejects requests whose
Host header is a name other than localhost, so any other hostname (e.g.
host.docker.internal from inside a container) is resolved to an IP before
attaching. If nothing is listening there, scrape_new_entries() fails fast
with PullPreconditionError instead of letting Selenium hang.

Pages are parsed in memory with BeautifulSoup; nothing is written to disk.
A browser crash mid-scrape (WebDriverException) re-attaches and retries the
same page, up to BrowserSettings.max_retries times in a row.
"""

import ipaddress
import logging
import os
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import load_data

logger = logging.getLogger(__name__)

DEBUGGER_ENV = "CHROME_DEBUGGER_ADDRESS"
DEFAULT_DEBUGGER_ADDRESS = "127.0.0.1:9222"
# Path to a chromedriver binary (the worker image bakes one in). Unset: Selenium
# Manager locates or downloads a driver itself.
CHROMEDRIVER_ENV = "CHROMEDRIVER_PATH"
# Seconds the pre-flight connect to the debugger port may take.
PORT_CHECK_TIMEOUT = 3
# Upper bound on new entries collected by one scrape, so a single task stays
# short even after a long gap. Normally a scrape stops much earlier, at the
# first page with nothing new.
TARGET_COUNT = 300

CLOUDFLARE_PRECONDITION_MESSAGE = (
    "PULL FAILED: could not attach to Chrome for scraping.\n"
    "This scraper requires a Chrome browser already running with remote "
    "debugging enabled (--remote-debugging-port), reachable at "
    "CHROME_DEBUGGER_ADDRESS, with Grad Cafe's Cloudflare challenge already "
    "manually cleared in that session -- it does not launch or solve anything "
    "itself. Start that session first, then click Pull Data again."
)

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
        start_url: The survey URL to start from (the first, newest results page).
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


class PullPreconditionError(RuntimeError):
    """No usable Chrome debugging session: bad address, unknown host, or nothing listening."""


def _is_ip_literal(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _attachable_host(host):
    """`host` as Chrome's DevTools endpoint accepts it: an IP literal or "localhost".

    Any other hostname is resolved to an IPv4 address; PullPreconditionError
    if it can't be resolved.
    """
    if host == "localhost" or _is_ip_literal(host):
        return host
    try:
        return socket.gethostbyname(host)
    except OSError as exc:  # socket.gaierror: unknown host, or no DNS
        logger.error("Cannot resolve the Chrome debugger host %r: %s", host, exc)
        raise PullPreconditionError(f"cannot resolve Chrome debugger host {host!r}: {exc}") from exc


def debugger_address():
    """(host, port) to attach to, from $CHROME_DEBUGGER_ADDRESS (default 127.0.0.1:9222).

    The host comes back as an IP literal or "localhost" (see
    _attachable_host). Raises PullPreconditionError if the setting isn't
    host:port or the host can't be resolved.
    """
    text = os.environ.get(DEBUGGER_ENV, "").strip() or DEFAULT_DEBUGGER_ADDRESS
    host, separator, port_text = text.rpartition(":")
    if not separator or not host or not port_text.isdigit():
        raise PullPreconditionError(f"{DEBUGGER_ENV} must be host:port, got {text!r}")
    return _attachable_host(host), int(port_text)


def attach_to_chrome():
    """Default driver factory: attach to the already-running, verified Chrome session.

    With $CHROMEDRIVER_PATH set, that chromedriver is used as-is, so Selenium
    Manager never runs (and never downloads anything).
    """
    host, port = debugger_address()
    options = Options()
    options.add_experimental_option("debuggerAddress", f"{host}:{port}")
    driver_path = os.environ.get(CHROMEDRIVER_ENV, "").strip()
    service = ChromeService(executable_path=driver_path) if driver_path else None
    return ChromeWebDriver(options=options, service=service)


def _debugger_port_open(host, port):
    """Fast pre-flight check so a missing Chrome session fails in milliseconds.

    Without it, Selenium's own setup was observed to hang for 30+ seconds
    when nothing listens on the debugger port. A raw socket connect to the
    same address fails (or succeeds) almost at once.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(PORT_CHECK_TIMEOUT)
        try:
            s.connect((host, port))
        except OSError:
            return False
        return True


def chrome_is_listening():
    """True if something accepts connections at CHROME_DEBUGGER_ADDRESS."""
    return _debugger_port_open(*debugger_address())


def known_by_watermark(watermark):
    """is_known(urls) for scrape_newest(): the URLs whose result ID is at or below `watermark`.

    With no watermark (None) nothing is known. URLs without a result ID are
    never known; ON CONFLICT (url) DO NOTHING keeps them from being stored twice.
    """

    def is_known(urls):
        if watermark is None:
            return set()
        known = set()
        for url in urls:
            rid = load_data.result_id(url)
            if rid is not None and rid <= watermark:
                known.add(url)
        return known

    return is_known


def scrape_new_entries(
    watermark, target_count=TARGET_COUNT, browser=None, port_check=chrome_is_listening
):
    """Scrape the Grad Cafe entries newer than `watermark` (a result ID, or None), newest first.

    browser is a BrowserSettings, passed through to scrape_newest(). With the
    default driver_factory (attach to the real Chrome session), port_check()
    runs first and PullPreconditionError is raised if it reports nothing
    listening; a caller-supplied driver_factory skips it.
    """
    browser = browser or BrowserSettings()
    if browser.driver_factory is None and not port_check():
        raise PullPreconditionError(CLOUDFLARE_PRECONDITION_MESSAGE)
    return scrape_newest(known_by_watermark(watermark), browser, target_count=target_count)
