"""Roster read from a Google Sheets CSV export URL."""

from __future__ import annotations

import csv
import io

import requests

from ..models import Occurrence
from .rows import RosterError, occurrences_from_rows


class GoogleCsvSource:
    """Reads the roster from a published/link-shared Sheets CSV export."""

    def __init__(
        self,
        url: str,
        *,
        default_lead_days: tuple[int, ...],
        timeout: int = 30,
    ):
        self._url = url
        self._default_lead_days = default_lead_days
        self._timeout = timeout

    def fetch(self) -> list[Occurrence]:
        try:
            response = requests.get(self._url, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RosterError(f"Kunde inte hämta schemat: {exc}") from exc

        response.encoding = "utf-8"
        text = response.text

        if text.lstrip()[:9].lower().startswith("<!doctype") or text.lstrip().startswith(
            "<html"
        ):
            raise RosterError(
                "Fick en HTML-sida istället för CSV. Kontrollera att kalkylarket "
                "är delad med länk och att URL:en slutar med '/export?format=csv'."
            )

        rows = list(csv.DictReader(io.StringIO(text)))
        return occurrences_from_rows(
            rows, default_lead_days=self._default_lead_days
        )
