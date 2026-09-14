"""Tabular row -> Occurrence normalization.

Shared by every tabular adapter. Sees plain dicts, never cells or HTTP, so
adding a new tabular transport means writing "produce dicts" and nothing
else.

Wide and tall layouts are handled by one algorithm: each row contributes
however many people it happens to carry, keyed by its date. A wide row
contributes several at once; repeated dates in a tall sheet merge. Neither
shape is special-cased.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from ..models import Occurrence, Person

DATE_KEYS = ("datum", "date")
EMAIL_PREFIXES = ("epost", "e-post", "email")
NAME_PREFIXES = ("namn", "name")
LEAD_KEYS = ("dagar_innan", "lead_days")
KEY_LOCATION_KEYS = ("nyckelplats", "key_location")

_SUFFIX = re.compile(r"^(?P<prefix>[a-z\-_]+?)(?P<index>\d*)$")


class RosterError(Exception):
    """Raised when the roster cannot be read. Message is operator-facing."""


def _clean_headers(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k).strip().lower(): v for k, v in row.items() if k is not None}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_date(value: Any) -> date | None:
    """Parse a roster date cell. Blank means 'no date', not an error."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise RosterError(
            f"Kunde inte tolka datum: {text!r}. Använd formatet ÅÅÅÅ-MM-DD."
        ) from exc


def _parse_lead_days(value: Any, default: tuple[int, ...]) -> tuple[int, ...]:
    text = _text(value)
    if text is None:
        return default
    try:
        return tuple(int(part) for part in text.replace(" ", "").split(",") if part)
    except ValueError as exc:
        raise RosterError(
            f"Kunde inte tolka antal dagar innan: {text!r}. Använd t.ex. '7,1'."
        ) from exc


def _first(row: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _people_in_row(row: Mapping[str, Any]) -> list[Person]:
    """Every person this row carries, in column order.

    An email column may be bare ('epost') or numbered ('epost1'); a name
    column with the same suffix is paired with it. Sorted numerically by
    index: lexicographically 'epost10' would sort before 'epost2'.
    """
    found: list[tuple[int, Person]] = []
    for key, value in row.items():
        match = _SUFFIX.match(key)
        if match is None or match.group("prefix") not in EMAIL_PREFIXES:
            continue
        email = _text(value)
        if email is None:
            continue
        index = match.group("index")
        name = None
        for prefix in NAME_PREFIXES:
            name = _text(row.get(f"{prefix}{index}"))
            if name is not None:
                break
        found.append((int(index) if index else 0, Person(email=email.lower(), name=name)))
    return [person for _, person in sorted(found, key=lambda pair: pair[0])]


def occurrences_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    default_lead_days: tuple[int, ...],
) -> list[Occurrence]:
    """Normalize tabular rows into sorted, validated occurrences."""
    people_by_date: dict[date, list[Person]] = {}
    extras: dict[date, tuple[Any, Any]] = {}
    saw_date_column = False
    saw_email_column = False

    for raw in rows:
        row = _clean_headers(raw)

        if any(key in row for key in DATE_KEYS):
            saw_date_column = True
        if any(
            (m := _SUFFIX.match(key)) and m.group("prefix") in EMAIL_PREFIXES
            for key in row
        ):
            saw_email_column = True

        due = parse_date(_first(row, DATE_KEYS))
        if due is None:
            continue

        people = people_by_date.setdefault(due, [])
        seen = {p.email for p in people}
        for person in _people_in_row(row):
            if person.email not in seen:
                seen.add(person.email)
                people.append(person)

        if due not in extras:
            extras[due] = (
                _first(row, LEAD_KEYS),
                _text(_first(row, KEY_LOCATION_KEYS)),
            )

    if not saw_date_column:
        raise RosterError(
            "Schemat saknar en datumkolumn (förväntade en kolumn som heter 'datum')."
        )
    if not saw_email_column:
        raise RosterError(
            "Schemat saknar e-postkolumner (förväntade 'epost', 'epost1', ...)."
        )

    occurrences: list[Occurrence] = []
    for due in sorted(people_by_date):
        people = people_by_date[due]
        if not people:
            # A future row with nobody assigned yet: normal, not an error.
            continue
        lead_raw, key_location = extras[due]
        try:
            occurrences.append(
                Occurrence(
                    due=due,
                    people=tuple(people),
                    lead_days=_parse_lead_days(lead_raw, default_lead_days),
                    key_location=key_location,
                )
            )
        except ValueError as exc:
            raise RosterError(str(exc)) from exc
    return occurrences
