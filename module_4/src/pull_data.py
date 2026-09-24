"""Standalone worker: scrape new Grad Cafe entries and load them into the DB.

Launched as a background subprocess by app.py's "Pull Data" route (see
scrape_lock.py for the locking mechanism that prevents two of these from
running at once). Deliberately does NOT run the LLM-cleaning step -- newly
scraped rows are loaded with llm_generated_program/llm_generated_university
left NULL. LLM cleaning stays a separate, later, manual step (clean.py),
consistent with how this pipeline has worked throughout the project.

Precondition (cannot be automated away -- see module_3/README.txt): a
Chrome browser must already be running with remote debugging enabled
(--remote-debugging-port=9222), with Grad Cafe's Cloudflare challenge
already manually cleared in that session. This script attaches to that
existing, already-verified session; it does not launch a browser or solve
the challenge itself. If that precondition isn't met, it fails fast with
an explanatory message instead of hanging.
"""

import json
import os
import socket
import sys

from selenium.common.exceptions import WebDriverException

import load_data
from scrape import scrape_data

BASE_DIR = os.path.dirname(__file__)
PULL_STATE_FILE = os.path.join(BASE_DIR, "_pull_data_state.json")
PULL_CAPTURED_DIR = os.path.join(BASE_DIR, "_pull_data_pages")
PULL_ENTRIES_FILE = os.path.join(BASE_DIR, "_pull_data_new_entries.json")

# A bounded, "check for what's new" pull -- not the original historical
# backfill's target_count=60000. Already-seen entries are silently skipped
# by load_data.py's ON CONFLICT (url) DO NOTHING, so over-fetching here is
# harmless; this just keeps a single Pull Data run's lock-held time short
# and checkable, per the Part 8 design.
TARGET_COUNT = 300

DEBUGGER_HOST = "127.0.0.1"
DEBUGGER_PORT = 9222

CLOUDFLARE_PRECONDITION_MESSAGE = (
    "PULL FAILED: could not attach to Chrome for scraping.\n"
    "This scraper requires a Chrome browser already running with remote "
    "debugging enabled (--remote-debugging-port=9222), with Grad Cafe's "
    "Cloudflare challenge already manually cleared in that session -- it "
    "does not launch or solve anything itself. Start that session first, "
    "then click Pull Data again."
)


def _debugger_port_open():
    """Fast pre-flight check so a missing Chrome session fails in ~milliseconds.

    webdriver.Chrome() itself does not fail fast here: with nothing
    listening on DEBUGGER_PORT, Selenium's own driver-manager setup was
    observed to hang for 30+ seconds with no exception before this check
    was added, entirely unrelated to the actual attach target. A raw
    socket connect to the same port fails (or succeeds) in under a
    millisecond, so we check that ourselves before ever invoking Selenium.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        try:
            s.connect((DEBUGGER_HOST, DEBUGGER_PORT))
            return True
        except OSError:
            return False


def main():
    if not _debugger_port_open():
        print(CLOUDFLARE_PRECONDITION_MESSAGE)
        sys.exit(1)

    try:
        entries = scrape_data(
            target_count=TARGET_COUNT,
            state_file=PULL_STATE_FILE,
            captured_dir=PULL_CAPTURED_DIR,
        )
    except WebDriverException as exc:
        # The port was open (something is listening), but Selenium still
        # couldn't attach/negotiate a session with it -- a different,
        # rarer failure than the missing-Chrome case above.
        print(CLOUDFLARE_PRECONDITION_MESSAGE)
        print(f"Underlying error: {exc}")
        sys.exit(1)

    with open(PULL_ENTRIES_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f)

    inserted, skipped, failed = load_data.main(data_file=PULL_ENTRIES_FILE)

    print(
        f"Pull complete: {len(entries)} entries scraped, {inserted} inserted, "
        f"{skipped} already present, {len(failed)} failed to parse."
    )


if __name__ == "__main__":
    main()
