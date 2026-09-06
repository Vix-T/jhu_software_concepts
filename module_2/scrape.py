"""
Scrapes graduate school applicant self-reported data from The Grad Cafe
(thegradcafe.com) survey results pages.

Uses urllib3 to fetch raw HTML pages, Selenium to drive a browser for any
pages requiring JavaScript rendering or interaction, and BeautifulSoup to
parse the resulting HTML into structured applicant entry records.
"""


def scrape_data():
    """
    Scrape Grad Cafe survey result entries from the site.

    Fetches one or more result pages (via urllib3 and/or Selenium) and
    parses each entry using _parse_entry().

    Returns:
        list: A list of parsed applicant entry records.
    """
    pass


def _parse_entry(entry_html):
    """
    Parse a single Grad Cafe result entry into a structured record.

    Args:
        entry_html: The raw HTML (or BeautifulSoup element) representing
            one applicant entry.

    Returns:
        dict: A structured record containing the fields extracted from
            the entry.
    """
    pass
