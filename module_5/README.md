## Name

Vix Talbot (JHED: vtalbot1)

## Overview

Module 4 takes the Module 3 Grad Café analytics app and makes it testable,
tested and documented. The app loads cleaned Grad Café applicant data
(scraped and LLM-standardized in Module 2) into PostgreSQL, answers a fixed
set of questions with both raw SQL (`query_data.py`) and the SQLAlchemy ORM
(`orm_queries.py`), and shows them on a Flask page (`flask_app.py`) with "Pull
Data" (fetch the newest entries) and "Update Analysis" (recompute the
answers) buttons.

For Module 4 the code was refactored around a `create_app()` factory with
injectable dependencies (scraper, loader, busy state, analysis), covered by
a marked pytest suite at 100% statement coverage that runs against an
isolated test database with fakes instead of a browser or network, checked
in GitHub Actions CI, and documented with Sphinx on Read the Docs.

**Documentation:** https://jhu-software-concepts-vtalbot1.readthedocs.io

## Deliverables

| Deliverable | Location |
|---|---|
| Application source | `module_4/src/` |
| Test suite (135 tests, all marked) | `module_4/tests/` |
| Coverage report (100%) | `module_4/coverage_summary.txt` |
| CI workflow | `.github/workflows/tests.yml` (repository root) |
| Green CI run screenshot | `module_4/actions_success.png` |
| Sphinx source | `module_4/docs/source/` |
| Built HTML documentation | `module_4/docs/build/html/` (open `index.html`) |
| Read the Docs configuration | `.readthedocs.yaml` (repository root) |
| Dependencies (app, tests, coverage, docs) | `module_4/requirements.txt` |

## Set up the project

Requires Python 3.12 and PostgreSQL (developed against 15, tested in CI
against 16).

```
git clone https://github.com/Vix-T/jhu_software_concepts.git
cd jhu_software_concepts
python -m pip install -r module_4/requirements.txt
```

`requirements.txt` covers the app, the tests and coverage, and the Sphinx
documentation build.

## Fresh Install

Prerequisites: Python 3.12, PostgreSQL, and Graphviz (its `dot` command
draws the module dependency graph).

The supported install is editable (`pip install -e .`): the code locates its
templates, static files, `.env` and `data/` relative to `src/`, so the modules
must be imported from `src/` itself rather than copied into site-packages.

`requirements.in` lists the top-level dependencies; `requirements.txt` is the
fully pinned lock generated from it (every transitive dependency included,
since `uv pip sync` installs exactly what the lock lists and nothing else).

Get the code, create the databases, and create `.env` from the template:

```
git clone https://github.com/Vix-T/jhu_software_concepts.git
cd jhu_software_concepts/module_5
createdb jhu_module5
createdb jhu_module5_test
cp .env.example .env
```

Edit `.env`: set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
for `jhu_module5`, and `TEST_DATABASE_URL` for `jhu_module5_test`.

### Option A: pip and venv

```
python3.12 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

### Option B: uv

If a conda environment is active, activate `.venv` (as below) before any `uv pip` command: uv targets `$VIRTUAL_ENV` first, then `$CONDA_PREFIX`, and only then `.venv`, so it would otherwise install into (and `uv pip sync` would prune) the conda environment.

```
uv venv --python 3.12
source .venv/bin/activate
uv pip sync requirements.txt
uv pip install -e .
```

### Then, with the environment active (from `module_5/`)

Load the bundled dataset into `jhu_module5`:

```
python src/load_data.py
```

Run the app (http://127.0.0.1:5000/; set `FLASK_DEBUG=1` in `.env` for the debugger):

```
python src/flask_app.py
```

Run the tests (from the repository root):

```
cd ..
python -m pytest -c module_5/pytest.ini -m "web or buttons or analysis or db or integration" module_5/tests
cd module_5
```

Run Pylint:

```
python -m pylint src
```

### Database roles (least privilege)

Two roles: the owner (your PostgreSQL user, which owns `jhu_module5` and the
`applicants` table, and runs `load_data.py`) and the app role `gradcafe_app`,
which the Flask app and Pull Data connect as day to day. The app role can only
connect, read (`SELECT`) and add (`INSERT`) applicant rows: no `UPDATE`,
`DELETE`, `TRUNCATE`, `CREATE` (tables or temp tables), and no superuser,
create-database or create-role attributes.

1. Load the data as the owner (`python src/load_data.py`, above).
2. Set `APP_DB_USER` and `APP_DB_PASSWORD` in `.env` (see `.env.example`).
3. Create or update the role as the owner. The inline `DB_USER`/`DB_PASSWORD`
   override `.env`; replace `your_owner_role` with the owner's name. It prints
   the statements it ran, with the password masked, and is safe to re-run:

   ```
   DB_USER=your_owner_role DB_PASSWORD= python src/setup_roles.py
   ```

4. In `.env`, set `DB_USER` and `DB_PASSWORD` to the `APP_DB_USER` and
   `APP_DB_PASSWORD` values, then start the app.

Once `.env` points at the app role, the owner-only scripts need the owner
override inline every time: `load_data.py` (the app role can't create the
table) and `setup_roles.py` (it can't create roles or grant privileges):

```
DB_USER=your_owner_role DB_PASSWORD= python src/load_data.py
DB_USER=your_owner_role DB_PASSWORD= python src/setup_roles.py
```

The app itself (`python src/flask_app.py`) and Pull Data run as the app role
from `.env`, with no override.

If the `applicants` table is missing, the app (connected as `gradcafe_app`)
can't create it and answers 503 "applicants table missing; run load_data.py
as the database owner".

Local authentication note: Postgres.app trusts local connections, so the app
role's password isn't checked locally. Its privileges still are: any statement
outside `SELECT`/`INSERT` fails with "permission denied".

## Configure PostgreSQL

1. Create two databases: one for the app and one for the tests. The test
   database's name must end in `_test`; the test suite refuses to run
   against anything else, which protects your app data.
   ```
   createdb jhu_module4
   createdb jhu_module4_test
   ```
2. Create `module_4/.env` (it is git-ignored):
   ```
   DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/jhu_module4
   TEST_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/jhu_module4_test
   ```
   The password is optional for local trust authentication
   (`postgresql://USER@localhost:5432/jhu_module4`). A variable already set
   in the environment takes precedence over `.env`.
3. Load the bundled dataset (`module_4/data/llm_extend_applicant_data_full.json.gz`,
   the cleaned Module 2 data, gzipped from 50.5 MB to 4.0 MB). This creates
   the `applicants` table if needed:
   ```
   cd module_4
   python src/load_data.py
   ```
   To load a different file, pass its path (`.json` or `.json.gz`):
   `python src/load_data.py path/to/data.json`. If PostgreSQL isn't
   reachable, the loader exits with status 1 and says what to check.

### Expected results after a fresh load

`load_data.py` should report `Inserted: 60024`, `Skipped duplicates: 0`,
`Failed to parse: 1`. The bundled file holds 60,025 records: 60,024 have a
unique URL and are inserted, and one has no URL (0-based record index 13125) and is
skipped, because the URL is the table's natural key. There are no duplicate
URLs in the file. The page then shows Fall 2026 applicant count (Q1)
= **32,344**, Q7 = 20, Q8 = 30, Q9 = 26.

The development database used for Module 3 shows 60,030 rows and Q1 =
32,345. Those 6 extra rows are not in the bundled file: they were added to
that database separately (for example by later Pull Data runs). Their per-question footprint matches
exactly (1 Fall 2026 row, 6 rows with a nationality classification, 4 PhD
rows). So a fresh load reproduces the dataset, not those later additions.

## Run the Flask app

From `module_4/`:

```
python src/flask_app.py
```

Then open http://127.0.0.1:5000/ (the page is also served at `/analysis`).
On an empty database the page shows "N/A" answers instead of failing.

### Pull Data and Update Analysis

**Precondition.** The "Pull Data" button scrapes new entries from Grad Cafe.
It requires a Chrome browser already running with remote debugging enabled
(`--remote-debugging-port=9222`), with Grad Cafe's Cloudflare challenge
already manually cleared in that session. Pull Data attaches to that
existing, already-verified session — it does not launch a browser or solve
the challenge itself. Without it, the pull fails fast and the failure is
shown on the page (see "Last pull" below).

**Newest entries first.** Every pull starts at the first (newest) results
page and keeps only entries whose URL is not already in the database. It
stops at the first page whose entries are all already loaded, once 300 new
entries have been collected, or at the end of pagination. Pulls never
resume from where an earlier pull left off, so repeated pulls stay cheap
and always pick up what's new.

**Crash handling.** If the browser session crashes mid-pull, the scraper
re-attaches and retries the page that failed, keeping what it already
collected. After 3 consecutive failed retries it gives up, and the pull is
recorded as failed instead of retrying forever.

**One pull at a time.** While a pull is running, Pull Data and Update
Analysis both answer HTTP 409 (`{"busy": true}`), and the page shows a
"pull in progress" banner.

**Last pull.** Every pull, successful or not, records its outcome in
`src/_pull_data_result.json` (not committed). The page shows it under the
buttons: "Last pull succeeded: N new rows (finished …)", "Last pull failed:
<reason> (finished …)", or "No pull has run yet." The same information is
available as JSON:

```
GET /pull-status
{"last_result": {"ok": true, "inserted": 2, "skipped": 0, "failed": 0,
                 "error": null, "finished_at": "2026-09-27T17:33:44+00:00"},
 "running": false}
```

`last_result` is `null` before the first pull.

**Update Analysis.** The page shows the analysis as of the last Update
Analysis (the time is shown under the buttons). After a pull finishes,
click Update Analysis to recompute the numbers with the new rows.

## Run the tests

From the **repository root**:

```
pytest -c module_4/pytest.ini module_4/tests
```

* The run enforces 100% statement coverage of `module_4/src`
  (`--cov-fail-under=100` in `pytest.ini`) and prints a per-file report;
  `module_4/coverage_summary.txt` is a saved copy of that output.
* Tests use `TEST_DATABASE_URL` only, never the app database.
* Every test carries at least one marker: `web`, `buttons`, `analysis`, `db`,
  `integration`. Run one group with, for example,
  `pytest -c module_4/pytest.ini -m buttons module_4/tests` (a partial run
  reports a coverage failure; only the full suite reaches 100%). The
  command CI runs selects every marker, which is the entire suite:
  ```
  pytest -c module_4/pytest.ini -m "web or buttons or analysis or db or integration" module_4/tests
  ```
* CI: `.github/workflows/tests.yml` runs that command on every push and pull
  request to `main`, against a PostgreSQL 16 service container.

## Documentation

Published on Read the Docs: **https://jhu-software-concepts-vtalbot1.readthedocs.io**

It covers setup, architecture, the API reference (with the HTTP routes),
the testing guide, operational notes, troubleshooting and known issues.

The built HTML is also committed at `module_4/docs/build/html/`; open
`module_4/docs/build/html/index.html` in a browser. To rebuild it, from the
repository root (warnings are treated as errors):

```
sphinx-build -W --keep-going -b html module_4/docs/source module_4/docs/build/html
```

## Dependency graph

`module_5/dependency.svg` is the import graph of the Flask app, generated
with pydeps (from `requirements.txt`) and Graphviz (`dot` must be on
`PATH`; on Ubuntu, `sudo apt-get install graphviz`). From `module_5/`:

```
pydeps src/flask_app.py --noshow -T svg -o dependency.svg --max-module-depth 1 --rankdir TB --only applicant_search busy_state config flask_app load_data models orm_queries pull_data query_data scrape sql_utils flask psycopg2 sqlalchemy dotenv selenium bs4
```

* `--max-module-depth 1` collapses each package to one node
  (`sqlalchemy.orm`, `sqlalchemy.sql`, ... become `sqlalchemy`).
* `--rankdir TB` draws the layers top to bottom: third-party packages,
  then the database and config helpers, then the services, then
  `flask_app.py`.
* `--only` keeps the `src` modules and the six key third-party packages,
  dropping the transitive ones (werkzeug, jinja2, greenlet, urllib3, ...).
  A new `src` module must be added to this list to appear in the graph.

`setup_roles.py` is not in the graph: it is a standalone command-line
script that the app never imports.

---

# Module 3 — Raw SQL vs. SQLAlchemy Comparison (Part 7)

This section compares the raw-SQL and SQLAlchemy (ORM) implementations of **Question 9**: "Repeat Question 8, but identify the university and program using `llm_generated_program`/`llm_generated_university` instead of the original downloaded fields." Q9 was chosen because its regex-based, word-boundary university/program matching required dropping into PostgreSQL's `~*` operator on both sides, making it a concrete case where the two approaches diverge.

## Raw SQL (`query_data.py`)

```python
def q9(cur):
    """Same as Q8, but university/program matched via the LLM-generated fields."""
    university_clause = " OR ".join(
        "llm_generated_university ~* %s" for _ in Q8_Q9_UNIVERSITIES
    )
    params = [pattern for _, pattern in Q8_Q9_UNIVERSITIES]
    cur.execute(
        f"""
        SELECT COUNT(*) FROM applicants
        WHERE term = 'Fall 2026'
          AND status = 'Accepted'
          AND degree = 'PhD'
          AND llm_generated_program ~* %s
          AND ({university_clause})
        """,
        [CS_PATTERN] + params,
    )
    return cur.fetchone()[0]
```

## SQLAlchemy (`orm_queries.py`)

```python
def orm_q9(session):
    """Same as Q8, but university/program matched via the LLM-generated fields."""
    university_clause = or_(
        *[
            Applicant.llm_generated_university.op("~*")(pattern)
            for _, pattern in Q8_Q9_UNIVERSITIES
        ]
    )
    stmt = select(func.count()).where(
        and_(
            Applicant.term == "Fall 2026",
            Applicant.status == "Accepted",
            Applicant.degree == "PhD",
            Applicant.llm_generated_program.op("~*")(CS_PATTERN),
            university_clause,
        )
    )
    return session.scalar(stmt)
```

## Comparison

The SQLAlchemy version's real advantage shows up in the non-regex filters. `Applicant.term == "Fall 2026"`, `Applicant.status == "Accepted"`, and `Applicant.degree == "PhD"` are typed attributes on the `Applicant` model, so a typo'd column name or a type mismatch would be caught before the query ever reaches the database, and the same model would carry over largely unchanged if this project ever moved off PostgreSQL to another SQL backend. The raw-SQL version, by contrast, builds its `WHERE` clause as an f-string with manual `%s` placeholders and a hand-assembled parameter list as the number of dynamic clauses grows. That said, the regex-based university/program matching required falling back to `.op("~*")` on the ORM side anyway. Viewed through that lens the ORM version reads as SQL-with-extra-steps, and arguably the raw-SQL version is more direct and easier to audit at a glance, since the whole filter is visible as one literal query string rather than assembled through `and_()`/`or_()`/`.op()` calls. Overall, for this specific question, the ORM's main benefit was reusing `Q8_Q9_UNIVERSITIES` and `CS_PATTERN` from `query_data.py` unchanged and getting typed-column safety on the simple equality filters, while the regex matching itself was a wash.
