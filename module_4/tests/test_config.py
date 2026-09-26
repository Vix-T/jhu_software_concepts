"""config.get_database_url(): environment first, then the .env file, else a clear error."""

import pytest

from config import get_database_url

pytestmark = pytest.mark.db


def test_missing_database_url_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        get_database_url(env_file=str(tmp_path / "missing.env"))


def test_database_url_read_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL")
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://localhost:5432/from_file_test\n")

    assert get_database_url(env_file=str(env_file)) == "postgresql://localhost:5432/from_file_test"


def test_environment_wins_over_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/from_environment_test")
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://localhost:5432/from_file_test\n")

    assert get_database_url(env_file=str(env_file)) == "postgresql://localhost:5432/from_environment_test"
