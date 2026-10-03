"""flask_app.py's __main__ block: builds the default app and runs Flask's dev server."""

import os
import runpy

import pytest
from flask import Flask

from flask_app import debug_enabled

pytestmark = pytest.mark.web

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def test_main_runs_default_app_threaded_with_debug_off(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    runs = []

    def fake_run(self, **kwargs):
        runs.append((self, kwargs))

    monkeypatch.setattr(Flask, "run", fake_run)

    runpy.run_path(os.path.join(SRC_DIR, "flask_app.py"), run_name="__main__")

    [(app, kwargs)] = runs
    assert kwargs == {"debug": False, "threaded": True}
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert {"/", "/analysis", "/pull-data", "/update-analysis"} <= rules
    # The DB_* settings point at the test database for the whole run (conftest),
    # so the default app renders against it.
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert b"Answer:" in response.data


def test_flask_debug_env_turns_debugger_on(monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    runs = []
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: runs.append(kwargs))

    runpy.run_path(os.path.join(SRC_DIR, "flask_app.py"), run_name="__main__")

    assert runs == [{"debug": True, "threaded": True}]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("", False), ("0", False), ("false", False), ("no", False),
     ("1", True), ("true", True), ("TRUE", True), ("yes", True), (" 1 ", True)],
)
def test_debug_enabled_parses_flask_debug(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("FLASK_DEBUG", raising=False)
    else:
        monkeypatch.setenv("FLASK_DEBUG", value)
    assert debug_enabled() is expected


def test_main_exits_cleanly_when_settings_missing(monkeypatch, capsys):
    monkeypatch.setenv("DB_HOST", "")
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: pytest.fail("server must not start"))

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(os.path.join(SRC_DIR, "flask_app.py"), run_name="__main__")

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith("Cannot start the app: Missing required database setting(s): DB_HOST.")
    assert "Traceback" not in out
