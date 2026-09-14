from datetime import date, datetime, time

import pytest
from sqlalchemy.exc import IntegrityError

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, SwapRequest, Team


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def test_slot_links_team_and_person():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    person = Person(name="Alva Exempel", email="tova@exempel.se")
    session.add_all([team, person])
    session.flush()

    slot = Slot(
        team_id=team.id,
        date=date(2026, 1, 16),
        start_time=time(18, 0),
        end_time=time(21, 0),
        station="Cafe",
        duty_name="Arena värdskap",
        venue="Wallenstam arena",
        note="Hämta nyckel helgen innan",
        person_id=person.id,
    )
    session.add(slot)
    session.commit()

    fetched = session.get(Slot, slot.id)
    assert fetched.team.name == "F14 Blå"
    assert fetched.person.email == "tova@exempel.se"


def test_slot_can_be_unfilled():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()

    slot = Slot(
        team_id=team.id,
        date=date(2026, 1, 16),
        start_time=time(18, 0),
        end_time=time(21, 0),
        station="Cafe",
        duty_name="Arena värdskap",
        venue="Wallenstam arena",
        person_id=None,
    )
    session.add(slot)
    session.commit()

    assert session.get(Slot, slot.id).person is None


def test_foreign_keys_are_enforced_on_sqlite(tmp_path):
    """SQLite ignores FK constraints per-connection unless explicitly
    enabled (I8) — make_engine must turn this on so a Slot referencing a
    nonexistent team_id fails loudly instead of writing a dangling row.
    Exercised against a real on-disk file, not ':memory:', since
    ':memory:' pins every session to one already-open connection and this
    guards the PRAGMA being applied on every new connection generally."""
    engine = make_engine(str(tmp_path / "duty.db"))
    init_db(engine)
    session = make_session_factory(engine)()

    slot = Slot(
        team_id=999_999,
        date=date(2026, 1, 16),
        start_time=time(18, 0),
        end_time=time(21, 0),
        station="Cafe",
        duty_name="Arena värdskap",
        venue="Wallenstam arena",
    )
    session.add(slot)

    with pytest.raises(IntegrityError):
        session.commit()


def test_swap_request_defaults_to_pending():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    a = Person(name="A", email="a@exempel.se")
    b = Person(name="B", email="b@exempel.se")
    session.add_all([team, a, b])
    session.flush()

    slot_a = Slot(
        team_id=team.id, date=date(2026, 1, 16), start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=a.id,
    )
    slot_b = Slot(
        team_id=team.id, date=date(2026, 1, 23), start_time=time(17, 30),
        end_time=time(20, 30), station="Entré", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=b.id,
    )
    session.add_all([slot_a, slot_b])
    session.flush()

    request = SwapRequest(
        proposer_slot_id=slot_a.id,
        target_slot_id=slot_b.id,
        created_at=datetime(2026, 1, 1, 12, 0),
    )
    session.add(request)
    session.commit()

    assert session.get(SwapRequest, request.id).status == "pending"
