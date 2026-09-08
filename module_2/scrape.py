"""
Scrapes graduate school applicant self-reported data from The Grad Cafe
(thegradcafe.com) survey results pages.

Uses urllib3 to fetch raw HTML pages, Selenium to drive a browser for any
pages requiring JavaScript rendering or interaction, and BeautifulSoup to
parse the resulting HTML into structured applicant entry records.
"""

import re

from bs4 import BeautifulSoup


def scrape_data():
    """
    Scrape Grad Cafe survey result entries from the site.

    Fetches one or more result pages (via urllib3 and/or Selenium) and
    parses each entry using _parse_entry().

    Returns:
        list: A list of parsed applicant entry records.
    """
    pass


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
