"""The tables the admin keeps: places, roles and API keys."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from duty_web.models import (
    ROLE_ANSVARIG,
    ApiKey,
    ClubAdmin,
    Player,
    Slot,
    Station,
    TeamRole,
    Venue,
)
from duty_web.names import normalize_key


def _venue(session, name):
    venue = Venue(name=name, name_key=normalize_key(name))
    session.add(venue)
    session.flush()
    return venue


def test_a_venue_has_its_stations(session_factory):
    session = session_factory()
    venue = _venue(session, "Wallenstam arena")
    session.add_all([
        Station(venue_id=venue.id, name="Café Arena B", name_key="cafe arena b",
                document="Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf"),
        Station(venue_id=venue.id, name="Café Arena A", name_key="cafe arena a"),
    ])
    session.commit()

    assert [s.name for s in venue.stations] == ["Café Arena A", "Café Arena B"]
    assert venue.stations[0].document is None


def test_two_venues_cannot_share_a_key(session_factory):
    session = session_factory()
    _venue(session, "Hindås")
    session.add(Venue(name="hindas", name_key="hindas"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_station_name_is_unique_per_venue_only(session_factory):
    session = session_factory()
    arena, hall = _venue(session, "Wallenstam arena"), _venue(session, "Mölnlycke idrottshall")
    session.add_all([
        Station(venue_id=arena.id, name="Café", name_key="cafe"),
        Station(venue_id=hall.id, name="Café", name_key="cafe"),
    ])
    session.flush()  # same name, other venue: fine
    session.add(Station(venue_id=arena.id, name="cafe", name_key="cafe"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_slot_links_its_station(session_factory, seeded):
    session = session_factory()
    venue = _venue(session, "Wallenstam arena")
    station = Station(venue_id=venue.id, name="Café Arena B", name_key="cafe arena b")
    session.add(station)
    session.flush()
    slot = session.get(Slot, seeded["slot"].id)
    assert slot.station_id is None
    slot.station_id = station.id
    session.commit()

    assert session.get(Slot, seeded["slot"].id).station_row.name == "Café Arena B"


def test_a_player_is_not_exempt_by_default(session_factory, seeded):
    session = session_factory()
    assert session.get(Player, seeded["player"].id).exempt is False


def test_a_role_is_given_once(session_factory, seeded):
    session = session_factory()
    role = dict(person_id=seeded["person"].id, team_id=seeded["team"].id, role=ROLE_ANSVARIG)
    session.add(TeamRole(**role))
    session.flush()
    session.add(TeamRole(**role))
    with pytest.raises(IntegrityError):
        session.flush()


def test_club_admins_and_keys_belong_to_people(session_factory, seeded):
    session = session_factory()
    now = datetime(2026, 10, 23, 12, 0)
    session.add(ClubAdmin(person_id=seeded["person"].id))
    session.add(ApiKey(
        person_id=seeded["person"].id, prefix="cs_abcd1234", key_hash="0" * 64,
        created_at=now, expires_at=now + timedelta(days=180),
    ))
    session.commit()

    key = session.query(ApiKey).one()
    assert key.person.email == "tova@exempel.se"
    assert key.last_used_at is None and key.revoked_at is None
    assert session.get(ClubAdmin, seeded["person"].id).person.name == "Alva Exempel"
