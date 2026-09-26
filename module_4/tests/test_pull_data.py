"""pull_data.py: the port pre-flight, scrape_new_entries, run_pull, and the CLI."""

import os
import runpy
import socket

import pytest
from conftest import SURVEY_URL, DriverFactory, FakeScraper, Spy, survey_pages
from selenium.common.exceptions import WebDriverException

import pull_data

pytestmark = pytest.mark.buttons

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


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


def test_scrape_new_entries_requires_chrome(tmp_path):
    with pytest.raises(pull_data.PullPreconditionError) as excinfo:
        pull_data.scrape_new_entries(
            state_file=str(tmp_path / "state.json"),
            captured_dir=str(tmp_path / "pages"),
            port_check=lambda: False,
        )
    assert str(excinfo.value) == pull_data.CLOUDFLARE_PRECONDITION_MESSAGE
    assert not (tmp_path / "pages").exists()


def test_scrape_new_entries_with_driver_factory_skips_port_check(tmp_path):
    port_check = Spy(lambda: False)
    factory = DriverFactory(survey_pages())

    entries = pull_data.scrape_new_entries(
        target_count=100,
        state_file=str(tmp_path / "state.json"),
        captured_dir=str(tmp_path / "pages"),
        driver_factory=factory,
        port_check=port_check,
        start_url=SURVEY_URL,
        delay_seconds=0,
    )

    assert port_check.calls == []
    assert [e["University"] for e in entries] == [
        "Stanford University", "Johns Hopkins University", "Georgetown University",
        "Carnegie Mellon University", "Truncated Row University",
    ]


def test_run_pull_returns_counts(fake_records):
    loader = Spy(lambda records: (2, 1, [(3, "bad")]))

    result = pull_data.run_pull(FakeScraper(fake_records), loader)

    assert result == {"scraped": 3, "inserted": 2, "skipped": 1, "failed": [(3, "bad")]}
    assert loader.calls == [(fake_records,)]


def test_main_success_loads_into_database(fake_records, row_count, capsys):
    pull_data.main(scraper=FakeScraper(fake_records))

    assert row_count() == 3
    assert capsys.readouterr().out == (
        "Pull complete: 3 entries scraped, 3 inserted, 0 already present, 0 failed to parse.\n"
    )


def test_main_exits_when_chrome_missing(capsys):
    scraper = FakeScraper(raises=pull_data.PullPreconditionError(pull_data.CLOUDFLARE_PRECONDITION_MESSAGE))

    with pytest.raises(SystemExit) as excinfo:
        pull_data.main(scraper=scraper)

    assert excinfo.value.code == 1
    assert capsys.readouterr().out == pull_data.CLOUDFLARE_PRECONDITION_MESSAGE + "\n"


def test_main_exits_when_selenium_cannot_attach(capsys, row_count):
    with pytest.raises(SystemExit) as excinfo:
        pull_data.main(scraper=FakeScraper(raises=WebDriverException("cannot connect to chrome")))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert out.startswith(pull_data.CLOUDFLARE_PRECONDITION_MESSAGE)
    assert "Underlying error: Message: cannot connect to chrome" in out
    assert row_count() == 0


def test_cli_entry_point_fails_fast_without_chrome(monkeypatch, capsys):
    # Network is faked: the pre-flight connect to Chrome's debugger port is refused.
    attempts = []

    def refuse(self, address):
        attempts.append(address)
        raise ConnectionRefusedError(61, "Connection refused")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(os.path.join(SRC_DIR, "pull_data.py"), run_name="__main__")

    assert excinfo.value.code == 1
    assert attempts == [("127.0.0.1", 9222)]
    assert capsys.readouterr().out == pull_data.CLOUDFLARE_PRECONDITION_MESSAGE + "\n"
