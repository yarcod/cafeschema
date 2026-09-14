"""Roster read from the duty swap web app's /api/schedule endpoint."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

import requests

from ..models import Occurrence, Person
from .rows import RosterError


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
            by_date: dict[date, list[Person]] = defaultdict(list)
            seen_emails: dict[date, set[str]] = defaultdict(set)
            key_locations: dict[date, str | None] = {}
            for entry in response.json():
                due = datetime.strptime(entry["date"], "%Y-%m-%d").date()
                person_data = entry["person"]
                email = person_data["email"]
                # A parent can switch reminders off in the web app. Older
                # app versions don't send the field at all, so absence
                # means "still opted in".
                if not person_data.get("email_notifications", True):
                    continue
                # One row per station means a parent holding two stations on
                # the same date appears twice in the API response; dedupe by
                # email within the date, matching rows.py's own behavior, so
                # they aren't listed twice or double-addressed in the To:.
                if email not in seen_emails[due]:
                    seen_emails[due].add(email)
                    by_date[due].append(Person(email=email, name=person_data["name"]))
                if due not in key_locations:
                    # The xlsx's key-handoff info travels in the "note" /
                    # "anteckning" column (per the spec's own example row),
                    # and /api/schedule surfaces it as "note" — thread it
                    # through as key_location, matching rows.py's own
                    # first-row-wins behavior, so FORHANDSBESKED emails
                    # don't silently lose it after cutover to this source.
                    note = entry.get("note")
                    key_locations[due] = note.strip() if isinstance(note, str) else None

            return [
                Occurrence(
                    due=due,
                    people=tuple(people),
                    lead_days=self._default_lead_days,
                    key_location=key_locations.get(due),
                )
                for due, people in sorted(by_date.items())
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise RosterError(f"Kunde inte tolka svaret från webbappen: {exc}") from exc
