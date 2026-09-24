"""File-based lock preventing overlapping Pull Data (scrape) runs.

A global in-memory flag is not safe here: Flask's debug=True reloader can
restart the worker process (wiping in-memory state) while a background
subprocess it launched keeps running orphaned, and a plain variable
wouldn't be shared across multiple worker processes under a real WSGI
server either. Instead, the lock is a small JSON file on disk
(module_3/.scrape_lock) holding the subprocess's PID and start time.

Acquisition is atomic (os.O_CREAT | os.O_EXCL) to avoid a check-then-create
race between two near-simultaneous requests. Staleness is detected by
checking whether the recorded PID is still alive, not by a fixed timeout,
so a legitimately slow run is never mistaken for a crash.
"""

import json
import os
from datetime import datetime, timezone

LOCK_PATH = os.path.join(os.path.dirname(__file__), ".scrape_lock")


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _clear_lock():
    try:
        os.remove(LOCK_PATH)
    except FileNotFoundError:
        pass


def get_status():
    """Return {"running": False} or {"running": True, "pid": ..., "started_at": ...}.

    A lock file whose PID is no longer alive (e.g. left behind by a crashed
    Flask process) is treated as stale and removed here.
    """
    if not os.path.exists(LOCK_PATH):
        return {"running": False}

    try:
        with open(LOCK_PATH, "r", encoding="utf-8") as f:
            info = json.load(f)
        pid = info.get("pid")
    except (json.JSONDecodeError, OSError):
        _clear_lock()
        return {"running": False}

    if pid is not None and _pid_alive(pid):
        return {"running": True, **info}

    _clear_lock()
    return {"running": False}


def try_acquire(pid):
    """Atomically create the lock file recording `pid`. Returns True if acquired."""
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False

    with os.fdopen(fd, "w") as f:
        json.dump(
            {"pid": pid, "started_at": datetime.now(timezone.utc).isoformat()}, f
        )
    return True


def release():
    _clear_lock()
