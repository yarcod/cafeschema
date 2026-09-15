"""The one-off migration that renames duties and says where they are held.

It lives in scripts/ (it is pushed to the Fly machine), but it is duty_web
code, so it is tested from here where duty_web is importable.
"""

from __future__ import annotations

import importlib.util
from datetime import date, time
from pathlib import Path

import pytest

from duty_web.models import Slot

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "rename_duty_profiles.py"
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("rename_duty_profiles", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _slot(seeded, **overrides):
    fields = dict(
        team_id=seeded["team"].id, date=date(2026, 9, 18), start_time=time(18, 0),
        end_time=time(21, 0), station="", duty_name="Arena värdskap höst",
        venue="Wallenstam arena", player_id=seeded["player"].id,
    )
    fields.update(overrides)
    return Slot(**fields)


def test_both_arena_terms_become_cafepass(migration, seeded, session_factory):
    session = session_factory()
    session.add_all([
        _slot(seeded),
        _slot(seeded, duty_name="Arena värdskap vinter", date=None),
    ])
    session.commit()

    migration.apply_profiles(session)

    names = {slot.duty_name for slot in session.query(Slot).all()}
    assert names == {"Arena värdskap", "Cafépass"}  # the fixture's own slots stay


def test_the_cup_gets_its_hall_and_cafe(migration, seeded, session_factory):
    session = session_factory()
    session.add(_slot(seeded, duty_name="Bästkustcupen", venue="", station=""))
    session.commit()

    migration.apply_profiles(session)

    cup = session.query(Slot).filter(Slot.duty_name == "Bästkustcupen").one()
    assert cup.venue == "Mölnlycke idrottshall"
    assert cup.station == "Café"


def test_a_renamed_slot_is_also_placed(migration, seeded, session_factory):
    session = session_factory()
    session.add(_slot(seeded))
    session.commit()

    migration.apply_profiles(session)

    slot = session.query(Slot).filter(Slot.duty_name == "Cafépass").one()
    assert (slot.venue, slot.station) == ("Wallenstam arena", "Café Arena B, nedre plan")


def test_a_duty_with_no_profile_is_left_alone(migration, seeded, session_factory):
    session = session_factory()
    session.add(_slot(seeded, duty_name="Åby Julmarknad", venue="Åby", station="Lotteri"))
    session.commit()

    migration.apply_profiles(session)

    market = session.query(Slot).filter(Slot.duty_name == "Åby Julmarknad").one()
    assert (market.venue, market.station) == ("Åby", "Lotteri")


def test_running_it_twice_changes_nothing_the_second_time(
    migration, seeded, session_factory
):
    session = session_factory()
    session.add(_slot(seeded))
    session.commit()

    migration.apply_profiles(session)
    assert migration.apply_profiles(session) == (0, 0)


def test_ownership_is_untouched(migration, seeded, session_factory):
    """The whole reason this is an UPDATE and not a re-import."""
    session = session_factory()
    session.add(_slot(seeded))
    session.commit()
    owners_before = {(slot.id, slot.player_id) for slot in session.query(Slot).all()}

    migration.apply_profiles(session)

    assert {(slot.id, slot.player_id) for slot in session.query(Slot).all()} == (
        owners_before
    )
