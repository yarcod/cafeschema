"""Naive-UTC "now" helper.

Every timestamp column in models.py is Mapped[datetime] (naive — no tzinfo),
so comparing an aware `now` against a naive `row.expires_at` would raise
TypeError. This helper produces the same naive-UTC value datetime.utcnow()
did, without triggering its deprecation warning, and with zero semantic
change to any existing comparison.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
