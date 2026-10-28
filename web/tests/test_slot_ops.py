"""Shared helpers for the slot actions: clock times, labels, team scoping, swaps."""

import json
from datetime import date, time

import pytest

from duty_web.admin import slot_ops
from duty_web.admin.actions.base import Plan
from duty_web.admin.errors import ValidationError
from duty_web.admin.slot_ops import (
    day_label,
    parse_clock,
    pending_slot_ids,
    shift_label,
    team_slots,
)
from duty_web.clock import utcnow
from duty_web.models import Person, Slot, SwapRequest


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


def _run_writes(session, plan):
    for write in plan.writes:
        write(session)
    session.commit()


def _pending(session, seeded, second_team, status="pending"):
    swap = SwapRequest(
        proposer_slot_id=seeded["future_slot"].id,
        target_slot_id=second_team["neighbour_slot"].id,
        created_at=utcnow(), status=status,
    )
    session.add(swap)
    session.commit()
    return swap


def test_a_changed_seat_cancels_its_swap_and_mails_both_families(
    app, session_factory, seeded, second_team, monkeypatch
):
    sent = []
    monkeypatch.setattr(slot_ops, "send_swap_cancelled", lambda emails, **kw: sent.append((emails, kw)))
    session = session_factory()
    swap = _pending(session, seeded, second_team)
    plan = Plan()

    with app.test_request_context():
        slot_ops.plan_swap_cancellations(session, plan, changed=[seeded["future_slot"].id])
    _run_writes(session, plan)
    for callback in plan.after_commit:
        callback()

    (warning,) = plan.warnings
    assert "Tova Exempel" in warning and "Moa Granne" in warning and "ställs in" in warning
    assert session.get(SwapRequest, swap.id).status == "cancelled"
    assert len(sent) == 2
    assert sorted(len(emails) for emails, _ in sent) == [1, 2]   # Moa's one parent, Tova's two
    assert "@" not in json.dumps(plan.public(), ensure_ascii=False)


def test_a_removed_seat_takes_every_swap_row_with_it(app, session_factory, seeded, second_team):
    session = session_factory()
    _pending(session, seeded, second_team)
    _pending(session, seeded, second_team, status="accepted")
    plan = Plan()
    doomed = seeded["future_slot"].id

    with app.test_request_context():
        slot_ops.plan_swap_cancellations(session, plan, deleted=[doomed])
    plan.writes.append(lambda s: s.delete(s.get(Slot, doomed)))
    _run_writes(session, plan)

    assert session.query(SwapRequest).count() == 0
    assert session.get(Slot, doomed) is None
    assert len(plan.warnings) == 1   # only the pending one is news to anyone


def test_a_family_that_turned_mail_off_gets_none(app, session_factory, seeded, second_team, monkeypatch):
    sent = []
    monkeypatch.setattr(slot_ops, "send_swap_cancelled", lambda emails, **kw: sent.append(emails))
    session = session_factory()
    _pending(session, seeded, second_team)
    session.query(Person).filter_by(email="maja@exempel.se").one().email_notifications = False
    session.commit()
    plan = Plan()

    with app.test_request_context():
        slot_ops.plan_swap_cancellations(session, plan, changed=[seeded["future_slot"].id])
    for callback in plan.after_commit:
        callback()

    assert len(sent) == 1


def test_a_swap_answered_after_review_changes_the_plan(app, session_factory, seeded, second_team):
    session = session_factory()
    swap = _pending(session, seeded, second_team)
    reviewed, now = Plan(), Plan()

    with app.test_request_context():
        slot_ops.plan_swap_cancellations(session, reviewed, changed=[seeded["future_slot"].id])
        session.get(SwapRequest, swap.id).status = "accepted"
        session.commit()
        slot_ops.plan_swap_cancellations(session, now, changed=[seeded["future_slot"].id])

    assert reviewed.digest() != now.digest()


def test_nothing_to_cancel_adds_nothing(app, session_factory, seeded):
    plan = Plan()

    with app.test_request_context():
        slot_ops.plan_swap_cancellations(session_factory(), plan, changed=[seeded["future_slot"].id])

    assert plan.warnings == [] and plan.writes == [] and plan.after_commit == []
    assert plan.basis == []
