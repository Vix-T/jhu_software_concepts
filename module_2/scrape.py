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

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


def capture_pages(
    start_url="https://www.thegradcafe.com/survey/",
    target_pages=None,
    delay_seconds=2.5,
    state_file="_scrape_state.json",
    captured_dir="_captured_pages",
):
    """
    Capture raw HTML for Grad Cafe survey result pages, without parsing them.

    Page capture is kept separate from parsing so that a bug in the
    parsing logic (_group_entry_rows/_parse_entry/etc.) never requires
    re-scraping the live site to fix — the raw HTML for every page
    visited is saved to captured_dir, and parse_captured_pages() can be
    re-run against those saved files as many times as needed after a fix,
    independent of any live browser session.

    Resumability:
        Like the original combined scrape, a capture run can be
        interrupted partway through. Progress is persisted to state_file
        after every page as {"next_url": ..., "pages_captured": ...}. On
        startup, if state_file exists, capturing resumes from its
        "next_url" (continuing the running "pages_captured" count used
        for output filenames) instead of starting over from start_url. If
        the previously saved state already recorded next_url as None
        (meaning a prior run reached the last page), this call captures
        nothing and returns 0 immediately.

    Args:
        start_url: The survey URL to start from when there is no prior
            saved state to resume from.
        target_pages: If not None, stop once pages_captured reaches this
            count (a cumulative count that persists across resumed
            calls, not necessarily the number of pages captured in this
            particular call).
        delay_seconds: Seconds to sleep between page loads, to avoid
            hammering the site.
        state_file: Path to the JSON file used to persist resumability
            state (next_url, pages_captured).
        captured_dir: Directory to save each captured page's raw HTML
            into, as page_00001.html, page_00002.html, etc.

    Returns:
        int: The number of new pages captured during this call (not the
            cumulative total across all resumed runs).
    """
    os.makedirs(captured_dir, exist_ok=True)

    if os.path.exists(state_file):
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        next_url = state["next_url"]
        pages_captured = state["pages_captured"]
    else:
        next_url = start_url
        pages_captured = 0

    pages_captured_this_run = 0
    stop_reason = None

    if next_url is None:
        stop_reason = "no next-page link found (already at the last page)"
    else:
        options = Options()
        options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        driver = webdriver.Chrome(options=options)

        current_url = next_url
        driver.get(current_url)

        while True:
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, "html.parser")

            pages_captured += 1
            pages_captured_this_run += 1
            page_path = os.path.join(
                captured_dir, f"page_{pages_captured:05d}.html"
            )
            with open(page_path, "w", encoding="utf-8") as f:
                f.write(page_source)

            next_url = _extract_next_url(soup)

            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(
                    {"next_url": next_url, "pages_captured": pages_captured}, f
                )

            if next_url is None:
                stop_reason = "no next-page link found"
                break
            if next_url == current_url:
                stop_reason = "next_url did not advance from the current page"
                break
            if target_pages is not None and pages_captured >= target_pages:
                stop_reason = "reached target_pages"
                break

            time.sleep(delay_seconds)
            current_url = next_url
            driver.get(current_url)

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
            html = f.read()

        soup = BeautifulSoup(html, "html.parser")
        tbody = soup.find("tbody", class_="tw-divide-y tw-divide-gray-200 tw-bg-white")
        tbody_rows = tbody.find_all("tr", recursive=False) if tbody else []

        non_ad_rows = [row for row in tbody_rows if not _is_ad_row(row)]

        grouped_entries = _group_entry_rows(non_ad_rows)
        parsed_entries = [_parse_entry(entry_rows) for entry_rows in grouped_entries]
        filtered_entries = _filter_valid_entries(parsed_entries)

        all_entries.extend(filtered_entries)

    print(f"Pages read: {len(page_paths)}")
    print(f"Total valid entries parsed: {len(all_entries)}")

    return all_entries


def scrape_data(
    target_count=60000,
    start_url="https://www.thegradcafe.com/survey/",
    delay_seconds=2.5,
    state_file="_scrape_state.json",
    captured_dir="_captured_pages",
):
    """
    Scrape Grad Cafe survey results end-to-end, until target_count entries
    have been collected.

    This is a thin convenience wrapper around capture_pages() and
    parse_captured_pages() — it repeatedly captures a small batch of pages
    (raw HTML, via a live attached browser) and then re-parses everything
    captured so far to check the running entry total, stopping once
    target_count is reached or a capture batch makes no forward progress
    (0 new pages captured, meaning capture_pages() hit a stopping
    condition such as reaching the last page).

    capture_pages() and parse_captured_pages() are also fully usable on
    their own: e.g. parse_captured_pages() can be re-run by itself,
    without a browser at all, after fixing a parsing bug, to re-derive
    entries from HTML that was already captured in an earlier run.

    Args:
        target_count: Stop once at least this many parsed entries exist
            across all captured pages.
        start_url: The survey URL to start from when there is no prior
            saved state to resume from.
        delay_seconds: Seconds to sleep between page loads, to avoid
            hammering the site.
        state_file: Path to the JSON file used to persist capture
            resumability state.
        captured_dir: Directory holding captured page_*.html files.

    Returns:
        list: The full flat list of parsed applicant entry records.
    """
    batch_size = 20

    while True:
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                current_pages_captured = json.load(f)["pages_captured"]
        else:
            current_pages_captured = 0

        new_pages = capture_pages(
            start_url=start_url,
            target_pages=current_pages_captured + batch_size,
            delay_seconds=delay_seconds,
            state_file=state_file,
            captured_dir=captured_dir,
        )

        entries = parse_captured_pages(captured_dir=captured_dir)

        if len(entries) >= target_count or new_pages == 0:
            break

    return parse_captured_pages(captured_dir=captured_dir)


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


def _parse_entry(entry_rows):
    """
    Parse a single grouped Grad Cafe result entry into a structured record.

    Args:
        entry_rows: A list of BeautifulSoup <tr> tags for one applicant
            entry, as produced by _group_entry_rows().

    Returns:
        dict: A structured record containing the fields extracted from
            the entry.
    """
    record = {
        "Program Name": None,
        "University": None,
        "Comments": None,
        "Date Added": None,
        "URL": None,
        "Applicant Status": None,
        "Acceptance Date": None,
        "Rejection Date": None,
        "Semester and Year": None,
        "International/American": None,
        "GRE Score": None,
        "GRE V Score": None,
        "GRE AW Score": None,
        "Masters or PhD": None,
        "GPA": None,
        "raw_entry_text": None,
    }

    main_row = entry_rows[0]
    cells = main_row.find_all("td")

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
        status_text = cells[3].get_text(strip=True)
        match = re.match(
            r'^(Accepted|Rejected|Wait listed|Interview)(?:\s+on\s+(.+))?$',
            status_text,
        )
        if match:
            status, date = match.group(1), match.group(2)
            record["Applicant Status"] = status
            if status == "Accepted":
                record["Acceptance Date"] = date
            elif status == "Rejected":
                record["Rejection Date"] = date

    if len(cells) > 4:
        link = cells[4].find("a")
        if link is not None:
            href = link.get("href")
            if href:
                if href.startswith("/"):
                    record["URL"] = "https://www.thegradcafe.com" + href
                else:
                    record["URL"] = href

    if len(entry_rows) > 1:
        for badge in entry_rows[1].find_all("div"):
            text = badge.get_text(strip=True)

            m = re.match(r'^(Fall|Spring|Summer|Winter)\s+\d{4}$', text)
            if m:
                record["Semester and Year"] = text
                continue

            m = re.match(r'^(International|American|Other)$', text)
            if m:
                record["International/American"] = text
                continue

            m = re.match(r'^GRE V\s+([\d.]+)$', text)
            if m:
                record["GRE V Score"] = m.group(1)
                continue

            m = re.match(r'^GRE AW\s+([\d.]+)$', text)
            if m:
                record["GRE AW Score"] = m.group(1)
                continue

            m = re.match(r'^GRE\s+(\d+)$', text)
            if m:
                record["GRE Score"] = m.group(1)
                continue

            m = re.match(r'^GPA\s+([\d.]+)$', text)
            if m:
                record["GPA"] = m.group(1)
                continue

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
