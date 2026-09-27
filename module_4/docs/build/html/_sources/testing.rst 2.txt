Testing guide
=============

Running the tests
-----------------

Always run pytest from the **repository root**, pointing it at the Module 4
config:

.. code-block:: bash

   # the whole suite (135 tests, 100% coverage enforced)
   pytest -c module_4/pytest.ini module_4/tests

   # only one marker
   pytest -c module_4/pytest.ini -m buttons module_4/tests

   # all markers: selects the entire suite, since every test is marked
   pytest -c module_4/pytest.ini -m "web or buttons or analysis or db or integration" module_4/tests

A single-marker run exercises only part of ``src/``, so it reports a
coverage failure; that's expected. Only the full run must reach 100%.

Markers
-------

Every test carries at least one marker (``pytest.ini``). Counts are for the
current suite.

.. list-table::
   :header-rows: 1
   :widths: 14 8 78

   * - Marker
     - Tests
     - Covers
   * - ``web``
     - 6
     - App factory and routes, page rendering, buttons present, first run
       with no table, the ``__main__`` entry point.
   * - ``buttons``
     - 48
     - Pull Data / Update Analysis JSON contract and busy gating, the error
       path, busy-state implementations, simultaneous pulls, the default
       subprocess launcher, ``pull_data`` CLI and result reporting,
       ``/pull-status`` and the last-pull banner.
   * - ``analysis``
     - 37
     - "Answer:" labels, two-decimal percentages, N/A on empty data,
       regex patterns (evaluated by PostgreSQL), raw-SQL and ORM CLI
       output, Custom 2 tie ordering.
   * - ``db``
     - 19
     - Inserts, idempotency, required fields, row and analysis query
       functions, loader parsing helpers, loader CLI and failure handling,
       ``config``.
   * - ``integration``
     - 25
     - End-to-end pull → update → render, overlapping pulls, the scraper
       against synthetic pages, newest-first pulls, crash retries and the
       retry cap.

Selectors
---------

UI tests find elements by ``data-testid`` rather than by text or layout:

* ``pull-data-btn``: the Pull Data button
* ``update-analysis-btn``: the Update Analysis button
* ``action-status``: where the button script reports the JSON result
* ``last-pull``: the last pull outcome line

Test database and its guard
---------------------------

Tests use the database named by ``TEST_DATABASE_URL`` (environment first,
then ``module_4/.env``). ``conftest.py`` refuses to start, with
``pytest.UsageError`` (never a skip), if the variable is missing or its
database name doesn't end in ``_test``. For the whole run it also sets
``DATABASE_URL`` to the test URL, so no code path can fall back to the
development database. The ``applicants`` table is created with the app's own
``CREATE_TABLE_SQL`` and truncated before every test.

Fixtures and fakes
------------------

Everything below lives in ``module_4/tests/conftest.py``. Fakes replace only
external dependencies (the browser, the network, subprocesses); the code
under test always runs for real.

* **Database helpers:** ``db_conn``, ``seed(records)`` (inserts through the
  real ``load_rows``), ``row_count()``, ``fetch_rows()``.
* **Records:** ``make_record(i, **overrides)`` / ``make_records(n)`` build
  records in exactly the shape the real scraper produces.
* **FakeScraper:** returns a fixed list of records, or raises a given
  exception, and counts its calls.
* **Spy:** a recording wrapper; ``loader_spy`` wraps the real database
  loader, ``refresh_spy`` records Update Analysis calls.
* **InMemoryBusyState** (``busy_state`` fixture): the in-process
  implementation of the busy-state interface. File-lock tests use
  ``FileLockBusyState`` on a ``tmp_path`` lock file.
* **FakeDriver / DriverFactory:** stand in for Selenium, serving HTML by
  URL through ``scrape.py``'s ``driver_factory`` seam. ``find_element``
  raises Selenium's own ``NoSuchElementException``, so the real wait logic
  runs.
* **Synthetic HTML fixtures** (``tests/fixtures/``): hand-written pages in
  Grad Café's structure (entry rows, badge and comment rows, ad rows,
  pagination), not captured from the site: ``page_1.html``,
  ``page_2.html``, ``page_self_link.html``, ``page_no_results.html``, and
  the newest-first set ``pull_page_1.html`` to ``pull_page_3.html``.
* **make_app / app / client:** build the app on the test database with the
  fakes above; ``pull_result_path`` points each test's pull result file at
  ``tmp_path``.

Rules the suite follows
-----------------------

No coverage exclusions, skips, xfails or ``time.sleep``; no mocking of the
function or route under test; every test asserts observable behaviour
(status codes, JSON, rendered HTML, database rows). The full rules are in
``module_4/CLAUDE.md``.

Continuous integration
----------------------

``.github/workflows/tests.yml`` runs on every push and pull request to
``main``, and on demand (``workflow_dispatch``):

#. starts a ``postgres:16`` service container with database
   ``jhu_module4_test``, user ``postgres`` and trust authentication (no
   password anywhere), and waits for its ``pg_isready`` health check;
#. checks out the repository and sets up Python 3.12 with pip caching;
#. installs ``module_4/requirements.txt``;
#. runs, from the repository root, with
   ``TEST_DATABASE_URL=postgresql://postgres@localhost:5432/jhu_module4_test``::

      pytest -c module_4/pytest.ini -m "web or buttons or analysis or db or integration" module_4/tests

The job runs on ``ubuntu-24.04`` with a 10-minute timeout. No browser or
chromedriver is installed; the tests use the fakes above.
