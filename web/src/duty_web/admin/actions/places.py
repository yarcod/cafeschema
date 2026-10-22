"""Platser: venues, their stations, and each station's instruction (spec D4).

Shared by the whole club, so these are club-scope actions: an ansvarig in
any team may add a place or a station and link its instruction. A name
that normalizes to an existing one is refused with the existing one as a
suggestion, never quietly added as a second place.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import Station, Venue
from ...names import normalize_key
from ...places import find_station, find_venue, linkable_documents, title_for
from .base import Action, NearDuplicate, Plan, ValidationError, register

MAX_NAME = 80


def _name(raw: str, field_name: str) -> str:
    name = " ".join((raw or "").split())
    if not normalize_key(name):
        raise ValidationError({field_name: "Skriv ett namn"})
    if len(name) > MAX_NAME:
        raise ValidationError({field_name: f"Högst {MAX_NAME} tecken"})
    return name


def _document(filename: str | None) -> tuple[str | None, str]:
    """(filename or None, title for Granska). Only uploaded files are allowed."""
    if not filename:
        return None, "ingen"
    for doc in linkable_documents():
        if doc.filename == filename:
            return doc.filename, doc.title
    raise ValidationError({"document": "Okänd instruktion"})


def _document_choices() -> list[tuple[str, str]]:
    return [
        (d.filename, d.title)
        for d in linkable_documents()
    ]


@dataclass
class VenueCreateInput:
    name: str


@register
class CreateVenue(Action):
    name = "venue.create"
    title = "Ny plats"
    scope = "club"
    Input = VenueCreateInput
    template = "admin/actions/generic.html"
    done_endpoint = "admin.platser"

    def form_context(self, session, ctx):
        return {"labels": {"name": "Platsens namn"}}

    def plan(self, session, ctx, inp):
        name = _name(inp.name, "name")
        existing = find_venue(session, name)
        if existing is not None:
            raise NearDuplicate("name", existing.name, existing.id)
        plan = Plan()
        plan.add("add", "venue", f"Ny plats: {name}")
        plan.writes.append(lambda s: s.add(Venue(name=name, name_key=normalize_key(name))))
        return plan


@dataclass
class StationCreateInput:
    venue_id: int
    name: str
    document: str | None = None


@register
class CreateStation(Action):
    name = "station.create"
    title = "Ny station"
    scope = "club"
    Input = StationCreateInput
    template = "admin/actions/generic.html"
    done_endpoint = "admin.platser"

    def form_context(self, session, ctx):
        venues = session.query(Venue).order_by(Venue.name).all()
        return {
            "labels": {"venue_id": "Plats", "name": "Stationens namn", "document": "Instruktion"},
            "choices": {
                "venue_id": [(v.id, v.name) for v in venues],
                "document": _document_choices(),
            },
        }

    def plan(self, session, ctx, inp):
        venue = session.get(Venue, inp.venue_id)
        if venue is None:
            raise ValidationError({"venue_id": "Okänd plats"})
        name = _name(inp.name, "name")
        existing = find_station(session, venue, name)
        if existing is not None:
            raise NearDuplicate("name", existing.name, existing.id)
        document, title = _document(inp.document)
        plan = Plan()
        plan.add("add", "station", f"Ny station: {name}", f"på {venue.name} · instruktion: {title}")
        venue_id = venue.id
        plan.writes.append(lambda s: s.add(Station(
            venue_id=venue_id, name=name, name_key=normalize_key(name), document=document
        )))
        return plan


@dataclass
class LinkDocumentInput:
    station_id: int
    document: str | None = None


@register
class LinkDocument(Action):
    name = "station.link_document"
    title = "Koppla instruktion"
    scope = "club"
    Input = LinkDocumentInput
    template = "admin/actions/generic.html"
    done_endpoint = "admin.platser"

    def form_context(self, session, ctx):
        stations = session.query(Station).join(Venue).order_by(Venue.name, Station.name).all()
        return {
            "labels": {"station_id": "Station", "document": "Instruktion"},
            "choices": {
                "station_id": [(s.id, f"{s.name} ({s.venue.name})") for s in stations],
                "document": _document_choices(),
            },
        }

    def plan(self, session, ctx, inp):
        station = session.get(Station, inp.station_id)
        if station is None:
            raise ValidationError({"station_id": "Okänd station"})
        document, title = _document(inp.document)
        plan = Plan()
        if document == station.document:
            return plan
        before = title_for(station.document) if station.document else "ingen"
        plan.add("update", "station", f"Instruktion för {station.name} ({station.venue.name})",
                 f"{before} → {title}")
        station_id = station.id

        def write(s):
            s.get(Station, station_id).document = document

        plan.writes.append(write)
        return plan
