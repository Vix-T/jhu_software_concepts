"""FileLockBusyState and InMemoryBusyState: acquire, release, set_owner, staleness.

Real child processes give deterministic PIDs: a child blocked on stdin is
alive until the test closes its stdin; a finished child that was already
.wait()-ed is dead and no longer our child.
"""

import json
import os
import subprocess
import sys
import time

import pytest

from busy_state import FileLockBusyState, InMemoryBusyState

pytestmark = pytest.mark.buttons


@pytest.fixture
def lock(tmp_path):
    return FileLockBusyState(str(tmp_path / "pull.lock"))


@pytest.fixture
def blocked_child():
    """A live child process that exits only when its stdin is closed."""
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    yield proc
    if proc.stdin and not proc.stdin.closed:
        proc.stdin.close()
    proc.wait()


@pytest.fixture
def dead_pid():
    """PID of a process that has exited and been reaped by Popen.wait()."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _write_lock(lock, contents):
    with open(lock.lock_path, "w", encoding="utf-8") as f:
        f.write(contents)


def _read_lock(lock):
    with open(lock.lock_path, encoding="utf-8") as f:
        return json.load(f)


# --- FileLockBusyState ------------------------------------------------------------


def test_no_lock_file_is_not_busy(lock):
    assert lock.status() == {"running": False}
    assert lock.is_busy() is False


def test_acquire_records_pid_and_start_time(lock):
    assert lock.try_acquire(1234) is True
    contents = _read_lock(lock)
    assert set(contents) == {"pid", "started_at"}
    assert contents["pid"] == 1234


def test_acquire_defaults_to_own_pid_and_is_busy(lock):
    assert lock.try_acquire() is True
    status = lock.status()
    assert status["running"] is True
    assert status["pid"] == os.getpid()
    assert lock.is_busy() is True


def test_second_acquire_fails_while_holder_alive(lock):
    assert lock.try_acquire() is True
    assert lock.try_acquire(5678) is False
    assert _read_lock(lock)["pid"] == os.getpid()


def test_live_child_holder_is_busy(lock, blocked_child):
    assert lock.try_acquire(blocked_child.pid) is True
    status = lock.status()
    assert status["running"] is True
    assert status["pid"] == blocked_child.pid


def test_exited_child_is_reaped_and_lock_cleared(lock, blocked_child):
    assert lock.try_acquire(blocked_child.pid) is True
    blocked_child.stdin.close()

    deadline = time.monotonic() + 5
    while lock.is_busy():
        if time.monotonic() > deadline:
            pytest.fail("child process still reported busy 5 s after its stdin was closed")

    assert not os.path.exists(lock.lock_path)
    # status() reaped the child itself, so it is no longer waitable.
    with pytest.raises(ChildProcessError):
        os.waitpid(blocked_child.pid, os.WNOHANG)


def test_dead_non_child_pid_is_stale(lock, dead_pid):
    _write_lock(lock, json.dumps({"pid": dead_pid, "started_at": "2026-01-01T00:00:00+00:00"}))
    assert lock.status() == {"running": False}
    assert not os.path.exists(lock.lock_path)


def test_stale_lock_is_replaced_on_acquire(lock, dead_pid):
    _write_lock(lock, json.dumps({"pid": dead_pid, "started_at": "2026-01-01T00:00:00+00:00"}))
    assert lock.try_acquire(4321) is True
    assert _read_lock(lock)["pid"] == 4321


def test_permission_denied_pid_counts_as_alive(lock, monkeypatch):
    # A PID that isn't our child (waitpid raises ChildProcessError) and that we
    # may not signal: os.kill raising PermissionError means it exists.
    signalled = []

    def kill(pid, sig):
        signalled.append((pid, sig))
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "kill", kill)
    _write_lock(lock, json.dumps({"pid": 999_999, "started_at": "2026-01-01T00:00:00+00:00"}))

    assert lock.is_busy() is True
    assert signalled == [(999_999, 0)]
    assert os.path.exists(lock.lock_path)


@pytest.mark.parametrize(
    "contents",
    ["{corrupt", "[1, 2, 3]", "{}"],
    ids=["corrupt-json", "non-object-json", "no-pid"],
)
def test_unusable_lock_file_is_cleared(lock, contents):
    _write_lock(lock, contents)
    assert lock.status() == {"running": False}
    assert not os.path.exists(lock.lock_path)


def test_set_owner_moves_lock_to_new_pid(lock, tmp_path):
    assert lock.try_acquire() is True
    started_at = _read_lock(lock)["started_at"]

    lock.set_owner(2468)

    assert _read_lock(lock) == {"pid": 2468, "started_at": started_at}
    assert os.listdir(tmp_path) == ["pull.lock"]


def test_release_removes_lock(lock):
    assert lock.try_acquire() is True
    lock.release()
    assert not os.path.exists(lock.lock_path)
    lock.release()  # releasing with no lock file is a no-op
    assert lock.is_busy() is False


# --- InMemoryBusyState ------------------------------------------------------------


def test_in_memory_acquire_status_set_owner_release():
    state = InMemoryBusyState()
    assert state.status() == {"running": False}

    assert state.try_acquire(11) is True
    assert state.try_acquire(22) is False
    status = state.status()
    assert status["running"] is True
    assert status["pid"] == 11
    assert "started_at" in status

    state.set_owner(33)
    assert state.status()["pid"] == 33
    assert state.status()["started_at"] == status["started_at"]

    state.release()
    assert state.is_busy() is False
