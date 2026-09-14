"""Roster sources.

The rest of the application depends only on `ScheduleSource`. Nothing above
this package knows whether the roster is a spreadsheet, a CSV export, or an
API — which is what lets a 360Player adapter drop in later without touching
any other module.
"""

from __future__ import annotations

from typing import Protocol

from ..config import ScheduleConfig
from ..models import Occurrence
from .rows import RosterError

__all__ = ["RosterError", "ScheduleSource", "build_source"]


class ScheduleSource(Protocol):
    """Read-only access to the duty roster."""

    def fetch(self) -> list[Occurrence]:
        """All known occurrences. Never writes to the underlying source."""
        ...


def build_source(cfg: ScheduleConfig) -> ScheduleSource:
    """Construct the adapter named by the config."""
    if cfg.source == "xlsx":
        from .xlsx import XlsxSource

        assert cfg.path is not None  # guaranteed by config validation
        return XlsxSource(cfg.path, default_lead_days=cfg.default_lead_days)

    if cfg.source == "google_csv":
        from .google_csv import GoogleCsvSource

        assert cfg.url is not None  # guaranteed by config validation
        return GoogleCsvSource(cfg.url, default_lead_days=cfg.default_lead_days)

    if cfg.source == "web_api":
        from .web_api import WebAppApiSource

        assert cfg.url is not None  # guaranteed by config validation
        assert cfg.api_key is not None  # guaranteed by config validation
        return WebAppApiSource(
            cfg.url, api_key=cfg.api_key, default_lead_days=cfg.default_lead_days
        )

    raise RosterError(f"Okänd schemakälla: {cfg.source}")
