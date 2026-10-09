"""Packaging metadata for module_6: the Grad Cafe analytics web service and worker.

The code is three separate import roots, one per Docker build context:

    src/web     run.py, publisher.py and the `app` package (Flask)
    src/worker  consumer.py and the `etl` package (scraper, analysis queries)
    src/db      load_data.py, sql_utils.py, setup_roles.py (mounted into the worker)

setuptools maps whole packages to directories, but top-level modules can only
come from one root, so this installs the two packages (`app`, with its
templates and static files, and `etl`) and declares the runtime dependencies.
The top-level modules are found the way each environment provides them:
pytest's pythonpath (pytest.ini), pylint's --source-roots, and each
container's working directory plus PYTHONPATH=/app/db for the worker.

    python -m pip install -e .

The exact, fully pinned environments are requirements.txt (development: both
services plus test and lint tools) and src/web/requirements.txt and
src/worker/requirements.txt (the images).
"""

from setuptools import setup

setup(
    name="gradcafe-analytics",
    version="6.0.0",
    description=(
        "Grad Cafe applicant analytics: a Flask web service that queues tasks on RabbitMQ "
        "and a worker that scrapes, loads and analyses them in PostgreSQL (EN.605.256 Module 6)"
    ),
    python_requires=">=3.11",
    package_dir={"app": "src/web/app", "etl": "src/worker/etl"},
    packages=["app", "etl"],
    package_data={"app": ["templates/*.html", "static/*.css"]},
    install_requires=[
        "beautifulsoup4>=4.12",
        "Flask>=3.1",
        "pika>=1.3",
        "psycopg2-binary>=2.9",
        "selenium>=4.20",
    ],
)
