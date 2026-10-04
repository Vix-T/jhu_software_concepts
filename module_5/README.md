## Name

Vix Talbot (JHED: vtalbot1)

## Overview

Module 5 hardens the Grad Café analytics app from Modules 3 and 4. The app
loads cleaned Grad Café applicant data (scraped and LLM-standardized in
Module 2) into PostgreSQL, answers a fixed set of questions with both raw SQL
(`query_data.py`) and the SQLAlchemy ORM (`orm_queries.py`), and shows them on
a Flask page (`flask_app.py`) with "Pull Data" (fetch the newest entries) and
"Update Analysis" (recompute the answers) buttons, plus a JSON search
endpoint, `GET /api/applicants`.

Module 5 adds:

* **Safe SQL:** every raw-SQL statement is composed with `psycopg2.sql`
  (identifiers as `sql.Identifier`, values only as bound parameters), and
  every `SELECT` has a clamped `LIMIT`.
* **Configuration from environment variables** (`DB_*`, read from the
  environment or `.env`), with no credentials in the code.
* **Least privilege:** the app connects as `gradcafe_app`, which can only
  `SELECT` and `INSERT` applicant rows.
* **Code quality:** Pylint 10.00/10 with specific exception handling only.
* **Packaging:** pinned `requirements.txt` and an editable `setup.py` install.
* **Supply-chain checks:** a pydeps dependency graph, Snyk scans, and
  GitHub Actions CI with four jobs.

The report is `module_5_report.pdf` (source: `report/module_5_report.html`).

## Deliverables

| Deliverable | Location (in `module_5/` unless noted) |
|---|---|
| Report (PDF) and its HTML source | `module_5_report.pdf`, `report/module_5_report.html` |
| Application source | `src/` |
| Test suite (307 tests, all marked) | `tests/`, `pytest.ini` |
| Coverage report (100%) | `coverage_summary.txt` |
| Pylint report (10.00/10) | `pylint_report.txt` |
| Packaging | `setup.py` |
| Dependencies | `requirements.in` (top level), `requirements.txt` (pinned lock) |
| Environment template | `.env.example` |
| Dependency graph | `dependency.svg` |
| Snyk dependency scan | `snyk_test_output.txt`, `snyk-analysis.png` |
| Snyk Code scan (extra credit) | `snyk_code_output.txt`, `snyk-code-analysis.png`, `SNYK_FINDINGS.md` |
| Database privileges evidence | `db-privileges.png` |
| CI workflow | `.github/workflows/ci.yml` (repository root; mirrored at `module_5/.github/workflows/ci.yml`) |
| Green CI run screenshot | `actions_success.png` |
| Sphinx source | `docs/source/` |

## Fresh Install

Prerequisites: Python 3.12, PostgreSQL (developed against 15, tested in CI
against 16), and Graphviz (its `dot` command draws the dependency graph).

The supported install is editable (`pip install -e .`): the code locates its
templates, static files, `.env` and `data/` relative to `src/`, so the modules
must be imported from `src/` itself rather than copied into site-packages.

`requirements.in` lists the top-level dependencies; `requirements.txt` is the
fully pinned lock generated from it (every transitive dependency included,
since `uv pip sync` installs exactly what the lock lists and nothing else).
It covers the app, the tests and coverage, Pylint, pydeps and the Sphinx
build.

Get the code, create the databases, and create `.env` from the template:

```
git clone https://github.com/Vix-T/jhu_software_concepts.git
cd jhu_software_concepts/module_5
createdb jhu_module5
createdb jhu_module5_test
cp .env.example .env
```

Edit `.env` (see [Environment variables](#environment-variables)).

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

## Environment variables

Settings are read from the environment first, then from `module_5/.env`
(git-ignored; `.env.example` is the template). A variable already set in
the environment always wins over `.env`.

| Variable | Required | Meaning |
|---|---|---|
| `DB_HOST` | yes | PostgreSQL server host |
| `DB_PORT` | no | PostgreSQL port (default 5432) |
| `DB_NAME` | yes | The development database, `jhu_module5` (the tests never use it) |
| `DB_USER` | yes | The user the app connects as: the owner at first, then `gradcafe_app` (see [Database roles](#database-roles-least-privilege)) |
| `DB_PASSWORD` | no | Its password; leave empty for local trust authentication |
| `TEST_DATABASE_URL` | for tests | The test database URL, e.g. `postgresql://your_username@localhost:5432/jhu_module5_test`; its name must end in `_test` |
| `APP_DB_USER` | for `setup_roles.py` | The least-privilege role to create (`gradcafe_app`) |
| `APP_DB_PASSWORD` | for `setup_roles.py` | Its password |
| `FLASK_DEBUG` | no | `1` turns on the Flask debugger and reloader (never in production; off by default) |

A missing required variable stops the app and scripts with a message
naming every missing one.

## Configure PostgreSQL and load the data

With `.env` pointing at `jhu_module5` as the database owner (your own
PostgreSQL user), load the bundled dataset
(`data/llm_extend_applicant_data_full.json.gz`, the cleaned Module 2 data).
This creates the `applicants` table if needed:

```
python src/load_data.py
```

To load a different file, pass its path (`.json` or `.json.gz`):
`python src/load_data.py path/to/data.json`. If PostgreSQL isn't reachable,
the loader exits with status 1 and says what to check.

**Expected results after a fresh load:** `Inserted: 60024`,
`Skipped duplicates: 0`, `Failed to parse: 1`. The file holds 60,025
records; one has no URL (0-based record index 13125) and is skipped, because
the URL is the table's natural key. The page then shows Fall 2026 applicant
count (Q1) = **32,344**, Q7 = 20, Q8 = 30, Q9 = 26.

## Database roles (least privilege)

Two roles: the owner (your PostgreSQL user, which owns `jhu_module5` and the
`applicants` table, and runs `load_data.py`) and the app role `gradcafe_app`,
which the Flask app and Pull Data connect as day to day. The app role can only
connect, read (`SELECT`) and add (`INSERT`) applicant rows: no `UPDATE`,
`DELETE`, `TRUNCATE`, `CREATE` (tables or temp tables), and no superuser,
create-database or create-role attributes.

1. Load the data as the owner (`python src/load_data.py`, above).
2. Set `APP_DB_USER` and `APP_DB_PASSWORD` in `.env`.
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

## Run the Flask app

From `module_5/`:

```
python src/flask_app.py
```

Then open http://127.0.0.1:5000/ (the page is also served at `/analysis`).
On an empty database the page shows "N/A" answers instead of failing. Set
`FLASK_DEBUG=1` for the debugger; it is off by default.

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
entries have been collected, or at the end of pagination.

**Crash handling.** If the browser session crashes mid-pull, the scraper
re-attaches and retries the page that failed, keeping what it already
collected. After 3 consecutive failed retries it gives up, and the pull is
recorded as failed instead of retrying forever.

**One pull at a time.** While a pull is running, Pull Data and Update
Analysis both answer HTTP 409 (`{"busy": true}`), and the page shows a
"pull in progress" banner.

**Last pull.** Every pull, successful or not, records its outcome in
`src/_pull_data_result.json` (not committed). The page shows it under the
buttons, and the same information is available as JSON:

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

### `GET /api/applicants`

Returns applicant rows as JSON (a fixed column list; the free-text
`comments` column is never returned), on a read-only connection.

| Parameter | Accepted values | Default |
|---|---|---|
| `limit` | An integer; clamped to 1–100 (`0` → 1, `500` → 100) | 10 |
| `sort` | `p_id`, `date_added`, `gpa`, `gre`, `university`, `program` | `p_id` |
| `order` | `asc`, `desc` | `asc` |
| `university` | Substring match on the standardized university name, at most 100 characters; `%`, `_` and `\` match literally | none |

```
GET /api/applicants?limit=5&sort=gpa&order=desc&university=Johns%20Hopkins
{"count": 5, "limit": 5, "sort": "gpa", "order": "desc", "rows": [...]}
```

A non-integer `limit`, an unknown `sort` or `order`, or a `university`
filter over 100 characters returns **400** with `{"error": "..."}`; no SQL
is built. A database that is unreachable, a missing table, or a permission
error returns **503**.

## Run the tests

From the **repository root**:

```
pytest -c module_5/pytest.ini module_5/tests
```

* The run enforces 100% statement coverage of `module_5/src`
  (`--cov-fail-under=100` in `pytest.ini`) and prints a per-file report;
  `coverage_summary.txt` is a saved copy of that report.
* Tests use `TEST_DATABASE_URL` only, never the app database, and never read
  the developer's `.env` beyond that one value.
* Every test carries at least one marker: `web`, `buttons`, `analysis`, `db`,
  `integration`. Run one group with, for example,
  `pytest -c module_5/pytest.ini -m buttons module_5/tests` (a partial run
  reports a coverage failure; only the full suite reaches 100%). The
  command CI runs selects every marker, which is the entire suite:
  ```
  python -m pytest -c module_5/pytest.ini -m "web or buttons or analysis or db or integration" module_5/tests
  ```
* CI runs that command on every push and pull request to `main`; see
  [Continuous integration](#continuous-integration).

## Pylint

From `module_5/` (the saved output is `pylint_report.txt`, 10.00/10):

```
python -m pylint src
```

No Pylint checks are disabled. Exceptions are caught by specific type only
(no bare `except:` or `except Exception`), and every handler logs the error
and returns a specific response or exit code.

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

## Security scan

The dependencies are scanned with the Snyk CLI (`snyk auth` first). From
`module_5/`, with the virtual environment synced from `requirements.txt`:

```
snyk test --file=requirements.txt --package-manager=pip --command=venv/bin/python
```

Prerequisite: Snyk ignores the `os_name == 'nt'` markers on two
Windows-only entries in the lock and stops with "Missing required packages"
unless they are installed, so install them into the venv first:
`uv pip install cffi==2.1.1 pycparser==3.0 --python venv/bin/python`.
The saved output is `module_5/snyk_test_output.txt` (64 dependencies, 0
issues).

Snyk Code (static analysis) runs with `snyk code test src` and
`snyk code test tests`; the saved output is `snyk_code_output.txt`. Its 11
findings are reviewed in `SNYK_FINDINGS.md`: all are false positives, left
open rather than ignored.

## Continuous integration

`.github/workflows/ci.yml` (repository root) runs four jobs on every push
and pull request to `main` (and on demand from the Actions tab), each on
Ubuntu 24.04 with Python 3.12 and `module_5/requirements.txt`:

| Job | What it checks |
|---|---|
| `pylint` | `python -m pylint src --fail-under=10` from `module_5/`; it also fails if the two workflow copies differ |
| `dependency-graph` | Installs Graphviz, runs the pydeps command above, fails unless `dependency.svg` exists and has nodes, and uploads it as an artifact |
| `snyk` | `snyk test` (fails on high or critical issues), then `snyk code test src` as a report only (its findings are the documented false positives in `SNYK_FINDINGS.md`) |
| `pytest` | The full suite against a PostgreSQL 16 service container, with 100% coverage enforced |

The `snyk` job needs a repository secret named `SNYK_TOKEN` (Settings >
Secrets and variables > Actions) holding a Snyk API token; without it the
job stops with an error naming the missing secret.

GitHub only runs workflows from the repository root, so the root copy is
the one that executes. `module_5/.github/workflows/ci.yml` is an identical
copy kept for the expected module layout; the `pylint` job compares the two
and fails if they differ. The Module 4 workflow, `tests.yml`, is unchanged.

## Documentation

The published Read the Docs site
(https://jhu-software-concepts-vtalbot1.readthedocs.io) still builds the
**Module 4** documentation (`.readthedocs.yaml` points at `module_4/docs`).

`module_5/docs/source/` is the Module 4 documentation carried forward: its
API pages autodoc the `module_5/src` code, but its text has not been
updated for Module 5 (there are no pages yet for `applicant_search`,
`sql_utils`, `models`, `setup_roles` or `/api/applicants`; this README and
the report cover them). To build it locally, from the repository root
(warnings are treated as errors):

```
sphinx-build -W --keep-going -b html module_5/docs/source module_5/docs/build/html
```
