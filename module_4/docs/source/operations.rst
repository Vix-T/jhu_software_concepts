Operational notes
=================

Busy-state policy
-----------------

Only one pull runs at a time, and Update Analysis waits for it:

* **Lock first.** ``POST /pull-data`` acquires the lock (owned by the app's
  own process) *before* launching anything; only then is the pull
  subprocess started and ownership moved to its PID
  (``set_owner``). A request that finds the lock held gets
  ``409 {"busy": true}`` without launching anything.
* **Update Analysis** also answers ``409 {"busy": true}`` while a pull is
  running, without recomputing.
* **Stale locks** clean themselves up. The lock file records the holder's
  PID and start time. If that process has exited (reaped with
  ``waitpid``, or found dead by ``os.kill(pid, 0)`` when it isn't this
  process's child) or the file is unreadable, the next status check removes
  it. There is no timeout, so a slow pull is never mistaken for a dead one.
* **Race-free.** Every read or change of the lock holds an exclusive
  ``fcntl.flock`` on a companion ``.scrape_lock.guard`` file, so
  check-then-acquire and check-then-clear are atomic across threads and
  processes. Two simultaneous Pull Data requests yield exactly one 202 and
  one 409; the test suite checks this 200 times per run.
* In-process pulls (used by tests) release the lock in a ``finally`` block
  on every path.

Idempotency and the uniqueness key
----------------------------------

* The Grad Café result URL is the natural key: ``url TEXT UNIQUE``.
  Records without a URL are rejected by the loader.
* Every insert is ``INSERT ... ON CONFLICT (url) DO NOTHING``, so loading the
  same data twice, or pulling an entry that's already present, inserts
  nothing and reports it as a skipped duplicate.
* Each insert runs inside its own savepoint: a database error on one record
  rolls back only that record, and it is reported as failed.
* All inserts of a batch share one transaction, committed after the last
  record. Any *unexpected* exception mid-batch rolls back the whole batch,
  so a failed pull never leaves partial writes.

Pulls: newest first
-------------------

* Every pull starts at the first (newest) results page. There is no resume
  state across pulls.
* It keeps entries whose URL isn't already in the database, and stops at
  the first page that yields nothing new, once 300 new entries are collected
  (``TARGET_COUNT``), or at the end of pagination.
* Pull Data needs Chrome with remote debugging on port 9222 and Grad Café's
  Cloudflare challenge already cleared; the port is checked first, so a
  missing browser fails in milliseconds.

Retry cap
---------

If the browser session crashes mid-pull, the scraper re-attaches and
retries the page that failed, keeping what it has collected. After
``max_retries`` (default 3) consecutive failed retries it raises
``ScrapeRetriesExhausted``; the pull is recorded as failed and the
subprocess exits with status 1 (which releases the lock).

Pull-result reporting
---------------------

Every pull, successful or not, writes its outcome atomically to
``src/_pull_data_result.json`` (or ``PULL_RESULT_FILE`` / the
``PULL_RESULT_PATH`` app config key):
``{ok, inserted, skipped, failed, error, finished_at}``. The page shows it
as "Last pull succeeded: N new rows", "Last pull failed: <reason>" or "No
pull has run yet.", and ``GET /pull-status`` returns it as JSON.
