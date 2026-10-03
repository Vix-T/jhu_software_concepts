"""pull_data.py: the port pre-flight, scrape_new_entries, run_pull, result files, and the CLI."""

import errno
import json
import os
import runpy
import socket

import pytest
from conftest import SURVEY_URL, DriverFactory, FakeScraper, Spy, survey_pages
from selenium.common.exceptions import WebDriverException

import pull_data

pytestmark = pytest.mark.buttons

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _result(path):
    return json.loads(path.read_text())


def test_port_open_when_something_listens():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert pull_data._debugger_port_open("127.0.0.1", port) is True


def test_port_closed_when_nothing_listens():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert pull_data._debugger_port_open("127.0.0.1", port) is False


def test_scrape_new_entries_requires_chrome():
    is_known = Spy(lambda urls: set())

    with pytest.raises(pull_data.PullPreconditionError) as excinfo:
        pull_data.scrape_new_entries(port_check=lambda: False, is_known=is_known)

    assert str(excinfo.value) == pull_data.CLOUDFLARE_PRECONDITION_MESSAGE
    assert is_known.calls == []


def test_scrape_new_entries_with_driver_factory_skips_port_check():
    port_check = Spy(lambda: False)
    factory = DriverFactory(survey_pages())

    # Default is_known: the (empty) test database, so page 1's entries are all new.
    entries = pull_data.scrape_new_entries(
        driver_factory=factory, port_check=port_check, start_url=SURVEY_URL, delay_seconds=0,
    )

    assert port_check.calls == []
    assert [e["University"] for e in entries] == [
        "Stanford University", "Johns Hopkins University", "Georgetown University",
    ]
    # Page 2's entries have no URL, so it yields nothing new and the pull stops there.
    assert factory.visited == [SURVEY_URL, "https://www.thegradcafe.com/survey/?page=2"]


def test_run_pull_returns_counts(fake_records):
    loader = Spy(lambda records: (2, 1, [(3, "bad")]))

    result = pull_data.run_pull(FakeScraper(fake_records), loader)

    assert result == {"scraped": 3, "inserted": 2, "skipped": 1, "failed": [(3, "bad")]}
    assert loader.calls == [(fake_records,)]


def test_default_result_path_follows_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PULL_RESULT_FILE", str(tmp_path / "elsewhere.json"))
    assert pull_data.default_result_path() == str(tmp_path / "elsewhere.json")

    monkeypatch.delenv("PULL_RESULT_FILE")
    assert pull_data.default_result_path() == pull_data.PULL_RESULT_FILE
    assert pull_data.PULL_RESULT_FILE == os.path.join(SRC_DIR, "_pull_data_result.json")


def test_write_then_read_pull_result(tmp_path):
    path = tmp_path / "result.json"
    result = pull_data.pull_result(run={"inserted": 4, "skipped": 1, "failed": [(0, "x")]})

    pull_data.write_pull_result(str(path), result)

    assert pull_data.read_pull_result(str(path)) == result
    assert result["ok"] is True
    assert (result["inserted"], result["skipped"], result["failed"], result["error"]) == (4, 1, 1, None)
    assert os.listdir(tmp_path) == ["result.json"]


@pytest.mark.parametrize("contents", [None, "{half-written", "[1, 2]"], ids=["missing", "corrupt", "not-object"])
def test_read_pull_result_without_usable_file(tmp_path, contents):
    path = tmp_path / "result.json"
    if contents is not None:
        path.write_text(contents)

    assert pull_data.read_pull_result(str(path)) is None


def test_main_success_loads_and_records_result(fake_records, row_count, capsys, pull_result_path):
    pull_data.main(scraper=FakeScraper(fake_records))

    assert row_count() == 3
    assert capsys.readouterr().out == (
        "Pull complete: 3 entries scraped, 3 inserted, 0 already present, 0 failed to parse.\n"
    )
    result = _result(pull_result_path)
    assert {k: result[k] for k in ("ok", "inserted", "skipped", "failed", "error")} == {
        "ok": True, "inserted": 3, "skipped": 0, "failed": 0, "error": None,
    }
    assert result["finished_at"]


def test_main_exits_when_chrome_missing(capsys, pull_result_path):
    scraper = FakeScraper(raises=pull_data.PullPreconditionError(pull_data.CLOUDFLARE_PRECONDITION_MESSAGE))

    with pytest.raises(SystemExit) as excinfo:
        pull_data.main(scraper=scraper)

    assert excinfo.value.code == 1
    assert capsys.readouterr().out == pull_data.CLOUDFLARE_PRECONDITION_MESSAGE + "\n"
    result = _result(pull_result_path)
    assert result["ok"] is False
    assert result["error"] == pull_data.CLOUDFLARE_PRECONDITION_MESSAGE
    assert result["inserted"] == 0


def test_main_exits_when_selenium_cannot_attach(capsys, row_count, pull_result_path):
    with pytest.raises(SystemExit) as excinfo:
        pull_data.main(scraper=FakeScraper(raises=WebDriverException("cannot connect to chrome")))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith(pull_data.CLOUDFLARE_PRECONDITION_MESSAGE)
    assert "Underlying error: Message: cannot connect to chrome" in out
    assert row_count() == 0
    result = _result(pull_result_path)
    assert result["ok"] is False
    assert result["error"] == "could not attach to Chrome: Message: cannot connect to chrome"


def test_main_records_unexpected_failure(capsys, row_count, tmp_path):
    result_path = tmp_path / "explicit.json"

    def broken_loader(records):
        raise RuntimeError("disk full")

    with pytest.raises(SystemExit) as excinfo:
        pull_data.main(scraper=FakeScraper([{"URL": "u"}]), loader=broken_loader, result_path=str(result_path))

    assert excinfo.value.code == 1
    assert capsys.readouterr().out == "PULL FAILED: RuntimeError: disk full\n"
    assert _result(result_path)["error"] == "RuntimeError: disk full"
    assert row_count() == 0


def test_cli_entry_point_fails_fast_without_chrome(monkeypatch, capsys, pull_result_path):
    # Network is faked: the pre-flight connect to Chrome's debugger port is refused.
    attempts = []

    def refuse(self, address):
        attempts.append(address)
        raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(os.path.join(SRC_DIR, "pull_data.py"), run_name="__main__")

    assert excinfo.value.code == 1
    assert attempts == [("127.0.0.1", 9222)]
    assert capsys.readouterr().out == pull_data.CLOUDFLARE_PRECONDITION_MESSAGE + "\n"
    assert _result(pull_result_path)["ok"] is False
