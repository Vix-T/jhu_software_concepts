API reference
=============

HTTP routes
-----------

The route handlers are defined inside :func:`app.create_app`, so they are
listed here rather than by autodoc.

.. list-table::
   :header-rows: 1
   :widths: 8 16 34 42

   * - Method
     - Path
     - Purpose
     - Responses
   * - GET
     - ``/`` and ``/analysis``
     - Render the analysis page (current snapshot, last pull result, busy
       banner).
     - ``200`` HTML.
   * - POST
     - ``/pull-data``
     - Start a pull. The busy check comes before any scraper, loader or
       database call.
     - ``202 {"ok": true}``: pull subprocess launched.
       ``200 {"ok": true, "inserted": N}``: in-process pull finished (when a
       ``scraper`` is injected).
       ``409 {"busy": true}``: a pull is already running.
       ``500 {"ok": false, "error": "..."}``: the in-process pull or the
       subprocess launch failed; nothing from that pull is left in the
       database and the busy state is released.
   * - POST
     - ``/update-analysis``
     - Recompute the analysis snapshot (``refresh_fn``).
     - ``200 {"ok": true}``; ``409 {"busy": true}`` while a pull is running
       (``refresh_fn`` is not called).
   * - GET
     - ``/pull-status``
     - Report whether a pull is running and the last pull's outcome.
     - ``200 {"running": bool, "last_result": null | {...}}`` (see below).

``last_result`` is ``null`` before the first pull, otherwise::

   {"ok": true, "inserted": 2, "skipped": 0, "failed": 0,
    "error": null, "finished_at": "2026-09-27T17:33:44+00:00"}

``failed`` is a count of records that could not be loaded; ``error`` is a
message when ``ok`` is false.

Modules
-------

app
^^^

.. automodule:: app
   :members:

scrape
^^^^^^

.. automodule:: scrape
   :members:

pull_data
^^^^^^^^^

.. automodule:: pull_data
   :members:

load_data
^^^^^^^^^

.. automodule:: load_data
   :members:

query_data
^^^^^^^^^^

.. automodule:: query_data
   :members:

orm_queries
^^^^^^^^^^^

.. automodule:: orm_queries
   :members:

busy_state
^^^^^^^^^^

.. automodule:: busy_state
   :members:

config
^^^^^^

.. automodule:: config
   :members:

models
^^^^^^

.. automodule:: models
   :members:
   :exclude-members: metadata, registry
