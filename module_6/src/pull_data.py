"""Pull Data: fetch the newest Grad Cafe entries and load them into the DB.

Split into injectable pieces plus a thin CLI:

- scrape_new_entries(...) -> list[dict]: the scraper. Every pull starts at
  the first (newest) results page and stops at the first page whose entries
  are all already in the database (scrape.scrape_newest), so a pull fetches
  what's new rather than resuming deeper into history.
- run_pull(scraper, loader) -> dict: calls scraper(), hands the records to
  loader(records) -> (inserted, skipped, failed), and returns the counts.
  flask_app.py's in-process Pull Data path calls this directly with injected
  callables.
- main(): the CLI flask_app.py's default Pull Data route launches as a background
  subprocess (see busy_state.py for the lock that prevents two pulls from
  running at once). Every run -- success or failure -- records its outcome
  in a small JSON result file (write_pull_result) that the app reads to
  show the last pull's result.

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

import json
import logging
import os
import socket
import sys
import tempfile
from datetime import datetime, timezone

import psycopg2
from selenium.common.exceptions import WebDriverException

import load_data
from scrape import BrowserSettings, ScrapeRetriesExhausted, scrape_newest

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(__file__)
PULL_RESULT_FILE = os.path.join(BASE_DIR, "_pull_data_result.json")
# The app passes its configured result path to the pull subprocess via this variable.
RESULT_FILE_ENV = "PULL_RESULT_FILE"

# Upper bound on new entries collected by one pull, so a single Pull Data
# run's lock-held time stays short even after a long gap between pulls.
# Normally a pull stops much earlier, at the first page with nothing new.
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


# Recorded when a launched pull process ends without writing its own result.
CRASHED_PULL_ERROR = "pull process exited without reporting a result"

# Failures main() records and reports (exit 1); anything else is a bug and
# propagates with its traceback.
RECORDED_FAILURES = (
    ScrapeRetriesExhausted,
    load_data.TableMissingError,
    psycopg2.Error,
    OSError,
    load_data.ConfigError,
)


class PullPreconditionError(RuntimeError):
    """Raised when there is no Chrome remote-debugging session to attach to."""


def _debugger_port_open(host=DEBUGGER_HOST, port=DEBUGGER_PORT):
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
            s.connect((host, port))
            return True
        except OSError:
            return False


def scrape_new_entries(
    target_count=TARGET_COUNT,
    browser=None,
    port_check=_debugger_port_open,
    is_known=None,
):
    """Scrape the newest Grad Cafe entries not yet in the database.

    browser is a scrape.BrowserSettings (start_url, delay_seconds,
    driver_factory, max_retries, ...), passed through to
    scrape.scrape_newest(). With the default driver_factory (attach to the
    real Chrome session), the debugger port is checked first (port_check,
    default _debugger_port_open) and PullPreconditionError is raised if
    nothing is listening. A caller-supplied driver_factory skips that check,
    since it doesn't attach to Chrome. is_known(urls) -> set defaults to a
    lookup in the $DATABASE_URL database.
    """
    browser = browser or BrowserSettings()
    if browser.driver_factory is None and not port_check():
        raise PullPreconditionError(CLOUDFLARE_PRECONDITION_MESSAGE)

    return scrape_newest(is_known or load_data.existing_urls, browser, target_count=target_count)


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


def default_result_path():
    """The pull result file: $PULL_RESULT_FILE if set, else src/_pull_data_result.json."""
    return os.environ.get(RESULT_FILE_ENV) or PULL_RESULT_FILE


def pull_result(run=None, error=None):
    """Build the result record for a finished pull (run on success, error on failure)."""
    run = run or {}
    return {
        "ok": error is None,
        "inserted": run.get("inserted", 0),
        "skipped": run.get("skipped", 0),
        "failed": len(run.get("failed", [])),
        "error": error,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_pull_result(path, result):
    """Atomically write `result` as JSON to `path` (temp file + os.replace)."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".pull_result.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(result, f)
    os.replace(tmp_path, path)


def pending_result():
    """Result record written before a pull subprocess starts.

    It is already a failure record: the child replaces it with its real
    outcome when it finishes, so if it is still pending once the child is
    gone, the pull ended without reporting anything.
    """
    result = pull_result(error=CRASHED_PULL_ERROR)
    result["pending"] = True
    return result


def read_pull_result(path):
    """Return the last pull's result dict, or None if no (readable) result exists yet.

    A missing, unreadable (permissions, a directory) or undecodable file is
    logged and treated as "no result", so it can never break the page.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            result = json.load(f)
    except (OSError, ValueError) as exc:  # ValueError covers JSON and UTF-8 decode errors
        logger.debug("No readable pull result at %s: %s", path, exc)
        return None
    return result if isinstance(result, dict) else None


def settle_pull_result(path, running):
    """Return the last pull's result, resolving a pending record.

    While the pull is still running its pending record isn't a result yet
    (None). Once it isn't running, a record that is still pending means the
    process ended without writing its outcome: it is rewritten as a failure,
    timestamped now.
    """
    result = read_pull_result(path)
    if result is None or not result.get("pending"):
        return result
    if running:
        return None
    settled = pull_result(error=CRASHED_PULL_ERROR)
    write_pull_result(path, settled)
    logger.warning("Pull process ended without a result; recorded as failed in %s", path)
    return settled


def _fail(result_path, error, lines):
    write_pull_result(result_path, pull_result(error=error))
    for line in lines:
        print(line)
    sys.exit(1)


def main(scraper=None, loader=None, result_path=None):
    """CLI entry point; records the outcome in the result file and exits 1 on failure.

    scraper/loader default to scrape_new_entries and
    load_data.load_into_database ($DATABASE_URL database); result_path defaults to
    default_result_path().
    """
    result_path = result_path or default_result_path()
    try:
        run = run_pull(scraper or scrape_new_entries, loader or load_data.load_into_database)
    except PullPreconditionError as exc:
        _fail(result_path, str(exc), [str(exc)])
    except WebDriverException as exc:
        # The port was open (something is listening), but Selenium still
        # couldn't attach/negotiate a session with it -- a different,
        # rarer failure than the missing-Chrome case above.
        _fail(
            result_path,
            f"could not attach to Chrome: {exc}".strip(),
            [CLOUDFLARE_PRECONDITION_MESSAGE, f"Underlying error: {exc}"],
        )
    except RECORDED_FAILURES as exc:  # retries exhausted, database, file/socket, or no DATABASE_URL
        message = f"{type(exc).__name__}: {exc}"
        _fail(result_path, message, [f"PULL FAILED: {message}"])

    write_pull_result(result_path, pull_result(run=run))
    print(
        f"Pull complete: {run['scraped']} entries scraped, {run['inserted']} inserted, "
        f"{run['skipped']} already present, {len(run['failed'])} failed to parse."
    )


if __name__ == "__main__":
    main()
