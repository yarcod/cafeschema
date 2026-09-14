"""Swedish date formatting, hardcoded rather than locale-derived — same
convention as duty_mailer's templates.py, since neither dev machines nor
the deployed container are guaranteed to have an sv_SE locale installed."""

from __future__ import annotations

from datetime import date

MONTHS_SV = (
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
)
MONTHS_SV_SHORT = (
    "jan", "feb", "mar", "apr", "maj", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec",
)
WEEKDAYS_SV = (
    "måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag",
)


def weekday_sv(d: date) -> str:
    """E.g. 'fredag'."""
    return WEEKDAYS_SV[d.weekday()]


def short_date_sv(d: date) -> str:
    """E.g. '16 jan'."""
    return f"{d.day} {MONTHS_SV_SHORT[d.month - 1]}"


def long_date_sv(d: date) -> str:
    """E.g. 'fredag 16 januari'."""
    return f"{weekday_sv(d)} {d.day} {MONTHS_SV[d.month - 1]}"
