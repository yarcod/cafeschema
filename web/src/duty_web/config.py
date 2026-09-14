"""Environment-only configuration. No secrets in a committed file."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when required environment variables are missing."""


@dataclass(frozen=True)
class AppConfig:
    secret_key: str
    db_path: str
    api_key: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    from_address: str
    # Hostnames this app answers on. Empty means "any", which is fine
    # locally but should always be set in production — see create_app.
    public_hosts: tuple[str, ...] = ()
    # Documents live on the volume, not in the image, so adding one needs
    # no deploy.
    documents_dir: str = "/data/dokument"


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Miljövariabeln {name} saknas")
    return value


def load_config() -> AppConfig:
    return AppConfig(
        secret_key=_require("SECRET_KEY"),
        db_path=os.environ.get("DB_PATH", "/data/duty.db"),
        api_key=_require("SCHEDULE_API_KEY"),
        smtp_host=_require("SMTP_HOST"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=_require("SMTP_USER"),
        smtp_password=_require("SMTP_PASSWORD"),
        from_address=_require("FROM_ADDRESS"),
        documents_dir=os.environ.get("DOCUMENTS_DIR", "/data/dokument"),
        public_hosts=tuple(
            host.strip().lower()
            for host in os.environ.get("PUBLIC_HOSTS", "").split(",")
            if host.strip()
        ),
    )
