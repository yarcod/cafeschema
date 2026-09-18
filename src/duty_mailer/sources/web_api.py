"""Roster read from the duty swap web app's /api/schedule endpoint.

One mail per *shift*, not per day. The endpoint sends one row per person
per station, and grouping those by date alone told everyone rostered that
Saturday that they were on duty together — when the only people a parent
needs to know about are the ones standing in the same café at the same
time. `Shift` is that grouping key, deliberately the same tuple the web
app's own agenda groups on (`_shifts()` in duty_web/routes/schedule_routes.py),
so a card on the site and a mail in the inbox describe the same thing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from typing import Any, NamedTuple

import requests

from ..models import Occurrence, Person
from .rows import RosterError


class Shift(NamedTuple):
    """What makes two rows the same shift. Ordered by when it starts."""

    due: date
    start_time: time
    end_time: time
    duty_name: str
    venue: str


def _text(value: Any) -> str | None:
    """Blank and missing both mean "not stated", never the empty string."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _parse_time(raw: Any) -> time:
    """'12:30' or '12:30:00' -> time(12, 30). The app sends minutes."""
    return time.fromisoformat(str(raw))


def _shift_of(entry: dict) -> Shift:
    return Shift(
        due=datetime.strptime(entry["date"], "%Y-%m-%d").date(),
        start_time=_parse_time(entry["start_time"]),
        end_time=_parse_time(entry["end_time"]),
        duty_name=str(entry.get("duty_name") or ""),
        venue=str(entry.get("venue") or ""),
    )


class WebAppApiSource:
    """Reads the roster from the web app once it is seeded and authoritative."""

    def __init__(
        self,
        url: str,
        *,
        api_key: str,
        default_lead_days: tuple[int, ...],
        timeout: int = 30,
    ):
        self._url = url
        self._api_key = api_key
        self._default_lead_days = default_lead_days
        self._timeout = timeout

    def fetch(self) -> list[Occurrence]:
        try:
            response = requests.get(
                self._url,
                headers={"X-Api-Key": self._api_key},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RosterError(f"Kunde inte hämta schemat från webbappen: {exc}") from exc

        try:
            by_shift: dict[Shift, list[Person]] = defaultdict(list)
            seen_emails: dict[Shift, set[str]] = defaultdict(set)
            key_locations: dict[Shift, str | None] = {}
            document_links: dict[Shift, str | None] = {}
            stations: dict[Shift, set[str | None]] = defaultdict(set)
            for entry in response.json():
                shift = _shift_of(entry)
                # A duty belongs to a player, and either parent may turn up
                # for it, so both are reminded. Older app versions sent the
                # single parent who happened to own the slot instead.
                parents = entry.get("parents")
                if parents is None:
                    parents = [entry["person"]]
                for parent in parents:
                    email = parent["email"]
                    # A parent can switch reminders off in the web app. Older
                    # app versions don't send the field at all, so absence
                    # means "still opted in".
                    if not parent.get("email_notifications", True):
                        continue
                    # One row per station means a parent holding two stations
                    # in the same shift appears twice in the API response;
                    # dedupe by email within the shift so they aren't listed
                    # twice or double-addressed in the To:.
                    if email not in seen_emails[shift]:
                        seen_emails[shift].add(email)
                        by_shift[shift].append(Person(email=email, name=parent["name"]))
                stations[shift].add(_text(entry.get("station")))
                if shift not in key_locations:
                    # The xlsx's key-handoff info travels in the "note" /
                    # "anteckning" column (per the spec's own example row),
                    # and /api/schedule surfaces it as "note" — thread it
                    # through as key_location, matching rows.py's own
                    # first-row-wins behavior, so FORHANDSBESKED emails
                    # don't silently lose it after cutover to this source.
                    note = entry.get("note")
                    key_locations[shift] = note.strip() if isinstance(note, str) else None
                if shift not in document_links:
                    # Which instruction to read is the web app's call — it
                    # owns the documents. One shift is one duty, so this is
                    # now the instruction for exactly the shift being mailed
                    # about. Older app versions don't send the field, and the
                    # mail then falls back to the index from config.yaml.
                    link = entry.get("document_url")
                    document_links[shift] = (
                        link if isinstance(link, str) and link else None
                    )

            return [
                Occurrence(
                    due=shift.due,
                    people=tuple(people),
                    lead_days=self._default_lead_days,
                    key_location=key_locations.get(shift),
                    document_link=document_links.get(shift),
                    start_time=shift.start_time,
                    end_time=shift.end_time,
                    venue=shift.venue or None,
                    # Only when the shift's rows agree on one: a shift split
                    # across two stations has no single place to name, and
                    # guessing at the first would send half the group to the
                    # wrong counter.
                    station=next(iter(stations[shift]))
                    if len(stations[shift]) == 1
                    else None,
                )
                for shift, people in sorted(by_shift.items())
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise RosterError(f"Kunde inte tolka svaret från webbappen: {exc}") from exc
