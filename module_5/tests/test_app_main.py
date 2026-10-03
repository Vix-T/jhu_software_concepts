"""flask_app.py's __main__ block: builds the default app and runs Flask's dev server."""

import os
import runpy

import pytest
from flask import Flask

pytestmark = pytest.mark.web

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def test_main_runs_default_app_in_debug_threaded_mode(monkeypatch):
    runs = []

    def fake_run(self, **kwargs):
        runs.append((self, kwargs))

    monkeypatch.setattr(Flask, "run", fake_run)

    runpy.run_path(os.path.join(SRC_DIR, "flask_app.py"), run_name="__main__")

    [(app, kwargs)] = runs
    assert kwargs == {"debug": True, "threaded": True}
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert {"/", "/analysis", "/pull-data", "/update-analysis"} <= rules
    # The DB_* settings point at the test database for the whole run (conftest),
    # so the default app renders against it.
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert b"Answer:" in response.data
