"""Shared helpers for the slot actions: clock times, labels, team scoping, swaps."""

from datetime import date, time

import pytest

from duty_web.admin.errors import ValidationError
from duty_web.admin.slot_ops import (
    day_label,
    parse_clock,
    pending_slot_ids,
    shift_label,
    team_slots,
)
from duty_web.clock import utcnow
from duty_web.models import Slot, SwapRequest


@pytest.mark.parametrize("raw, expected", [
    ("9", time(9)), ("9:00", time(9)), ("09.30", time(9, 30)), (" 18:05 ", time(18, 5)),
])
def test_clock_times_as_people_type_them(raw, expected):
    assert parse_clock(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "25:00", "9:60", "nio", "9-12", "9:5"])
def test_anything_else_is_not_a_clock_time(raw):
    assert parse_clock(raw) is None


def test_a_shift_is_named_the_way_granska_shows_it():
    assert day_label(date(2027, 3, 13)) == "lör 13 mar"
    assert day_label(None) == "datum ej satt"
    assert (
        shift_label(date(2027, 3, 13), time(9), time(12), "Bästkustcupen", "Café")
        == "lör 13 mar 09:00–12:00 Bästkustcupen · Café"
    )
    assert shift_label(None, time(18), time(21), "Cafépass", "") == "datum ej satt 18:00–21:00 Cafépass"


def test_a_seats_basis_changes_with_any_field_a_write_could_overwrite(session_factory, seeded):
    from duty_web.admin.slot_ops import slot_basis

    session = session_factory()
    slot = session.get(Slot, seeded["future_slot"].id)
    before = slot_basis(slot)
    slot.note = "ta med nycklar"

    assert slot_basis(slot) != before
    assert before[:2] == [slot.id, slot.player_id]


def test_another_teams_slot_is_just_unknown(session_factory, seeded, second_team):
    session = session_factory()
    ours = seeded["future_slot"].id
    theirs = second_team["stranger_slot"].id

    assert [s.id for s in team_slots(session, seeded["team"].id, [ours])] == [ours]
    with pytest.raises(ValidationError) as caught:
        team_slots(session, seeded["team"].id, [ours, theirs])
    assert caught.value.errors == {"slot_ids": f"Okända pass: {theirs}"}
    with pytest.raises(ValidationError):
        team_slots(session, seeded["team"].id, [])


def test_pending_swaps_are_found_on_either_side(session_factory, seeded, second_team):
    session = session_factory()
    mine, theirs = seeded["future_slot"].id, second_team["neighbour_slot"].id
    session.add(SwapRequest(proposer_slot_id=mine, target_slot_id=theirs, created_at=utcnow()))
    session.add(SwapRequest(
        proposer_slot_id=seeded["slot"].id, target_slot_id=theirs, created_at=utcnow(),
        status="declined",
    ))
    session.commit()

    assert pending_slot_ids(session, [theirs]) == {theirs}
    assert pending_slot_ids(session, [mine, seeded["slot"].id]) == {mine}
