"""Config loading.

config.yaml is committed and holds no secrets; SMTP_PASSWORD comes from the
environment only. Validation happens here so a misconfiguration fails
immediately with a readable Swedish message in the Actions log, rather than
halfway through a send.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import MAX_LEAD_TIMES

VALID_SOURCES = ("xlsx", "google_csv", "web_api")


class ConfigError(Exception):
    """Raised when config.yaml is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class ScheduleConfig:
    source: str
    chore: str
    path: str | None = None
    url: str | None = None
    api_key: str | None = None
    default_lead_days: tuple[int, ...] = (1,)
    timezone: str = "Europe/Stockholm"
    schedule_link: str | None = None
    documents_link: str | None = None
    max_handoff_gap_days: int = 8


@dataclass(frozen=True)
class EmailConfig:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    from_address: str
    reply_to: str | None = None


@dataclass(frozen=True)
class Config:
    schedule: ScheduleConfig
    email: EmailConfig


def _require(section: dict[str, Any], key: str, where: str) -> Any:
    if key not in section or section[key] is None:
        raise ConfigError(f"Saknad inställning: {where}.{key}")
    return section[key]


def load_config(path: Path) -> Config:
    """Read and validate config.yaml."""
    if not path.is_file():
        raise ConfigError(f"Konfigurationsfilen hittades inte: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    schedule_raw = raw.get("schedule") or {}
    email_raw = raw.get("email") or {}

    source = _require(schedule_raw, "source", "schedule")
    if source not in VALID_SOURCES:
        raise ConfigError(
            f"Okänd källa '{source}'. Giltiga värden: {', '.join(VALID_SOURCES)}"
        )

    lead_days = tuple(schedule_raw.get("default_lead_days") or (1,))
    if len(lead_days) > MAX_LEAD_TIMES:
        raise ConfigError(
            f"schedule.default_lead_days: högst {MAX_LEAD_TIMES} påminnelser, "
            f"fick {list(lead_days)}"
        )

    schedule = ScheduleConfig(
        source=source,
        chore=_require(schedule_raw, "chore", "schedule"),
        path=schedule_raw.get("path"),
        url=schedule_raw.get("url"),
        api_key=os.environ.get("SCHEDULE_API_KEY"),
        default_lead_days=tuple(sorted(lead_days, reverse=True)),
        timezone=schedule_raw.get("timezone", "Europe/Stockholm"),
        schedule_link=schedule_raw.get("schedule_link"),
        documents_link=schedule_raw.get("documents_link"),
        max_handoff_gap_days=schedule_raw.get("max_handoff_gap_days", 8),
    )

    if schedule.source == "xlsx" and not schedule.path:
        raise ConfigError("schedule.path krävs när schedule.source är 'xlsx'")
    if schedule.source == "google_csv" and not schedule.url:
        raise ConfigError("schedule.url krävs när schedule.source är 'google_csv'")
    if schedule.source == "web_api" and not schedule.url:
        raise ConfigError("schedule.url krävs när schedule.source är 'web_api'")
    if schedule.source == "web_api" and not schedule.api_key:
        raise ConfigError("Miljövariabeln SCHEDULE_API_KEY krävs när schedule.source är 'web_api'")

    email = EmailConfig(
        smtp_host=_require(email_raw, "smtp_host", "email"),
        smtp_port=int(_require(email_raw, "smtp_port", "email")),
        smtp_user=_require(email_raw, "smtp_user", "email"),
        from_address=_require(email_raw, "from_address", "email"),
        reply_to=email_raw.get("reply_to"),
    )

    return Config(schedule=schedule, email=email)
