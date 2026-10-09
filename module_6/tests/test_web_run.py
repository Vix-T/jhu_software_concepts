"""run.py: builds the default app and runs Flask's server on 0.0.0.0:8080."""

import os
import runpy

import pytest
from flask import Flask

from run import debug_enabled

pytestmark = pytest.mark.web

RUN_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "web", "run.py")


def test_main_binds_all_interfaces_on_8080_with_debug_off(monkeypatch, refresh_summary):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    refresh_summary()
    runs = []
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: runs.append((self, kwargs)))

    runpy.run_path(RUN_PY, run_name="__main__")

    [(app, kwargs)] = runs
    assert kwargs == {"host": "0.0.0.0", "port": 8080, "debug": False, "threaded": True}
    # DATABASE_URL points at the test database for the whole run (conftest).
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert b"Answer:" in response.data


def test_flask_debug_env_turns_debugger_on(monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    runs = []
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: runs.append(kwargs))

    runpy.run_path(RUN_PY, run_name="__main__")

    assert runs == [{"host": "0.0.0.0", "port": 8080, "debug": True, "threaded": True}]


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


def test_main_exits_cleanly_without_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: pytest.fail("server must not start"))

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(RUN_PY, run_name="__main__")

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert out == "Cannot start the web service: DATABASE_URL is not set\n"
