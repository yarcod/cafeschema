"""Venues and their stations: where a shift is held, and its instruction.

Names are compared by normalize_key only, so "cafe", "Cafe" and "Café" can
never become three stations with three different instruction links
(spec D4). The display name is whatever was entered first.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .models import Slot, Station, Venue
from .names import normalize_key


def find_venue(session: Session, name: str) -> Venue | None:
    return session.query(Venue).filter_by(name_key=normalize_key(name)).one_or_none()


def find_station(session: Session, venue: Venue, name: str) -> Station | None:
    return (
        session.query(Station)
        .filter_by(venue_id=venue.id, name_key=normalize_key(name))
        .one_or_none()
    )


def find_or_create_station(session: Session, venue_name: str, station_name: str) -> Station:
    """The station, creating its venue and itself as needed. Flushes, never commits."""
    venue = find_venue(session, venue_name)
    if venue is None:
        venue = Venue(name=" ".join(venue_name.split()), name_key=normalize_key(venue_name))
        session.add(venue)
        session.flush()
    station = find_station(session, venue, station_name)
    if station is None:
        station = Station(
            venue_id=venue.id,
            name=" ".join(station_name.split()),
            name_key=normalize_key(station_name),
        )
        session.add(station)
        session.flush()
    return station


# Anything a parent could reasonably need to read on a phone. Kept to a
# known list so a stray file on the volume can't be served as something the
# browser will execute.
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md"}


def document_for_slot(slot) -> str | None:
    """The instruction filename this slot links, or None.

    A linked station is authoritative, including when it has no instruction:
    the station is the ansvarig's decision. Only slots without a station yet
    (imported by the old schedule-push since the last migration) fall back to
    duties.py, the way every slot did before.
    """
    from .duties import profile_for

    if slot.station_row is not None:
        return slot.station_row.document
    profile = profile_for(slot.duty_name)
    return profile.document if profile else None


@dataclass(frozen=True)
class DocumentFile:
    filename: str
    team_slug: str | None  # None: shared by the club
    title: str


def title_for(filename: str) -> str:
    """'Cafeteria_Instruktion_MIH.pdf' → 'Cafeteria Instruktion MIH'."""
    return Path(filename).stem.replace("_", " ")


def list_documents(root: Path, team_slugs: Iterable[str]) -> list[DocumentFile]:
    """The uploaded instructions: shared ones first, then each team's own."""
    found = []
    for slug in [None, *sorted(team_slugs)]:
        directory = root / slug if slug else root
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES:
                found.append(DocumentFile(path.name, slug, title_for(path.name)))
    return found


def rank_documents(
    venue_name: str, station_name: str, documents: list[DocumentFile]
) -> list[DocumentFile]:
    """Instructions whose names share words with the place, first."""
    words = [w for w in normalize_key(f"{venue_name} {station_name}").split() if len(w) >= 3]

    def score(doc: DocumentFile) -> int:
        title = normalize_key(doc.title)
        return sum(1 for word in words if word in title)

    return sorted(documents, key=lambda d: (-score(d), d.title.casefold()))


def linkable_documents() -> list[DocumentFile]:
    """What a station may link: the club's shared files only.

    Stations are club-wide, so a link to one team's private folder would be
    broken for every other team and expose its filename. A team's folder
    keeps its role as a same-name override when the file is served.
    Needs an app context.
    """
    from flask import current_app

    root = Path(current_app.extensions["duty_web_app_config"].documents_dir)
    return list_documents(root, [])


def link_unlinked_slots(session: Session) -> tuple[int, int]:
    """Link slots without a station to stations that already exist.

    By the duty's profile, else the slot's own venue and station text. Never
    creates a venue or station and never touches a document: a place the
    ansvariga removed or left out stays that way. Returns (linked, unlinked);
    a slot with no match is left for an ansvarig to place. Flushes, never commits.
    """
    from .duties import profile_for

    linked = unlinked = 0
    for slot in session.query(Slot).filter(Slot.station_id.is_(None)).all():
        profile = profile_for(slot.duty_name)
        venue_name, station_name = (
            (profile.venue, profile.station) if profile else (slot.venue, slot.station)
        )
        venue = find_venue(session, venue_name) if normalize_key(venue_name) else None
        station = (
            find_station(session, venue, station_name)
            if venue and normalize_key(station_name)
            else None
        )
        if station is None:
            unlinked += 1
            continue
        slot.station_id = station.id
        linked += 1
    session.flush()
    return linked, unlinked
