"""Entry point for the localhost-only admin process."""

from __future__ import annotations

from .admin_app import create_admin_app
from .config import load_config

if __name__ == "__main__":
    app = create_admin_app(load_config())
    app.run(host="127.0.0.1", port=8081)
