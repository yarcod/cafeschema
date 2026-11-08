"""pending.publish and the one Granska page for a team's whole batch."""

import re
from datetime import date, time
from pathlib import Path

import pytest

from duty_web.admin import slot_ops
from duty_web.admin.actions import REGISTRY, pending_actions
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.pending import new_pass
from duty_web.admin.permissions import admin_context
from duty_web.admin.plan_tokens import PlanTokenError
from duty_web.clock import utcnow
from duty_web.models import (
    ORIGIN_ADMIN,
    ORIGIN_IMPORT,
    PendingPass,
    Person,
    Player,
    Slot,
    SwapRequest,
    Team,
)
from duty_web.places import find_or_create_station

from .conftest import bearer, login, make_family, run_action

MOVED_TO = "2031-03-05"


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(slot_ops, "send_shift_moved", lambda emails, **kw: sent.append(("moved", emails, kw)))
    monkeypatch.setattr(slot_ops, "send_swap_cancelled", lambda emails, **kw: sent.append(("cancelled", emails, kw)))
    return sent


@pytest.fixture
def ida(session_factory, seeded):
    session = session_factory()
    player = make_family(session, session.get(Team, seeded["team"].id), "Ida Ny", [("Iris Ny", "iris@exempel.se")])
    session.commit()
    return player.id


@pytest.fixture
def station(session_factory):
    session = session_factory()
    row = find_or_create_station(session, "Arena B", "Café nedre")
    session.commit()
    return row.id


def _seat(session_factory, seeded, *, day, start=9, player_id=None, child_name=None, origin=ORIGIN_ADMIN, note=None):
    session = session_factory()
    slot = Slot(
        team_id=seeded["team"].id, date=day, start_time=time(start), end_time=time(start + 2),
        station="Kiosk", duty_name="Kioskpass", venue="Arena", player_id=player_id,
        child_name=child_name, origin=origin, note=note,
    )
    session.add(slot)
    session.commit()
    return slot.id


def _stage(app, session_factory, admins, **fields):
    return run_action(app, session_factory, admins["ansvarig"], "pending.edit", **fields)


def _publish(app, session_factory, admins, **fields):
    return run_action(app, session_factory, admins["ansvarig"], "pending.publish", **fields)


def _remaining(session_factory):
    return [row.id for row in session_factory().query(PendingPass).order_by(PendingPass.id)]


def _entry_of(session_factory, slot_id):
    rows = session_factory().query(PendingPass).order_by(PendingPass.id).all()
    return next(row for row in rows if slot_id in [s["slot_id"] for s in row.seats])


def _token(page):
    return re.search(r'name="plan_token" value="([^"]+)"', page).group(1)


def test_publish_applies_the_whole_batch_in_one_go(app, session_factory, seeded, admins, ida, station, mails):
    soon = date(2031, 3, 1)
    a = _seat(session_factory, seeded, day=None, start=9)
    b = _seat(session_factory, seeded, day=soon, start=11, player_id=seeded["player"].id)
    c = _seat(session_factory, seeded, day=soon, start=14, player_id=seeded["player"].id)
    session = session_factory()
    session.add(new_pass(seeded["team"].id, "Cafépass", date(2031, 3, 2), time(8, 30), time(10, 30), station, 2))
    session.commit()

    _stage(app, session_factory, admins, slot_ids=[a], day="2031-03-07", seats={str(a): str(ida)})
    _stage(app, session_factory, admins, slot_ids=[b], start="11:30", end="13:30", note="Ta nycklar")
    _stage(app, session_factory, admins, slot_ids=[c], station_id=station, seats={str(c): ""})

    plan = _publish(app, session_factory, admins)

    session = session_factory()
    first = session.get(Slot, a)
    assert (first.date, first.player_id, first.child_name) == (date(2031, 3, 7), ida, "Ida Ny")
    second = session.get(Slot, b)
    assert (second.start_time, second.end_time, second.note) == (time(11, 30), time(13, 30), "Ta nycklar")
    third = session.get(Slot, c)
    assert (third.station_id, third.station, third.venue) == (station, "Café nedre", "Arena B")
    assert third.player_id is None and third.child_name is None
    created = session.query(Slot).filter(Slot.duty_name == "Cafépass").all()
    assert len(created) == 2
    assert {(s.date, s.start_time, s.origin, s.station_id, s.venue) for s in created} == {
        (date(2031, 3, 2), time(8, 30), ORIGIN_ADMIN, station, "Arena B")
    }
    assert _remaining(session_factory) == []
    # Sorted by the day each Pass ends up on.
    assert [c.op for c in plan.changes] == ["update", "update", "add", "update"]


def test_the_details_use_the_wording_of_a_shift_edit(app, session_factory, seeded, admins, ida, station):
    soon = date(2031, 3, 1)
    a = _seat(session_factory, seeded, day=None, start=9)
    b = _seat(session_factory, seeded, day=soon, start=11, player_id=seeded["player"].id, note="Gammal")
    c = _seat(session_factory, seeded, day=date(2031, 3, 8), start=14, player_id=seeded["player"].id)
    session = session_factory()
    session.add(new_pass(seeded["team"].id, "Cafépass", date(2031, 3, 20), time(8, 30), time(10, 30), station, 2))
    session.commit()
    _stage(app, session_factory, admins, slot_ids=[a], day="2031-03-07")
    _stage(app, session_factory, admins, slot_ids=[b], start="11:30", end="13:30", note="")
    _stage(app, session_factory, admins, slot_ids=[c], station_id=station, seats={str(c): ""})

    plan = _publish(app, session_factory, admins, apply=False)

    by_summary = {c.summary: c.detail for c in plan.changes}
    assert by_summary["fre 7 mar 09:00–11:00 Kioskpass · Kiosk"] == "datum satt: fre 7 mar"
    assert by_summary["lör 1 mar 11:30–13:30 Kioskpass · Kiosk"] == (
        "tid 11:00–13:00 → 11:30–13:30; anteckning: 'Gammal' → ingen"
    )
    assert by_summary["lör 8 mar 14:00–16:00 Kioskpass · Café nedre"] == (
        "station Kiosk → Café nedre; Tova Exempel → Ledigt"
    )
    assert by_summary["tor 20 mar 08:30–10:30 Cafépass · Café nedre"] == "2 platser"


def test_a_player_on_an_imported_seat_keeps_the_files_spelling(app, session_factory, seeded, admins, ida):
    a = _seat(session_factory, seeded, day=date(2031, 3, 1), child_name="Ida N", origin=ORIGIN_IMPORT)
    _stage(app, session_factory, admins, slot_ids=[a], seats={str(a): str(ida)})

    _publish(app, session_factory, admins)

    slot = session_factory().get(Slot, a)
    assert (slot.player_id, slot.child_name) == (ida, "Ida N")


def test_a_player_on_a_new_pass_is_placed(app, session_factory, seeded, admins, ida, station):
    session = session_factory()
    entry = new_pass(seeded["team"].id, "Cafépass", date(2031, 3, 2), time(8, 30), time(10, 30), station, 2)
    entry.seats = [{"slot_id": None, "from": None, "to": ida, "drawn": True},
                   {"slot_id": None, "from": None, "to": None, "drawn": False}]
    session.add(entry)
    session.commit()

    plan = _publish(app, session_factory, admins)

    created = session_factory().query(Slot).filter(Slot.duty_name == "Cafépass").order_by(Slot.id).all()
    assert sorted((s.player_id or 0, s.child_name or "") for s in created) == [(0, ""), (ida, "Ida Ny")]
    assert plan.changes[0].detail == "2 platser; Ida Ny"
    assert plan.review[0]["drawn"] is True


def test_a_removed_pass_is_deleted_with_its_swap(app, session_factory, seeded, admins, second_team, mails):
    session = session_factory()
    swap = SwapRequest(proposer_slot_id=seeded["future_slot"].id,
                       target_slot_id=second_team["neighbour_slot"].id, created_at=utcnow(), status="pending")
    session.add(swap)
    session.commit()
    run_action(app, session_factory, admins["ansvarig"], "pending.remove", slot_ids=[seeded["future_slot"].id])

    plan = _publish(app, session_factory, admins)

    session = session_factory()
    assert session.get(Slot, seeded["future_slot"].id) is None
    assert session.query(SwapRequest).count() == 0
    assert [c.op for c in plan.changes] == ["delete"]
    assert plan.changes[0].detail == "Tova Exempel"
    assert sorted(kind for kind, _, _ in mails) == ["cancelled", "cancelled"]


def test_a_moved_shift_with_a_player_is_mailed_once_and_the_switch_mutes_it(app, session_factory, seeded, admins, mails):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], day=MOVED_TO)

    plan = _publish(app, session_factory, admins, apply=False, notify=False)
    assert plan.notify is False and plan.move_mails == 1

    _publish(app, session_factory, admins, notify=False)
    assert mails == []

    _stage(app, session_factory, admins, slot_ids=[seeded["slot"].id], day=MOVED_TO)
    _publish(app, session_factory, admins)

    assert [kind for kind, _, _ in mails] == ["moved"]
    (_, emails, details) = mails[0]
    assert sorted(emails) == ["bjorn@exempel.se", "tova@exempel.se"]
    assert details["after"].startswith("ons 5 mar 18:00–21:00")


def test_a_seat_that_keeps_its_day_gets_no_mail_for_a_note(app, session_factory, seeded, admins, mails):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], note="Ta nycklar")

    _publish(app, session_factory, admins)

    assert mails == []


def test_placing_a_player_and_a_first_date_send_no_mail(app, session_factory, seeded, admins, ida, mails):
    empty = _seat(session_factory, seeded, day=date(2031, 3, 1))
    undated = _seat(session_factory, seeded, day=None, start=14, player_id=seeded["player"].id)
    _stage(app, session_factory, admins, slot_ids=[empty], seats={str(empty): str(ida)})
    _stage(app, session_factory, admins, slot_ids=[undated], day="2031-03-08")

    plan = _publish(app, session_factory, admins)

    assert len(plan.changes) == 2
    assert mails == []


@pytest.fixture
def swap(session_factory, seeded, second_team):
    session = session_factory()
    row = SwapRequest(proposer_slot_id=seeded["future_slot"].id,
                      target_slot_id=second_team["neighbour_slot"].id, created_at=utcnow(), status="pending")
    session.add(row)
    session.commit()
    return row.id


def test_a_cancelled_swap_is_mailed_even_when_muted(app, session_factory, seeded, admins, swap, mails):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], day=MOVED_TO)

    _publish(app, session_factory, admins, notify=False)

    assert session_factory().get(SwapRequest, swap).status == "cancelled"
    assert sorted(kind for kind, _, _ in mails) == ["cancelled", "cancelled"]


def test_a_moved_shift_with_a_swap_is_one_mail_for_that_family(app, session_factory, seeded, admins, swap, mails):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], day=MOVED_TO)

    _publish(app, session_factory, admins)

    assert sorted(kind for kind, _, _ in mails) == ["cancelled", "moved"]
    moved = next(kw for kind, _, kw in mails if kind == "moved")
    assert moved["swap_cancelled"] is True


def test_a_note_alone_leaves_a_swap_standing(app, session_factory, seeded, admins, swap, mails):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], note="Ta nycklar")

    _publish(app, session_factory, admins)

    assert session_factory().get(SwapRequest, swap).status == "pending"
    assert mails == []


def test_a_conflicted_pass_stays_vantande_and_the_rest_publishes(app, session_factory, seeded, admins, ida, mails):
    conflicted = _seat(session_factory, seeded, day=date(2031, 3, 1), player_id=seeded["player"].id)
    fine = _seat(session_factory, seeded, day=date(2031, 3, 2))
    _stage(app, session_factory, admins, slot_ids=[conflicted], start="10:00", seats={str(conflicted): str(ida)})
    _stage(app, session_factory, admins, slot_ids=[fine], start="10:00")
    held = _entry_of(session_factory, conflicted)
    session = session_factory()
    live = session.get(Slot, conflicted)
    live.start_time, live.player_id = time(8), None
    session.commit()

    plan = _publish(app, session_factory, admins)

    assert _remaining(session_factory) == [held.id]
    assert session_factory().get(Slot, conflicted).start_time == time(8)
    assert session_factory().get(Slot, fine).start_time == time(10)
    assert len(plan.changes) == 1
    assert "1 pass behöver ses över och följer inte med." in plan.warnings
    entry = next(e for e in plan.review if e["entry_id"] == held.id)
    assert entry["conflict"] == "Ändrat sedan det lades in: tid, spelare"
    assert entry["missing"] is False


def test_a_conflicted_seat_gets_no_mail_and_no_write(app, session_factory, seeded, admins, ida, mails):
    conflicted = seeded["future_slot"].id
    _stage(app, session_factory, admins, slot_ids=[conflicted], day=MOVED_TO)
    session = session_factory()
    session.get(Slot, conflicted).date = date(2031, 1, 1)
    session.commit()
    fine = _seat(session_factory, seeded, day=date(2031, 3, 2))
    _stage(app, session_factory, admins, slot_ids=[fine], start="10:00")

    _publish(app, session_factory, admins)

    assert mails == []
    assert session_factory().get(Slot, conflicted).date == date(2031, 1, 1)


def test_a_swap_on_an_untouched_seat_survives_a_staged_time(app, session_factory, seeded, admins, ida):
    day = date(2031, 3, 1)
    a = _seat(session_factory, seeded, day=day, player_id=seeded["player"].id)
    b = _seat(session_factory, seeded, day=day)
    _stage(app, session_factory, admins, slot_ids=[a], start="10:00")
    session = session_factory()
    session.get(Slot, b).player_id = ida
    session.commit()

    plan = _publish(app, session_factory, admins)

    assert session_factory().get(Slot, b).player_id == ida
    assert session_factory().get(Slot, a).player_id == seeded["player"].id
    assert session_factory().get(Slot, b).start_time == time(10)
    assert len(plan.changes) == 1


def test_an_excel_deleted_seat_offers_only_undo_and_the_rest_publishes(
    client, app, session_factory, seeded, admins, csrf_token, mails,
):
    gone = _seat(session_factory, seeded, day=date(2031, 3, 1))
    fine = _seat(session_factory, seeded, day=date(2031, 3, 2))
    _stage(app, session_factory, admins, slot_ids=[gone], start="10:00")
    _stage(app, session_factory, admins, slot_ids=[fine], start="10:00")
    held = _entry_of(session_factory, gone)
    session = session_factory()
    session.delete(session.get(Slot, gone))
    session.commit()

    login(client, admins["ansvarig"])
    page = client.get("/admin/f14-bla/granska").get_data(as_text=True)
    assert "Ångra" in page and "Behåll min ändring" not in page
    assert "passet finns inte längre" in page

    done = client.post("/admin/f14-bla/genomfor", data={"csrf_token": csrf_token(), "plan_token": _token(page)})

    assert done.status_code == 302
    assert _remaining(session_factory) == [held.id]
    assert session_factory().get(Slot, fine).start_time == time(10)


def test_a_conflicted_entry_offers_keep(client, app, session_factory, seeded, admins):
    a = _seat(session_factory, seeded, day=date(2031, 3, 1))
    _stage(app, session_factory, admins, slot_ids=[a], start="10:00")
    session = session_factory()
    session.get(Slot, a).start_time = time(8)
    session.commit()

    login(client, admins["ansvarig"])
    page = client.get("/admin/f14-bla/granska").get_data(as_text=True)

    assert "Behåll min ändring" in page
    assert "Ändrat sedan det lades in: tid" in page
    assert "Genomför" not in page


def test_a_staged_player_who_left_is_flagged_not_a_crash(app, session_factory, seeded, admins, second_team, ida):
    a = _seat(session_factory, seeded, day=date(2031, 3, 1))
    fine = _seat(session_factory, seeded, day=date(2031, 3, 2))
    _stage(app, session_factory, admins, slot_ids=[a], seats={str(a): str(ida)})
    _stage(app, session_factory, admins, slot_ids=[fine], start="10:00")
    held = _entry_of(session_factory, a)
    session = session_factory()
    session.get(Player, ida).team_id = second_team["team"].id
    session.commit()

    plan = _publish(app, session_factory, admins)

    entry = next(e for e in plan.review if e["entry_id"] == held.id)
    assert entry["missing"] is True and "spelaren" in entry["conflict"]
    assert _remaining(session_factory) == [held.id]
    assert session_factory().get(Slot, a).player_id is None
    assert session_factory().get(Slot, fine).start_time == time(10)


def test_double_booking_counts_other_vantande_edits(app, session_factory, seeded, admins, ida):
    day = date(2031, 3, 1)
    a = _seat(session_factory, seeded, day=day, start=9)
    b = _seat(session_factory, seeded, day=day, start=14)
    c = _seat(session_factory, seeded, day=day, start=16)
    for slot_id in (a, b, c):
        _stage(app, session_factory, admins, slot_ids=[slot_id], seats={str(slot_id): str(ida)})

    plan = _publish(app, session_factory, admins, apply=False)

    assert "Ida Ny har redan ett pass" in " ".join(plan.warnings)
    assert sum("Ida Ny har redan ett pass" in w for w in plan.warnings) == 1


def test_double_booking_sees_a_live_seat_and_a_move_onto_its_day(app, session_factory, seeded, admins):
    tova = seeded["player"].id
    a = _seat(session_factory, seeded, day=date(2031, 3, 1), player_id=tova)
    _seat(session_factory, seeded, day=date(2031, 3, 2), start=14, player_id=tova)
    _stage(app, session_factory, admins, slot_ids=[a], day="2031-03-02")

    plan = _publish(app, session_factory, admins, apply=False)

    assert "Tova Exempel har redan ett pass sön 2 mar." in plan.warnings


def test_a_seat_that_only_changes_time_is_no_new_double_booking(app, session_factory, seeded, admins):
    tova = seeded["player"].id
    a = _seat(session_factory, seeded, day=date(2031, 3, 1), player_id=tova)
    _seat(session_factory, seeded, day=date(2031, 3, 1), start=14, player_id=tova)
    _stage(app, session_factory, admins, slot_ids=[a], start="10:00")

    plan = _publish(app, session_factory, admins, apply=False)

    assert not any("har redan ett pass" in w for w in plan.warnings)


def test_a_conflicted_entry_does_not_count_for_double_booking(app, session_factory, seeded, admins, ida):
    day = date(2031, 3, 1)
    a = _seat(session_factory, seeded, day=day, start=9)
    b = _seat(session_factory, seeded, day=day, start=14)
    for slot_id in (a, b):
        _stage(app, session_factory, admins, slot_ids=[slot_id], seats={str(slot_id): str(ida)})
    session = session_factory()
    session.get(Slot, b).player_id = seeded["player"].id
    session.commit()

    plan = _publish(app, session_factory, admins, apply=False)

    assert not any("har redan ett pass" in w for w in plan.warnings)


def test_an_exempt_player_is_warned_about(app, session_factory, seeded, admins, ida):
    session = session_factory()
    session.get(Player, ida).exempt = True
    session.commit()
    a = _seat(session_factory, seeded, day=date(2031, 3, 1))
    _stage(app, session_factory, admins, slot_ids=[a], seats={str(a): str(ida)})

    plan = _publish(app, session_factory, admins, apply=False)

    assert "Ida Ny är undantagen (ledarfamilj)." in plan.warnings


def test_a_new_pass_warns_about_an_overlap(app, session_factory, seeded, admins, station):
    session = session_factory()
    session.add(Slot(team_id=seeded["team"].id, date=date(2031, 3, 2), start_time=time(9), end_time=time(11),
                     station="Café nedre", venue="Arena B", duty_name="Kioskpass", station_id=station))
    session.add(new_pass(seeded["team"].id, "Cafépass", date(2031, 3, 2), time(10), time(12), station, 2))
    session.commit()

    plan = _publish(app, session_factory, admins, apply=False)

    assert plan.warnings == ["Laget har redan ett pass som överlappar: sön 2 mar 09:00–11:00 Kioskpass · Café nedre"]


def test_notes_sum_up_who_gains_seats(app, session_factory, seeded, admins, ida):
    a = _seat(session_factory, seeded, day=date(2031, 3, 1))
    _stage(app, session_factory, admins, slot_ids=[a], seats={str(a): str(ida)})

    plan = _publish(app, session_factory, admins, apply=False)

    assert "Ida Ny: 0 → 1 pass" in plan.notes
    assert any(n.startswith("Laget: i snitt ") and n.endswith(" pass per spelare, spridning 1.") for n in plan.notes)


def test_a_batch_without_new_seats_has_no_staffing_notes(app, session_factory, seeded, admins):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], day=MOVED_TO)

    plan = _publish(app, session_factory, admins, apply=False)

    assert plan.notes == []


def test_the_preview_covers_the_old_and_new_day(app, session_factory, seeded, admins):
    moved = seeded["future_slot"]
    undated = _seat(session_factory, seeded, day=None)
    _stage(app, session_factory, admins, slot_ids=[moved.id], day=MOVED_TO)
    _stage(app, session_factory, admins, slot_ids=[undated], note="Lördagar")

    plan = _publish(app, session_factory, admins, apply=False)

    assert plan.preview_dates == {moved.date, date(2031, 3, 5)}
    assert plan.preview_undated is True


def _review(session_factory, admins):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    action = REGISTRY["pending.publish"]
    plan, token = dry_run(session, ctx, action, action.Input(), secret_key="test-secret")
    return session, ctx, plan, token


def test_a_change_during_review_gives_a_stale_plan(app, session_factory, seeded, admins):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], note="Första")
    with app.test_request_context():
        session, ctx, _, token = _review(session_factory, admins)
        other = session_factory()
        other.get(Slot, seeded["future_slot"].id).note = "Ändrat av någon annan"
        other.commit()

        with pytest.raises(PlanTokenError) as err:
            apply_plan(session, ctx, token, secret_key="test-secret", via="webb")

    assert err.value.code == "stale_plan"


def test_a_new_entry_during_review_gives_a_stale_plan(app, session_factory, seeded, admins):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], note="Första")
    with app.test_request_context():
        session, ctx, _, token = _review(session_factory, admins)
        _stage(app, session_factory, admins, slot_ids=[seeded["slot"].id], note="Andra")

        with pytest.raises(PlanTokenError) as err:
            apply_plan(session, ctx, token, secret_key="test-secret", via="webb")

    assert err.value.code == "stale_plan"


def test_genomfor_twice_is_stale_and_mails_once(client, app, session_factory, seeded, admins, mails, csrf_token):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], day=MOVED_TO)
    login(client, admins["ansvarig"])
    token = _token(client.get("/admin/f14-bla/granska").get_data(as_text=True))
    csrf = csrf_token()

    first = client.post("/admin/f14-bla/genomfor", data={"csrf_token": csrf, "plan_token": token})
    second = client.post("/admin/f14-bla/genomfor", data={"csrf_token": csrf, "plan_token": token})

    assert first.status_code == 302 and "/pass" in first.location
    assert second.status_code == 409 and len(mails) == 1


def test_the_mail_switch_replans_the_batch(client, app, session_factory, seeded, admins, csrf_token, mails):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], day=MOVED_TO)
    login(client, admins["ansvarig"])
    token = _token(client.get("/admin/f14-bla/granska").get_data(as_text=True))

    muted = client.post("/admin/f14-bla/omplanera", data={
        "csrf_token": csrf_token(), "plan_token": token, "mail_switch": "1", "mute": "1",
    }).get_data(as_text=True)

    assert 'name="tyst" value="1"' in muted
    client.post("/admin/f14-bla/genomfor", data={"csrf_token": csrf_token(), "plan_token": _token(muted)})
    assert mails == []


def test_granska_keeps_mute_after_undo(client, app, session_factory, seeded, admins, csrf_token):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], day=MOVED_TO)
    _stage(app, session_factory, admins, slot_ids=[seeded["slot"].id], note="Kvar")
    entry = _entry_of(session_factory, seeded["future_slot"].id)
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/granska?tyst=1").get_data(as_text=True)
    assert 'name="tyst" value="1"' in page

    undone = client.post("/admin/f14-bla/do/pending.undo", data={
        "csrf_token": csrf_token(), "entry_id": entry.id, "next": "granska", "tyst": "1",
    })

    assert undone.status_code == 302 and undone.location.endswith("/admin/f14-bla/granska?tyst=1")
    assert len(_remaining(session_factory)) == 1


def test_a_loud_granska_stays_loud_after_keep(client, app, session_factory, seeded, admins, csrf_token):
    a = seeded["future_slot"].id
    _stage(app, session_factory, admins, slot_ids=[a], start="10:00")
    session = session_factory()
    session.get(Slot, a).start_time = time(8)
    session.commit()
    entry = _entry_of(session_factory, a)
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/granska").get_data(as_text=True)
    assert 'name="tyst"' not in page

    kept = client.post("/admin/f14-bla/do/pending.keep", data={
        "csrf_token": csrf_token(), "entry_id": entry.id, "next": "granska",
    })

    assert kept.status_code == 302 and kept.location.endswith("/admin/f14-bla/granska")


@pytest.mark.parametrize("target", ["https://example.com/", "//example.com", "pass", ""])
def test_only_the_literal_granska_redirects_there(client, app, session_factory, seeded, admins, csrf_token, target):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], day=MOVED_TO)
    entry = _entry_of(session_factory, seeded["future_slot"].id)
    login(client, admins["ansvarig"])

    undone = client.post("/admin/f14-bla/do/pending.undo", data={
        "csrf_token": csrf_token(), "entry_id": entry.id, "next": target,
    })

    assert undone.status_code == 302 and undone.location.endswith("/admin/f14-bla/pass")


def test_empty_granska_goes_back_to_pass(client, admins):
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/granska")

    assert page.status_code == 302 and page.location.endswith("/admin/f14-bla/pass")


def test_granska_is_404_for_another_teams_admin(client, app, session_factory, seeded, admins):
    login(client, admins["parent"])

    assert client.get("/admin/f14-bla/granska").status_code == 404


def test_granska_groups_by_day_with_undated_last(client, app, session_factory, seeded, admins):
    undated = _seat(session_factory, seeded, day=None)
    late = _seat(session_factory, seeded, day=date(2031, 3, 8))
    early = _seat(session_factory, seeded, day=date(2031, 3, 1))
    for slot_id in (undated, late, early):
        _stage(app, session_factory, admins, slot_ids=[slot_id], note="Ny")
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/granska").get_data(as_text=True)

    assert "Granska väntande ändringar" in page
    assert page.index("lör 1 mar") < page.index("lör 8 mar") < page.index("Datum ej satt")
    assert "Genomför 3 ändringar" in page
    assert "Släng alla väntande" in page and "Tillbaka och ändra" in page


def test_granska_has_names_and_never_addresses(client, app, session_factory, seeded, admins, ida):
    a = seeded["future_slot"].id
    _stage(app, session_factory, admins, slot_ids=[a], seats={str(a): str(ida)})
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/granska").get_data(as_text=True)

    assert "Tova Exempel → Ida Ny" in page
    assert "@exempel.se" not in page


def test_an_agent_can_publish_through_the_api(client, app, session_factory, seeded, admins, api_keys):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], note="Ny")
    headers = bearer(api_keys["ansvarig"])

    planned = client.post("/admin/api/pending.publish", json={"team": "f14-bla"}, headers=headers).get_json()
    applied = client.post("/admin/api/pending.publish", headers=headers, json={
        "team": "f14-bla", "dry_run": False, "plan_token": planned["plan_token"],
    })

    assert applied.status_code == 200 and applied.get_json()["applied"] is True
    assert session_factory().get(Slot, seeded["future_slot"].id).note == "Ny"


def test_an_empty_batch_is_refused(app, session_factory, seeded, admins):
    from duty_web.admin.actions.base import ValidationError

    with pytest.raises(ValidationError) as caught:
        _publish(app, session_factory, admins)

    assert caught.value.errors == {"batch": "Inget väntande att genomföra"}


def test_discovery_warns_agents_about_publishing(client, api_keys):
    body = client.get("/admin/api", headers=bearer(api_keys["club"])).get_json()

    assert "uttrycklig begäran" in body["actions"]["pending.publish"]["agent_note"]
    assert body["actions"]["pending.edit"]["agent_note"] == ""


def test_the_source_warns_agents():
    source = Path(pending_actions.__file__).read_text(encoding="utf-8")

    assert "AGENTS: READ THIS BEFORE CALLING pending.publish" in source
