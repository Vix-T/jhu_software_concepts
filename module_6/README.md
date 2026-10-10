## Name

Vix Talbot (JHED: vtalbot1)

# Module 6 — Grad Café Analytics as a Docker Compose stack

The Grad Café analytics app from Modules 3–5, split into containerised
services. A Flask **web** service shows the analysis and queues work; a
**worker** consumes that work from **RabbitMQ** and does all writing to
**PostgreSQL**: seeding the 60,025-record dataset, scraping new Grad Café
entries, and recomputing the analysis the page shows.

Images: [hub.docker.com/r/vixbot/module_6](https://hub.docker.com/r/vixbot/module_6)
(`web-v1`, `worker-v1`). Repository:
[github.com/Vix-T/jhu_software_concepts](https://github.com/Vix-T/jhu_software_concepts).
The report is `module_6_report.pdf` (source: `report/module_6_report.html`).

## Architecture

| Service | Image | Role |
|---|---|---|
| `db` | `postgres:16` | Stores `applicants`, `ingestion_watermarks` and `analysis_summary` in the named volume `pgdata`. Not published to the host. |
| `rabbitmq` | `rabbitmq:3.13-management` | The task queue: durable direct exchange `tasks`, durable queue `tasks_q`, routing key `tasks`. Management UI on port 15672. |
| `web` | `vixbot/module_6:web-v1` (built from `src/web`) | Flask app on port 8080. Reads the stored analysis; the buttons only publish tasks. Never scrapes or writes applicant data, and connects as a read-only database role. |
| `worker` | `vixbot/module_6:worker-v1` (built from `src/worker`) | Initialises the database at startup, then consumes `tasks_q` and runs each task against PostgreSQL. |

Message flow for a button press:

1. The browser POSTs `/pull-data` or `/update-analysis`.
2. The web service publishes a persistent JSON message
   `{"kind": ..., "ts": ..., "payload": {}}` to the `tasks` exchange (publisher
   confirms on, `mandatory=True`) and answers **202** `{"status": "queued", "task": ...}`.
3. RabbitMQ routes it to `tasks_q`. The worker takes one message at a time
   (`prefetch_count=1`).
4. The worker runs the task in **one database transaction**, commits, then acks.
   If it fails, the transaction is rolled back and the message is nacked
   without requeue; the worker carries on with the next message.
5. The page's script polls `GET /api/status` and reloads when the worker has
   changed the data (see [The two buttons](#the-two-buttons)).

## Prerequisites

* Docker with the Compose plugin (`docker compose`). Built and tested on
  Docker Desktop 4.47 (Engine 28.4.0, Compose v2.39.4) on an Intel Mac (amd64).
* Ports 8080 and 15672 free on the host. PostgreSQL's 5432 and RabbitMQ's
  5672 are not published, so a local PostgreSQL on 5432 doesn't conflict.
* For Pull Data only: Google Chrome on the host (see
  [Pull Data precondition](#pull-data-precondition)).

## Quick start

```
git clone https://github.com/Vix-T/jhu_software_concepts.git
cd jhu_software_concepts/module_6
docker compose up --build
```

Then open http://localhost:8080. RabbitMQ's management UI is at
http://localhost:15672 (user `guest`, password `guest`: development only).

What to expect on a first start (measured from a clean volume): `docker compose up`
brings `db` and `rabbitmq` up healthy, then starts `web` and `worker` (about
20 seconds in total, after the images are built). The worker then creates the
tables and loads the seed data in one transaction, which takes about 50
seconds. Until it finishes, the page answers **503** with "The database is
being initialised. The first start loads about 60,000 rows and takes around a
minute. This page refreshes automatically.", and reloads itself every 5
seconds. The analysis then appears (Fall 2026 applicant count 32,344 on the
seed data). Later starts on the same volume skip the seed and are ready in
seconds.

`docker compose down` stops the stack and keeps the data;
`docker compose down -v` also deletes the `pgdata` volume, so the next start
seeds again.

## Configuration

Docker Compose reads these variables from the shell or from a `.env` file
next to `docker-compose.yml`, and every one has a default:

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` | `gradcafe` | Database owner; the worker connects as this user. |
| `POSTGRES_PASSWORD` | `gradcafe_dev_only` | Owner password. |
| `POSTGRES_DB` | `gradcafe` | Database name. |
| `WEB_DB_USER` | `gradcafe_web` | Read-only role the worker creates and the web connects as. |
| `WEB_DB_PASSWORD` | `gradcafe_web_dev_only` | Its password. |
| `RABBITMQ_USER` | `guest` | RabbitMQ user (web and worker connect as it). |
| `RABBITMQ_PASSWORD` | `guest` | Its password. |
| `CHROME_DEBUGGER_ADDRESS` | `host.docker.internal:9222` | Where the worker attaches to Chrome (`host:port`). A hostname other than `localhost` is resolved to an IP first. |
| `CHROMEDRIVER_VERSION` | `154.0.8037.92` | Build argument: the chromedriver baked into the worker image. |
| `DOCKERHUB_USER` | `vixbot` | Namespace of the image tags. |

**The credentials in `docker-compose.yml` are development-only defaults.** The
database is reachable only inside the compose network (its port is not
published), and RabbitMQ's `guest` account is the image's default. To use
other values, put them in `module_6/.env` (git-ignored) before the first
`docker compose up`, for example:

```
POSTGRES_PASSWORD=choose-a-password
WEB_DB_PASSWORD=choose-another-password
RABBITMQ_PASSWORD=choose-a-third-password
```

Compose substitutes them everywhere they are used, including the connection
URLs it builds for `web` and `worker`. Per the `postgres` image's
documentation, `POSTGRES_PASSWORD` is only applied when the volume is first
initialised, so changing it later needs `docker compose down -v` (which
deletes the data). `.env.example` lists every
variable.

Inside the containers the services read: `DATABASE_URL` and `RABBITMQ_URL`
(both services; built by Compose from the variables above), `SEED_JSON`
(`/data/applicant_data.json`), `WEB_DB_USER`/`WEB_DB_PASSWORD` and
`CHROME_DEBUGGER_ADDRESS` (worker), and `FLASK_DEBUG` (web; `1` turns on the
debugger, off by default). The worker image also sets
`CHROMEDRIVER_PATH=/usr/local/bin/chromedriver` and `SE_OFFLINE=true`, so
Selenium uses the baked-in driver and never downloads one.

## The two buttons

| Button | Endpoint | Task queued | Worker does |
|---|---|---|---|
| Pull Data | `POST /pull-data` | `scrape_new_data` | Scrapes Grad Café entries newer than the watermark, inserts them, advances the watermark and recomputes the analysis, all in one transaction. |
| Update Analysis | `POST /update-analysis` | `recompute_analytics` | Recomputes every question and replaces the stored analysis. |

Each answers **202** `{"status": "queued", "task": "<kind>"}` as soon as the
message is published, or **503** `{"status": "error", ...}` if RabbitMQ can't
be reached or doesn't confirm the message. After a 202 the page shows a
"Request queued" banner; its script then polls `GET /api/status` every 2
seconds for up to 60 seconds and reloads the page when "Analysis last updated"
or "Data last updated" changes. Verified in a browser: after Update Analysis
the page reloaded by itself and showed the new "Analysis last updated" time.

The page also offers `GET /api/applicants` (applicant rows as JSON, with
validated `limit`, `sort`, `order` and `university` parameters) and
`GET /api/status`.

## Pull Data precondition

Grad Café is behind a Cloudflare check that has to be passed by hand, so the
worker never starts a browser: it attaches to a Chrome you start on the host
with remote debugging. The command used to test it on the Mac:

```
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.chrome-gradcafe-debug" \
  --no-first-run --no-default-browser-check \
  "https://www.thegradcafe.com/survey/"
```

`--user-data-dir` keeps this debugging session in its own profile, separate
from your everyday Chrome. In that window, complete the Cloudflare check,
wait for the results table, and leave the window open. Then check the port
from the Mac:

```
lsof -nP -iTCP:9222 -sTCP:LISTEN
curl -s http://127.0.0.1:9222/json/version
```

The first lists a Google Chrome process; the second prints JSON with
`"Browser": "Chrome/..."`. Press Pull Data once that works. In testing a pull
read 3 pages and inserted the 29 entries newer than the seed data.

The worker image contains chromedriver `154.0.8037.92`, which matches Chrome
154. chromedriver must match your Chrome's major version (see
`chrome://version`); pick an exact version from
[Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) and rebuild:

```
CHROMEDRIVER_VERSION=<version> docker compose build worker
docker compose up -d worker
```

(Compose passes the value to the build as tested with `docker compose config`;
only the default version has been built and run.)

Without a Chrome session the task fails cleanly: the worker logs
`PullPreconditionError: PULL FAILED: could not attach to Chrome for scraping.`,
nacks the message without requeue, and keeps running; `tasks_q` is left empty.

## Docker Hub

The images are public at
[hub.docker.com/r/vixbot/module_6](https://hub.docker.com/r/vixbot/module_6):

| Tag | Built from | Compressed size |
|---|---|---|
| `web-v1` | `src/web` | 53.7 MiB |
| `worker-v1` | `src/worker` (includes chromedriver) | 80.4 MiB |

To run the published images instead of building, from `module_6/`:

```
docker compose pull web worker
docker compose up -d --no-build
```

`docker pull vixbot/module_6:web-v1` and `docker pull vixbot/module_6:worker-v1`
work without a Docker Hub login. The compose file is still needed for the
database, RabbitMQ, the seed data and the volume mounts.

## Database

The worker runs `load_data.initialize_database()` every time it starts, under
a PostgreSQL advisory lock:

* It creates the three tables if missing: `applicants` (one row per Grad
  Café entry, unique on `url`), `ingestion_watermarks` (the highest Grad Café
  result ID loaded so far, the `<id>` in `/result/<id>`), and `analysis_summary`
  (a single row holding the analysis the page shows).
* If `applicants` is empty, it loads `src/data/applicant_data.json` (mounted
  read-only at `/data`): 60,024 rows (one record has no URL and is skipped) and
  sets the watermark to 1,020,478. On later starts it logs
  "applicants already has rows: seed skipped".
* If there is no stored analysis yet, it computes one.
* It creates or updates the read-only role `gradcafe_web`: CONNECT, schema
  USAGE and **SELECT only** on the three tables. PostgreSQL refuses INSERT,
  UPDATE, DELETE and CREATE TABLE for it.

Scraping is incremental and idempotent. An entry counts as new when its result
ID is above the watermark; rows are inserted with `ON CONFLICT (url) DO
NOTHING`, and the watermark only moves forward (`GREATEST`). A repeated pull
with nothing new inserts nothing; it still refreshes "Data last updated" and
recomputes the analysis.

## Project layout

```
module_6/
  docker-compose.yml        db, rabbitmq, web, worker; volume pgdata
  .env.example              every Compose variable, with its default
  setup.py                  packaging metadata (packages app and etl)
  requirements.in / .txt    development lock: both services + test and lint tools
  pytest.ini
  src/
    web/                    Docker build context of the web image
      Dockerfile, requirements.in/.txt, run.py, publisher.py
      app/                  Flask app: routes, db reads, applicant search, templates, static
    worker/                 Docker build context of the worker image
      Dockerfile, requirements.in/.txt, consumer.py
      etl/                  incremental_scraper.py, query_data.py
    db/                     load_data.py, sql_utils.py, setup_roles.py
                            (mounted read-only into the worker at /app/db)
    data/applicant_data.json   seed data (60,025 records)
  tests/                    pytest suite (fixtures/ holds synthetic Grad Café pages)
  docs/                     earlier modules' Sphinx sources (not updated for Module 6)
  report/                   report source and screenshots
```

Each service's Docker build context is its own folder, so `web` and `worker`
don't import each other. The worker gets the `db` modules from the mount
(`PYTHONPATH=/app/db`).

## Tests and Pylint

The tests run on the host against a local PostgreSQL test database (they don't
need Docker, RabbitMQ or Chrome: pika's connection and the browser are faked).
Create a database whose name ends in `_test`, and give its URL in
`module_6/.env` or the environment:

```
createdb jhu_module6_test
TEST_DATABASE_URL=postgresql://USER@localhost:5432/jhu_module6_test
```

Install with Python 3.11 or later, from the repository root:

```
python -m pip install -r module_6/requirements.txt
python -m pytest -c module_6/pytest.ini -m "web or buttons or analysis or db or integration" module_6/tests
```

The suite (375 tests) enforces 100% statement coverage of `module_6/src`.
Pylint, from `module_6/` (10.00/10):

```
python -m pylint --py-version=3.11 --source-roots=src/web,src/worker,src/db src/web src/worker src/db
```

## Continuous integration

`.github/workflows/module6.yml` ("Module 6 CI") runs on every push and pull
request to `main`, on Ubuntu 24.04 with Python 3.11:

| Job | What it does |
|---|---|
| `pylint` | The Pylint command above, with `--fail-under=10`. |
| `pytest` | The full suite against a `postgres:16` service container (`jhu_module6_test`). |
| `compose-config` | `docker compose -f module_6/docker-compose.yml config --quiet` (validates the compose file; builds nothing). |

`.github/workflows/ci.yml` keeps Module 5's dependency graph and Snyk scans,
and `tests.yml` Module 4's tests.

## Known limitations

* **Pull cap.** A pull collects at most 300 new entries, newest first. If more
  than 300 arrived since the last pull, the older ones are skipped, and the
  advanced watermark means a later pull won't go back for them.
* **No LLM fields on scraped rows.** `llm_generated_program` and
  `llm_generated_university` are empty for scraped entries (the LLM cleaning
  from Module 2 isn't part of the pipeline), so questions that use those fields
  only count seed rows.
* **Restart after the host sleeps.** When the Mac sleeps, the RabbitMQ
  connection's 600-second heartbeat lapses; the worker exits and
  `restart: unless-stopped` brings it back within seconds. No message is lost
  (`tasks_q` is durable and a task is only acked after its commit).
* **Wrong web password looks like initialising.** PostgreSQL reports a wrong
  `WEB_DB_PASSWORD` the same way as a role that doesn't exist yet, so the page
  keeps showing "The database is being initialised".
* **Development server.** The web service runs Flask's built-in server, which
  logs that it isn't meant for production.
* **`docs/`** still holds the Sphinx sources from Modules 4–5; they are not
  updated for Module 6 and are not part of this module's deliverables.
