"""app.config.require_env: the web service's only source of settings is the environment."""

import pytest

from app.config import ConfigError, require_env

pytestmark = pytest.mark.web


def test_returns_the_value(monkeypatch):
    monkeypatch.setenv("WEB_TEST_SETTING", "amqp://broker.test/")
    assert require_env("WEB_TEST_SETTING") == "amqp://broker.test/"


def test_strips_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv("WEB_TEST_SETTING", "  value \n")
    assert require_env("WEB_TEST_SETTING") == "value"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unset_or_blank_is_config_error(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("WEB_TEST_SETTING", raising=False)
    else:
        monkeypatch.setenv("WEB_TEST_SETTING", value)

    with pytest.raises(ConfigError, match="^WEB_TEST_SETTING is not set$"):
        require_env("WEB_TEST_SETTING")


def test_reads_no_env_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("WEB_TEST_SETTING=from-file\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WEB_TEST_SETTING", raising=False)

    with pytest.raises(ConfigError):
        require_env("WEB_TEST_SETTING")
