"""Busy state preventing overlapping Pull Data runs (and Update Analysis during a pull).

Two implementations share one interface:

    try_acquire(pid=None) -> bool   atomically mark busy on behalf of `pid`
    set_owner(pid) -> None          re-point a held mark at `pid` (e.g. the
                                    pull subprocess launched after acquiring)
    release() -> None               clear the busy mark
    is_busy() -> bool               True while a holder is marked busy
    status() -> dict                {"running": False} or
                                    {"running": True, "pid": ..., "started_at": ...}

FileLockBusyState is the production implementation. A global in-memory flag
is not safe there: Flask's debug=True reloader can restart the worker
process (wiping in-memory state) while a background pull subprocess it
launched keeps running, and a plain variable wouldn't be shared across
multiple worker processes under a real WSGI server either. Instead, the
lock is a small JSON file on disk holding the holder's PID and start time.

Acquisition is atomic (os.O_CREAT | os.O_EXCL) to avoid a check-then-create
race between two near-simultaneous requests. Staleness is detected by
checking whether the recorded PID is still alive, not by a fixed timeout,
so a legitimately slow run is never mistaken for a crash. When the PID is a
child of this process, os.waitpid(WNOHANG) both checks it and reaps it once
it has exited (an unreaped zombie would otherwise still pass an os.kill(pid, 0)
check forever). A PID that isn't our child (e.g. launched before a reloader
restart, or this process itself during an in-process pull) raises
ChildProcessError there, and liveness falls back to os.kill(pid, 0).

InMemoryBusyState is the test implementation: same interface, no files,
no PIDs checked.
"""

import json
import os
from datetime import datetime, timezone


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid):
    """True if `pid` is still running. Reaps it if it is an exited child of ours."""
    try:
        reaped_pid, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return _pid_exists(pid)
    return reaped_pid == 0


def _pid_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class FileLockBusyState:
    """Busy state backed by a JSON lock file at `lock_path`."""

    def __init__(self, lock_path):
        self.lock_path = lock_path

    def _clear(self):
        try:
            os.remove(self.lock_path)
        except FileNotFoundError:
            pass

    def status(self):
        """Return the lock's contents if its holder is alive; clear it if stale.

        A lock file whose PID is no longer alive (e.g. left behind by a
        crashed Flask process) or that can't be parsed is removed here.
        """
        if not os.path.exists(self.lock_path):
            return {"running": False}

        try:
            with open(self.lock_path, "r", encoding="utf-8") as f:
                info = json.load(f)
            pid = info.get("pid")
        except (json.JSONDecodeError, OSError, AttributeError):
            self._clear()
            return {"running": False}

        if pid is not None and _pid_alive(pid):
            return {"running": True, **info}

        self._clear()
        return {"running": False}

    def is_busy(self):
        return self.status()["running"]

    def try_acquire(self, pid=None):
        """Atomically create the lock file recording `pid` (default: this process).

        Returns True if acquired, False if another live holder has it.
        """
        # Clears a stale lock first, so a crashed holder can't block forever.
        self.status()
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": pid if pid is not None else os.getpid(), "started_at": _now_iso()}, f)
        return True

    def set_owner(self, pid):
        """Record `pid` as the holder of the lock this process already holds.

        Keeps the original started_at. The new contents are written to a
        temporary file and swapped in with os.replace(), so a concurrent
        status() never sees a half-written lock file.
        """
        with open(self.lock_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        info["pid"] = pid
        tmp_path = f"{self.lock_path}.{os.getpid()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(info, f)
        os.replace(tmp_path, self.lock_path)

    def release(self):
        self._clear()


class InMemoryBusyState:
    """Busy state held in memory -- for tests and single-process use."""

    def __init__(self):
        self._info = None

    def status(self):
        if self._info is None:
            return {"running": False}
        return {"running": True, **self._info}

    def is_busy(self):
        return self._info is not None

    def try_acquire(self, pid=None):
        if self._info is not None:
            return False
        self._info = {"pid": pid, "started_at": _now_iso()}
        return True

    def set_owner(self, pid):
        self._info["pid"] = pid

    def release(self):
        self._info = None
