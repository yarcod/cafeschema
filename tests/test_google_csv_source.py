from __future__ import annotations

from datetime import date

import pytest
import requests

from duty_mailer.sources.google_csv import GoogleCsvSource
from duty_mailer.sources.rows import RosterError

CSV = "datum,epost1,namn1,epost2,namn2\n2026-09-19,a@x.se,Anna,b@x.se,Björn\n"

URL = "https://docs.google.com/spreadsheets/d/abc/export?format=csv"


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def test_parses_a_csv_export(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda url, timeout: FakeResponse(CSV)
    )
    occs = GoogleCsvSource(URL, default_lead_days=(1,)).fetch()
    assert [o.due for o in occs] == [date(2026, 9, 19)]
    assert [p.name for p in occs[0].people] == ["Anna", "Björn"]


def test_requests_the_configured_url(monkeypatch):
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        seen["timeout"] = timeout
        return FakeResponse(CSV)

    monkeypatch.setattr(requests, "get", fake_get)
    GoogleCsvSource(URL, default_lead_days=(1,), timeout=12).fetch()
    assert seen == {"url": URL, "timeout": 12}


def test_an_http_error_is_reported_clearly(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda url, timeout: FakeResponse("", status=404)
    )
    with pytest.raises(RosterError, match="Kunde inte hämta"):
        GoogleCsvSource(URL, default_lead_days=(1,)).fetch()


def test_a_network_failure_is_reported_clearly(monkeypatch):
    def boom(url, timeout):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(RosterError, match="Kunde inte hämta"):
        GoogleCsvSource(URL, default_lead_days=(1,)).fetch()


def test_a_login_page_instead_of_csv_is_reported_clearly(monkeypatch):
    # A sheet that is not link-shared returns an HTML sign-in page with 200.
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, timeout: FakeResponse("<!DOCTYPE html><html>Sign in</html>"),
    )
    with pytest.raises(RosterError, match="delad"):
        GoogleCsvSource(URL, default_lead_days=(1,)).fetch()
