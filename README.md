# JHU Software Concepts

Coursework repository for **EN.605.256.82 — Modern Software Concepts in
Python**, Johns Hopkins University (Fall 2026).

Each module lives in its own folder, with its own setup and run instructions
included inside that folder.

## Modules

- **[module_1](./module_1)** — Personal portfolio site built with Flask,
  using the application factory pattern and Blueprints. See
  `module_1/README.txt` for setup and run instructions.

- **[module_2](./module_2)** — Web scraper and data-cleaning pipeline for
  thegradcafe.com admissions data, using Selenium, BeautifulSoup, and regex
  to collect applicant records, with a local LLM (TinyLlama) standardizing
  program/university names. See `module_2/README.txt` for setup, approach,
  and known limitations.

- **[module_3](./module_3)** — PostgreSQL database and dynamic Flask
  webpage for analyzing the Module 2 Grad Cafe dataset, with both raw
  SQL and a SQLAlchemy ORM layer answering the same set of questions,
  plus "Pull Data" (trigger a new scrape) and "Update Analysis"
  (re-query current results) functionality on the webpage. See
  `module_3/README.md` for setup, run instructions, and a SQL-vs-ORM
  comparison.

- **[module_4](./module_4)** — Automated testing and documentation for the
  Module 3 Grad Cafe analytics app. The code was refactored for testability
  (a `create_app` factory, dependency-injected scraper/loader/busy-state, and
  a single `DATABASE_URL` setting), then covered by a marked Pytest suite at
  100% coverage that runs against an isolated test database, with fake
  scrapers and no live network access. Tests run in GitHub Actions CI, and
  Sphinx documentation is published on
  [Read the Docs](https://jhu-software-concepts-vtalbot1.readthedocs.io). See
  `module_4/README.md` for setup, run instructions, and how to run the tests.

- **[module_5](./module_5)** — Security and supply-chain hardening of the
  Grad Cafe analytics app: SQL composed safely with `psycopg2.sql` and
  clamped `LIMIT`s, database settings from environment variables, a
  least-privilege `gradcafe_app` database role, Pylint 10.00/10 with
  specific exception handling, pinned dependencies with an editable
  `setup.py` install, a pydeps dependency graph, Snyk dependency and code
  scans, and a four-job GitHub Actions CI pipeline. See
  `module_5/README.md` for setup and `module_5/module_5_report.pdf` for
  the report.

More modules will be added here as the semester progresses.
