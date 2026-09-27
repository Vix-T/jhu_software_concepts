Troubleshooting
===============

``DATABASE_URL is not set``
   ``config.get_database_url()`` found no ``DATABASE_URL`` in the
   environment or in ``module_4/.env``. Create ``module_4/.env`` with
   ``DATABASE_URL=postgresql://USER[:PASSWORD]@HOST:PORT/DBNAME`` (see
   :doc:`overview`).

``LOAD FAILED: could not connect to PostgreSQL ...``
   The loader couldn't reach the server or write to the table; the line
   after it shows the underlying error. Check that PostgreSQL is running
   (``pg_isready``), that ``DATABASE_URL`` has the right host, port and
   database, and that the database exists (``createdb``).

The page returns 500 or pytest reports connection errors
   Same cause: the server isn't running or the URL is wrong. Tests use
   ``TEST_DATABASE_URL``, not ``DATABASE_URL``.

``Refusing to run: TEST_DATABASE_URL points at database '...'``
   The test suite only runs against a database whose name ends in
   ``_test``, to protect development data. Create one
   (``createdb jhu_module4_test``) and point ``TEST_DATABASE_URL`` at it.

``TEST_DATABASE_URL is not set``
   Add it to the environment or to ``module_4/.env``.

"Last pull failed: could not attach to Chrome for scraping"
   Pull Data found nothing listening on port 9222. Start Chrome with
   ``--remote-debugging-port=9222``, open https://www.thegradcafe.com/survey/
   in it, clear the Cloudflare check by hand, then click Pull Data again.

pytest can't find tests, markers, or ``--cov`` paths
   Run pytest from the **repository root** with
   ``-c module_4/pytest.ini``: the coverage path (``module_4/src``) is
   relative to the directory you run from.

Coverage failure when running one file or one marker
   Expected: only the full suite covers all of ``src/``.

Pull Data keeps answering 409
   A pull is still running, or its lock file was left behind. A lock whose
   process has exited is removed automatically on the next request; check
   ``GET /pull-status``. If a lock survives while no pull process exists,
   stop the app and delete ``module_4/src/.scrape_lock``.

The numbers didn't change after a pull
   The page shows the analysis as of the last Update Analysis; click Update
   Analysis after the pull finishes.

CI fails but tests pass locally
   Check the "Run tests" step's log. CI uses a fresh ``postgres:16``
   container and the pinned versions in ``module_4/requirements.txt``;
   make sure local installs match the pins.
