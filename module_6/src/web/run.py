"""Start the web service: python run.py (from src/web, or the image's working directory).

Listens on 0.0.0.0:8080 so it is reachable from outside its container.
Needs DATABASE_URL to start; RABBITMQ_URL is read when a button publishes.
"""

import os
import sys

from app import create_app
from app.config import ConfigError

HOST = "0.0.0.0"
PORT = 8080
DEBUG_ENV = "FLASK_DEBUG"


def debug_enabled():
    """True only when FLASK_DEBUG is 1/true/yes: the debugger and reloader are off by default."""
    return os.environ.get(DEBUG_ENV, "").strip().lower() in ("1", "true", "yes")


def main():
    """Run the server; exit 1 with a clear message if DATABASE_URL is missing."""
    try:
        app = create_app()
    except ConfigError as exc:
        print(f"Cannot start the web service: {exc}")
        sys.exit(1)
    app.run(host=HOST, port=PORT, debug=debug_enabled(), threaded=True)


if __name__ == "__main__":
    main()
