"""Packaging for module_5: the Grad Cafe applicant analytics app.

Install it editable, from module_5/:

    python -m pip install -e .

Editable is the supported install: the code locates its templates/, static/,
.env and data/ relative to src/, which only holds while the modules are
imported from src/ itself (a regular install would copy just the .py files).

install_requires lists the runtime dependencies only; the exact, fully pinned
environment (including test, lint and docs tools) is requirements.txt.
"""

from setuptools import setup

setup(
    name="gradcafe-analytics",
    version="5.0.0",
    description=(
        "Grad Cafe applicant analytics: PostgreSQL loader, analysis queries "
        "and a Flask app (EN.605.256 Module 5)"
    ),
    python_requires=">=3.12",
    package_dir={"": "src"},
    py_modules=[
        "applicant_search",
        "busy_state",
        "config",
        "flask_app",
        "load_data",
        "models",
        "orm_queries",
        "pull_data",
        "query_data",
        "scrape",
        "setup_roles",
        "sql_utils",
    ],
    install_requires=[
        "beautifulsoup4>=4.12",
        "Flask>=3.1",
        "psycopg2-binary>=2.9",
        "python-dotenv>=1.0",
        "selenium>=4.20",
        "SQLAlchemy>=2.0",
    ],
)
