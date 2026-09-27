Overview & setup
================

Prerequisites
-------------

* Python 3.12
* PostgreSQL (developed against 15, tested in CI against 16), with two
  databases: one for the app and one for the test suite. The test database's
  name must end in ``_test``.
* For **Pull Data** only: Google Chrome started with
  ``--remote-debugging-port=9222``, with Grad Café's Cloudflare challenge
  already cleared by hand in that browser session. Nothing else needs a
  browser; the test suite uses fakes.

Fresh-clone setup
-----------------

.. code-block:: bash

   git clone https://github.com/Vix-T/jhu_software_concepts.git
   cd jhu_software_concepts
   python -m pip install -r module_4/requirements.txt
   createdb jhu_module4
   createdb jhu_module4_test

Then create ``module_4/.env``:

.. code-block:: bash

   DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/jhu_module4
   TEST_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/jhu_module4_test

The password is optional for local trust authentication
(``postgresql://USER@localhost:5432/jhu_module4``).

Environment variables
---------------------

These are all the environment variables the code reads. A variable already
set in the environment wins over ``module_4/.env``.

.. list-table::
   :header-rows: 1
   :widths: 22 18 60

   * - Variable
     - Read by
     - Purpose
   * - ``DATABASE_URL``
     - ``config.py``
     - Required. The app/loader database, as
       ``postgresql://USER[:PASSWORD]@HOST:PORT/DBNAME``. Read from the
       environment, falling back to ``module_4/.env``.
   * - ``TEST_DATABASE_URL``
     - ``tests/conftest.py``
     - Required to run the tests. Its database name must end in ``_test``.
       For the whole test run, ``DATABASE_URL`` is overridden with this value.
   * - ``PULL_RESULT_FILE``
     - ``pull_data.py``
     - Optional. Where a pull records its outcome (default
       ``src/_pull_data_result.json``). The app sets it for the pull
       subprocess it launches.
   * - ``SECRET_KEY``
     - ``app.py``
     - Optional. Flask secret key; if unset (and not given in the app
       config), a random key is generated at startup.

``BUSY_LOCK_PATH`` and ``PULL_RESULT_PATH`` are **not** environment
variables: they are keys of the ``config`` dict passed to
:func:`app.create_app` (see :doc:`architecture`), alongside
``DATABASE_URL``, ``SECRET_KEY`` and standard Flask settings such as
``TESTING``.

Loading the bundled dataset
---------------------------

The cleaned Module 2 dataset ships with the repository as
``module_4/data/llm_extend_applicant_data_full.json.gz`` (4.0 MB, gzipped
from 50.5 MB). Load it once:

.. code-block:: bash

   cd module_4
   python src/load_data.py

This creates the ``applicants`` table if needed and reports
``Inserted: 60024``, ``Skipped duplicates: 0``, ``Failed to parse: 1``: the
file holds 60,025 records, one of which has no URL (the table's natural key)
and is skipped. Afterwards the page shows a Fall 2026 applicant count (Q1)
of 32,344. To load another file, pass its path (``.json`` or ``.json.gz``)
as the first argument.

If the database can't be reached, the loader exits with status 1 and a
message naming ``DATABASE_URL``; see :doc:`troubleshooting`.

Running the app
---------------

.. code-block:: bash

   cd module_4
   python src/app.py

Then open http://127.0.0.1:5000/. On an empty database the page shows
"N/A" answers rather than failing (the table is created on first use).

Running the tests
-----------------

From the **repository root**:

.. code-block:: bash

   pytest -c module_4/pytest.ini module_4/tests

The run enforces 100% statement coverage of ``module_4/src``
(``--cov-fail-under=100`` in ``pytest.ini``). See :doc:`testing`.
