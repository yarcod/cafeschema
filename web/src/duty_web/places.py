"""Venues and their stations: where a shift is held, and its instruction.

Names are compared by normalize_key only, so "cafe", "Cafe" and "Café" can
never become three stations with three different instruction links
(spec D4). The display name is whatever was entered first.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import Station, Venue
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
