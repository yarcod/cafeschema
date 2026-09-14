"""Entry point for the public-facing process."""

from __future__ import annotations

from .app import create_app
from .config import load_config

if __name__ == "__main__":
    app = create_app(load_config())
    app.run(host="0.0.0.0", port=8080)
