"""Staged (väntande) edits live only in pending_passes, so until Genomför
nobody but the ansvarig sees them and nobody is mailed."""

import pytest

from duty_web.admin import slot_ops
from duty_web.clock import utcnow
from duty_web.models import PENDING_EDIT, PENDING_REMOVE, PendingPass, Slot, SwapRequest

from .conftest import login, make_family, run_action

API = {"X-Api-Key": "test-api-key"}


@pytest.fixture
def sent(monkeypatch):
    mails = []
    monkeypatch.setattr(slot_ops, "send_swap_cancelled", lambda emails, **kw: mails.append(("cancelled", kw)))
    monkeypatch.setattr(slot_ops, "send_shift_moved", lambda emails, **kw: mails.append(("moved", kw)))
    return mails


def _pending_swap(session_factory, seeded, second_team):
    session = session_factory()
    swap = SwapRequest(proposer_slot_id=seeded["future_slot"].id,
                       target_slot_id=second_team["neighbour_slot"].id,
                       created_at=utcnow(), status="pending")
    session.add(swap)
    session.commit()
    return swap.id


def _stage_everything(app, session_factory, admins, seeded, second_team):
    """A new date, a new player on the seat, and a second Pass marked for removal."""
    session = session_factory()
    ida = make_family(session, session.merge(seeded["team"]), "Ida Ny", [("Iris Ny", "iris@exempel.se")])
    session.commit()
    ansvarig = admins["ansvarig"]
    run_action(app, session_factory, ansvarig, "pending.edit",
               slot_ids=[seeded["future_slot"].id], day="2031-03-05", start="10:00", end="12:00",
               **{f"seat-{seeded['future_slot'].id}": str(ida.id)})
    run_action(app, session_factory, ansvarig, "pending.remove", slot_ids=[seeded["slot"].id])
    # Guard against silent no-ops: the "nothing changed" tests mean little otherwise.
    kinds = {(p.kind, tuple(x["slot_id"] for x in p.seats))
             for p in session_factory().query(PendingPass).all()}
    assert kinds == {(PENDING_EDIT, (seeded["future_slot"].id,)),
                     (PENDING_REMOVE, (seeded["slot"].id,))}


def _snapshot(client, seeded):
    day = seeded["future_slot"].date
    return {
        "api": client.get("/api/schedule", headers=API).get_json(),
        "team": client.get(f"/team?year={day.year}&month={day.month}").get_data(as_text=True),
        "mine": client.get("/").get_data(as_text=True),
    }


def test_parents_see_no_vantande_edit_or_pass(app, client, session_factory, admins, seeded, second_team):
    login(client, admins["parent"])
    before = _snapshot(client, seeded)
    assert before["api"] and "Arena värdskap" in before["mine"]

    _stage_everything(app, session_factory, admins, seeded, second_team)

    after = _snapshot(client, seeded)
    assert after == before
    assert "Ida Ny" not in after["team"] + after["mine"]
    assert "2031" not in after["team"] + after["mine"]


def test_staging_changes_no_slot(app, session_factory, admins, seeded, second_team):
    def rows():
        s = session_factory()
        return [(r.id, r.date, r.start_time, r.end_time, r.station, r.player_id, r.child_name, r.note)
                for r in s.query(Slot).order_by(Slot.id)]

    before = rows()
    _stage_everything(app, session_factory, admins, seeded, second_team)

    assert rows() == before


@pytest.fixture
def staged_with_swap(app, session_factory, admins, seeded, second_team, sent):
    """Everything staged, plus marking the swap's own Pass for removal.

    Removing a Pass that already has a staged edit replaces that edit with a
    remove entry, so the swap's seat ends up staged as a removal.
    """
    swap_id = _pending_swap(session_factory, seeded, second_team)
    _stage_everything(app, session_factory, admins, seeded, second_team)
    run_action(app, session_factory, admins["ansvarig"], "pending.remove", slot_ids=[seeded["future_slot"].id])
    kinds = {(p.kind, tuple(x["slot_id"] for x in p.seats))
             for p in session_factory().query(PendingPass).all()}
    assert kinds == {(PENDING_REMOVE, (seeded["future_slot"].id,)),
                     (PENDING_REMOVE, (seeded["slot"].id,))}
    return swap_id


def test_staging_sends_no_mail(staged_with_swap, sent):
    assert sent == []


def test_staging_leaves_pending_swaps_alone(staged_with_swap, session_factory):
    assert session_factory().get(SwapRequest, staged_with_swap).status == "pending"
