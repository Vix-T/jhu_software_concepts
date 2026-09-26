"""Pull Data: scrape new Grad Cafe entries and load them into the DB.

Split into two injectable pieces plus a thin CLI:

- scrape_new_entries(...) -> list[dict]: the scraper (Selenium attach via
  scrape.py; its driver_factory seam lets tests substitute a fake driver).
- run_pull(scraper, loader) -> dict: calls scraper(), hands the records to
  loader(records) -> (inserted, skipped, failed), and returns the counts.
  app.py's in-process Pull Data path calls this directly with injected
  callables.
- main(): the CLI app.py's default Pull Data route launches as a background
  subprocess (see busy_state.py for the lock that prevents two pulls from
  running at once).

Pull Data deliberately does NOT run the LLM-cleaning step -- newly
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

import os
import socket
import sys

from selenium.common.exceptions import WebDriverException

import load_data
from scrape import scrape_data

BASE_DIR = os.path.dirname(__file__)
PULL_STATE_FILE = os.path.join(BASE_DIR, "_pull_data_state.json")
PULL_CAPTURED_DIR = os.path.join(BASE_DIR, "_pull_data_pages")

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


class PullPreconditionError(RuntimeError):
    """Raised when there is no Chrome remote-debugging session to attach to."""


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


def scrape_new_entries(
    target_count=TARGET_COUNT,
    state_file=PULL_STATE_FILE,
    captured_dir=PULL_CAPTURED_DIR,
    driver_factory=None,
    **scrape_kwargs,
):
    """Scrape Grad Cafe entries for a Pull Data run and return them as records.

    With the default driver_factory (attach to the real Chrome session), the
    debugger port is checked first and PullPreconditionError is raised if
    nothing is listening. A caller-supplied driver_factory skips that check,
    since it doesn't attach to Chrome. Extra keyword arguments (e.g.
    delay_seconds, start_url) are passed through to scrape.scrape_data().
    """
    if driver_factory is None and not _debugger_port_open():
        raise PullPreconditionError(CLOUDFLARE_PRECONDITION_MESSAGE)

    return scrape_data(
        target_count=target_count,
        state_file=state_file,
        captured_dir=captured_dir,
        driver_factory=driver_factory,
        **scrape_kwargs,
    )


def run_pull(scraper, loader):
    """Scrape, then load. Exceptions from either step propagate to the caller.

    Args:
        scraper: Zero-argument callable returning a list of record dicts.
        loader: Callable taking that list and returning
            (inserted, skipped_duplicates, failed) like load_data.load_rows().

    Returns:
        dict: {"scraped": int, "inserted": int, "skipped": int, "failed": list}
    """
    entries = scraper()
    inserted, skipped, failed = loader(entries)
    return {
        "scraped": len(entries),
        "inserted": inserted,
        "skipped": skipped,
        "failed": failed,
    }


def main():
    try:
        result = run_pull(scrape_new_entries, load_data.load_into_database)
    except PullPreconditionError as exc:
        print(exc)
        sys.exit(1)
    except WebDriverException as exc:
        # The port was open (something is listening), but Selenium still
        # couldn't attach/negotiate a session with it -- a different,
        # rarer failure than the missing-Chrome case above.
        print(CLOUDFLARE_PRECONDITION_MESSAGE)
        print(f"Underlying error: {exc}")
        sys.exit(1)

    print(
        f"Pull complete: {result['scraped']} entries scraped, {result['inserted']} inserted, "
        f"{result['skipped']} already present, {len(result['failed'])} failed to parse."
    )


if __name__ == "__main__":
    main()
