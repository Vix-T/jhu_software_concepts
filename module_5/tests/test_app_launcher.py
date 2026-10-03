"""create_app's production defaults: file-lock busy state, DB loader, subprocess launcher."""

import json
import subprocess
import sys

import pytest
from conftest import FakeScraper, Spy

import flask_app as app_module
from busy_state import FileLockBusyState, InMemoryBusyState

pytestmark = pytest.mark.buttons

# Captured before any test replaces subprocess.Popen, so helper children are real processes.
REAL_POPEN = subprocess.Popen


@pytest.fixture
def lock_path(tmp_path):
    return str(tmp_path / "pull.lock")


@pytest.fixture
def config(test_database_url, lock_path):
    return {"DATABASE_URL": test_database_url, "TESTING": True, "BUSY_LOCK_PATH": lock_path}


@pytest.fixture
def blocked_child():
    proc = REAL_POPEN([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    yield proc
    proc.stdin.close()
    proc.wait()


class FakePopen:
    """Replaces subprocess.Popen: records the launch instead of starting pull_data.py."""

    launches = []
    pid = None
    error = None

    def __init__(self, args, **kwargs):
        if FakePopen.error is not None:
            raise FakePopen.error
        FakePopen.launches.append((args, kwargs))


@pytest.fixture
def fake_popen(monkeypatch):
    FakePopen.launches = []
    FakePopen.pid = None
    FakePopen.error = None
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    return FakePopen


def test_in_process_pull_with_default_lock_and_loader(config, lock_path, fake_records, row_count):
    scraper = FakeScraper(fake_records)
    client = app_module.create_app(config, scraper=scraper).test_client()

    response = client.post("/pull-data")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "inserted": 3}
    assert row_count() == 3
    assert FileLockBusyState(lock_path).is_busy() is False

    assert FileLockBusyState(lock_path).try_acquire() is True
    busy = client.post("/pull-data")
    assert busy.status_code == 409
    assert busy.get_json() == {"busy": True}
    assert scraper.calls == 1


def test_default_launcher_starts_pull_subprocess(
    config, lock_path, test_database_url, fake_popen, blocked_child, pull_result_path
):
    fake_popen.pid = blocked_child.pid
    client = app_module.create_app(config).test_client()

    response = client.post("/pull-data")
    assert response.status_code == 202
    assert response.get_json() == {"ok": True}

    [(args, kwargs)] = fake_popen.launches
    assert args == [sys.executable, app_module.PULL_DATA_SCRIPT]
    assert kwargs["cwd"] == app_module.SRC_DIR
    assert kwargs["env"]["DATABASE_URL"] == test_database_url
    assert kwargs["env"]["PULL_RESULT_FILE"] == str(pull_result_path)
    # Ownership moved from the app process to the launched child.
    with open(lock_path, encoding="utf-8") as f:
        assert json.load(f)["pid"] == blocked_child.pid

    again = client.post("/pull-data")
    assert again.status_code == 409
    assert again.get_json() == {"busy": True}
    assert len(fake_popen.launches) == 1


def test_launch_failure_returns_500_and_releases_lock(config, lock_path, fake_popen):
    fake_popen.error = OSError("exec format error")
    client = app_module.create_app(config).test_client()

    response = client.post("/pull-data")
    assert response.status_code == 500
    assert response.get_json() == {"ok": False, "error": "exec format error"}
    assert FileLockBusyState(lock_path).is_busy() is False


class RacingBusyState(InMemoryBusyState):
    """Simulates a competing request winning the lock between is_busy() and try_acquire()."""

    def try_acquire(self, pid=None):
        super().try_acquire(pid="competing-request")
        return super().try_acquire(pid)


def test_lost_acquire_race_returns_409_without_launching(test_database_url):
    launcher = Spy()
    client = app_module.create_app(
        {"DATABASE_URL": test_database_url, "TESTING": True},
        busy_state=RacingBusyState(),
        pull_launcher=launcher,
    ).test_client()

    response = client.post("/pull-data")
    assert response.status_code == 409
    assert response.get_json() == {"busy": True}
    assert launcher.calls == []
