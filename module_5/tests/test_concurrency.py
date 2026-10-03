"""Two Pull Data requests at the same instant: exactly one may launch a pull.

Uses the production FileLockBusyState (lock file in tmp_path) and a fake
launcher. threading.Barrier releases both requests together; no sleeps.
Repeated 200 times to shake out interleavings.
"""

import os
import threading

import pytest

from flask_app import create_app
from busy_state import FileLockBusyState

pytestmark = pytest.mark.buttons

ROUNDS = 200


class FakeProcess:
    # A live PID, so the lock stays held for the rest of the round.
    pid = os.getpid()


def test_simultaneous_pulls_launch_exactly_once(tmp_path, test_database_url):
    lock = FileLockBusyState(str(tmp_path / "pull.lock"))
    launches = []

    def launcher():
        launches.append(threading.get_ident())
        return FakeProcess()

    app = create_app(
        {"DATABASE_URL": test_database_url, "TESTING": True},
        busy_state=lock,
        pull_launcher=launcher,
    )

    for round_number in range(ROUNDS):
        barrier = threading.Barrier(2)
        results = []
        results_lock = threading.Lock()

        def fire():
            client = app.test_client()
            barrier.wait()
            response = client.post("/pull-data")
            with results_lock:
                results.append((response.status_code, response.get_json()))

        threads = [threading.Thread(target=fire) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(results, key=lambda r: r[0]) == [(202, {"ok": True}), (409, {"busy": True})], (
            f"round {round_number}: {results}"
        )
        assert len(launches) == round_number + 1, f"round {round_number}: {len(launches)} launches"
        lock.release()
