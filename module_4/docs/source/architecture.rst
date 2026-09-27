Architecture
============

The code in ``module_4/src`` has three layers.

Web layer
---------

:func:`app.create_app` builds the Flask application; there is no
module-level ``app`` object. Every external dependency is an optional
keyword argument, so tests can inject fakes and production uses real
defaults:

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Argument
     - Default (production)
   * - ``scraper``
     - ``None``: Pull Data launches ``pull_data.py`` as a background
       subprocess and answers 202. When given, the pull runs in-process
       (via :func:`pull_data.run_pull`) and answers 200.
   * - ``loader``
     - :func:`load_data.load_into_database` on ``DATABASE_URL``.
   * - ``analysis_fn``
     - :func:`orm_queries.get_analysis` on ``DATABASE_URL`` (creating the
       table on first use).
   * - ``refresh_fn``
     - Recompute the analysis snapshot shown on the page.
   * - ``busy_state``
     - :class:`busy_state.FileLockBusyState` on ``src/.scrape_lock`` (or the
       ``BUSY_LOCK_PATH`` config key).
   * - ``pull_launcher``
     - ``subprocess.Popen`` of ``pull_data.py``.

The page (``templates/analysis.html``) shows every question with an
"Answer:" label, the two action buttons, a "Last pull" line, and a
"pull in progress" banner while a pull runs. The buttons post with
``fetch()`` and show the JSON result.

**Analysis snapshot.** The page renders a cached snapshot of the analysis,
computed on the first page load and recomputed only by Update Analysis. New
rows from a pull therefore appear after Update Analysis is clicked.

**Busy state.** One pull may run at a time, across threads and processes:
see :doc:`operations`.

ETL layer
---------

.. code-block:: text

   scrape.py            pull_data.py                 load_data.py
   ─────────            ────────────                 ────────────
   scrape_newest() ──►  scrape_new_entries()  ──►    load_into_database()
   (Selenium page       run_pull(scraper, loader)    load_rows(): INSERT ...
    loads, parse_page)  main(): CLI + result file    ON CONFLICT (url) DO NOTHING

* :mod:`scrape` drives an already-verified Chrome session through a
  ``driver_factory`` seam and parses results pages with BeautifulSoup.
  :func:`scrape.scrape_newest` is the Pull Data scraper;
  :func:`scrape.scrape_data` / :func:`scrape.capture_pages` are the
  resumable historical backfill used in Module 2.
* :mod:`pull_data` checks that Chrome's debugging port is open, scrapes the
  newest entries not yet in the database, loads them, and records the
  outcome in a result file.
* :mod:`load_data` maps records to rows and inserts them idempotently.

DB layer
--------

A single table, created by :data:`load_data.CREATE_TABLE_SQL` and mapped by
:class:`models.Applicant`:

.. code-block:: sql

   CREATE TABLE IF NOT EXISTS applicants (
       p_id SERIAL PRIMARY KEY,
       program TEXT, comments TEXT, date_added DATE,
       url TEXT UNIQUE,                 -- the natural key
       status TEXT, term TEXT, us_or_international TEXT,
       gpa FLOAT, gre FLOAT, gre_v FLOAT, gre_aw FLOAT, degree TEXT,
       llm_generated_program TEXT, llm_generated_university TEXT
   );

* ``url`` is ``UNIQUE``: every insert is ``ON CONFLICT (url) DO NOTHING``,
  so re-loading or re-pulling the same entry is a no-op.
* :mod:`query_data` answers the questions with raw SQL (psycopg2);
  :mod:`orm_queries` answers them with the SQLAlchemy ORM and reuses
  ``query_data``'s regex patterns. Running ``python src/orm_queries.py``
  prints both side by side.
* :func:`orm_queries.get_analysis` gathers everything the page shows into
  one dict. Empty data yields ``None`` values, rendered as "N/A".

Pull → update → render
----------------------

.. code-block:: text

   Browser                     Flask app                         PostgreSQL
   ───────                     ─────────                         ──────────
   [Pull Data] ── POST /pull-data ──► busy? ── yes ──► 409 {"busy": true}
                                        │ no
                                        ▼
                               acquire lock, launch pull_data.py ──► 202 {"ok": true}
                                        │  (subprocess)
                                        ▼
                               scrape newest pages ──► INSERT ... ON CONFLICT ──► rows
                               write result file; exit (lock released)

   [Update Analysis] ─ POST /update-analysis ─► busy? ── yes ──► 409 {"busy": true}
                                        │ no
                                        ▼
                               refresh_fn(): get_analysis() ◄──── SELECT ... ──── rows
                               store snapshot ──► 200 {"ok": true}

   (page reload) ──── GET /analysis ──► render snapshot + last pull result
