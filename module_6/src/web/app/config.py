"""Web service settings: read from the process environment only (Docker passes them in)."""

import os


class ConfigError(RuntimeError):
    """A required environment variable is missing or empty."""


def require_env(name):
    """Return environment variable `name`, stripped; ConfigError if it is unset or blank."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    raise ConfigError(f"{name} is not set")
