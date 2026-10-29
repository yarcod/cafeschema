"""slot.update and slot.delete: change or remove one shift's seats."""

import re
from datetime import date, time

import pytest

from duty_web.admin import slot_ops
from duty_web.admin.actions import REGISTRY
from duty_web.admin.actions.base import ValidationError
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.permissions import admin_context
from duty_web.admin.plan_tokens import PlanTokenError
from duty_web.clock import utcnow
from duty_web.models import Person, Player, Slot, SwapRequest, Team
from duty_web.places import find_or_create_station

from .conftest import bearer, login, make_family

SECRET = "test-secret"


def _run(app, session_factory, person, name, *, apply=True, **fields):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, person.id), "f14-bla")
    action = REGISTRY[name]
    with app.test_request_context():
        plan, token = dry_run(session, ctx, action, action.parse(fields, {}), secret_key=SECRET)
        if apply:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")
    return plan


def _undated(session_factory, team_id, count=2):
    session = session_factory()
    slots = [
        Slot(team_id=team_id, date=None, start_time=time(18), end_time=time(21), station="Café Arena B",
             venue="Wallenstam arena", duty_name="Cafépass")
        for _ in range(count)
    ]
    session.add_all(slots)
    session.commit()
    return [s.id for s in slots]


def _swap(session_factory, seeded, second_team, status="pending"):
    session = session_factory()
    swap = SwapRequest(proposer_slot_id=seeded["future_slot"].id,
                       target_slot_id=second_team["neighbour_slot"].id,
                       created_at=utcnow(), status=status)
    session.add(swap)
    session.commit()
    return swap.id


def _ida(session_factory, seeded):
    session = session_factory()
    player = make_family(session, session.get(Team, seeded["team"].id), "Ida Ny",
                         [("Iris Ny", "iris@exempel.se")])
    session.commit()
    return player.id


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(slot_ops, "send_swap_cancelled", lambda emails, **kw: sent.append(kw))
    return sent


@pytest.fixture
def moved(monkeypatch):
    sent = []
    monkeypatch.setattr(slot_ops, "send_shift_moved", lambda emails, **kw: sent.append((sorted(emails), kw)))
    return sent


def test_an_undated_shift_gets_its_date(app, session_factory, admins, seeded):
    ids = _undated(session_factory, seeded["team"].id)

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update", slot_ids=ids, day="2027-02-05")

    assert [c.detail for c in plan.changes] == ["datum satt: fre 5 feb"] * 2
    session = session_factory()
    assert {session.get(Slot, i).date for i in ids} == {date(2027, 2, 5)}


def test_time_and_station_change_together(app, session_factory, admins, seeded):
    session = session_factory()
    cafe = find_or_create_station(session, "Mölnlycke idrottshall", "Café")
    session.commit()
    seat = seeded["future_slot"].id

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update", slot_ids=[seat],
                start="17:30", end="20:30", station_id=cafe.id)

    assert plan.changes[0].detail == "tid 18:00–21:00 → 17:30–20:30; station Cafe → Café"
    slot = session_factory().get(Slot, seat)
    assert (slot.start_time, slot.station, slot.venue, slot.station_id) == (
        time(17, 30), "Café", "Mölnlycke idrottshall", cafe.id
    )


def test_a_new_player_cancels_the_seats_swap_and_mails_both_families(
    app, session_factory, admins, seeded, second_team, mails
):
    ida = _ida(session_factory, seeded)
    swap = _swap(session_factory, seeded, second_team)

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update",
                slot_ids=[seeded["future_slot"].id], player_id=ida)

    assert plan.changes[0].detail == "Tova Exempel → Ida Ny"
    assert any("ställs in" in w for w in plan.warnings)
    assert session_factory().get(SwapRequest, swap).status == "cancelled"
    assert len(mails) == 2


def test_a_note_alone_leaves_swaps_alone(app, session_factory, admins, seeded, second_team, mails):
    swap = _swap(session_factory, seeded, second_team)

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update",
                slot_ids=[seeded["future_slot"].id], note="Ta med nycklar")

    assert plan.changes[0].detail == "anteckning: ingen → 'Ta med nycklar'"
    assert session_factory().get(SwapRequest, swap).status == "pending" and mails == []


def test_a_note_changed_after_review_is_a_conflict(app, session_factory, admins, seeded):
    seat = seeded["future_slot"].id
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    action = REGISTRY["slot.update"]
    with app.test_request_context():
        _, token = dry_run(session, ctx, action, action.parse({"slot_ids": [seat], "note": "B"}, {}),
                           secret_key=SECRET)
        other = session_factory()
        other.get(Slot, seat).note = "C"
        other.commit()

        with pytest.raises(PlanTokenError) as caught:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert caught.value.status == 409
    assert session_factory().get(Slot, seat).note == "C"


def test_seats_from_two_shifts_are_refused(app, session_factory, admins, seeded):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "slot.update",
             slot_ids=[seeded["future_slot"].id, seeded["slot"].id], note="x")

    assert "slot_ids" in caught.value.errors


def test_moving_a_shift_onto_a_day_its_player_works_is_a_warning(app, session_factory, admins, seeded):
    future = seeded["future_slot"]
    session = session_factory()
    later = future.date.replace(day=17)   # the seeded future shift is always on a 16th
    seats = [
        Slot(team_id=seeded["team"].id, date=later, start_time=time(9), end_time=time(12),
             station="Café", venue="Mölnlycke idrottshall", duty_name="Bästkustcupen",
             player_id=player_id, child_name=name)
        for player_id, name in ((seeded["player"].id, "Tova Exempel"), (None, None))
    ]
    session.add_all(seats)
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update", apply=False,
                slot_ids=[s.id for s in seats], day=future.date.isoformat())

    assert f"Tova Exempel har redan ett pass {slot_ops.day_label(future.date)}." in plan.warnings


def test_removing_seats_from_two_shifts_is_refused(app, session_factory, admins, seeded):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "slot.delete",
             slot_ids=[seeded["future_slot"].id, seeded["slot"].id])

    assert "slot_ids" in caught.value.errors
    assert session_factory().get(Slot, seeded["slot"].id) is not None


def test_one_player_of_a_two_seat_shift_is_changed_from_its_own_link(
    client, admins, csrf_token, seeded, session_factory
):
    ida = _ida(session_factory, seeded)
    first, second = _undated(session_factory, seeded["team"].id)
    login(client, admins["ansvarig"])

    form = client.get(f"/admin/f14-bla/do/slot.update?slot_ids={first}").get_data(as_text=True)
    page = client.post("/admin/f14-bla/do/slot.update", data={
        "csrf_token": csrf_token(), "slot_ids": str(first), "player_id": str(ida),
    }).get_data(as_text=True)
    done = client.post("/admin/f14-bla/genomfor", data={
        "csrf_token": csrf_token(),
        "plan_token": re.search(r'name="plan_token" value="([^"]+)"', page).group(1),
    })

    assert 'name="player_id"' in form
    assert done.status_code == 302
    session = session_factory()
    assert (session.get(Slot, first).player_id, session.get(Slot, second).player_id) == (ida, None)


def test_a_seat_can_be_made_free(app, session_factory, admins, seeded):
    seat = seeded["future_slot"].id

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update", slot_ids=[seat], clear_player=True)

    assert plan.changes[0].detail == "Tova Exempel → Ledigt"
    slot = session_factory().get(Slot, seat)
    # An imported seat keeps the schedule's name, as Pass shows ("Tova Exempel i schemat").
    assert (slot.player_id, slot.child_name) == (None, "Tova Exempel")


def test_a_busy_player_is_a_warning(app, session_factory, admins, seeded, second_team):
    moa = second_team["neighbour_slot"].player_id

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update", apply=False,
                slot_ids=[seeded["future_slot"].id], player_id=moa)

    assert any("Moa Granne har redan ett pass" in w for w in plan.warnings)


def test_the_unchanged_form_has_nothing_to_change(app, session_factory, admins, seeded):
    future = seeded["future_slot"]

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update", apply=False,
                slot_ids=[future.id], day=future.date.isoformat(), start="18:00", end="21:00",
                player_id=seeded["player"].id)

    assert plan.changes == []


@pytest.mark.parametrize("fields, key", [
    ({"start": "21:00", "end": "18:00"}, "end"),
    ({"start": "kväll"}, "start"),
    ({"day": "imorgon"}, "day"),
    ({"station_id": 999}, "station_id"),
])
def test_bad_values_are_refused_next_to_their_field(app, session_factory, admins, seeded, fields, key):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "slot.update",
             slot_ids=[seeded["future_slot"].id], **fields)

    assert key in caught.value.errors


def test_a_player_is_changed_one_seat_at_a_time(app, session_factory, admins, seeded):
    ids = _undated(session_factory, seeded["team"].id)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "slot.update", slot_ids=ids,
             player_id=seeded["player"].id)

    assert "player_id" in caught.value.errors


def test_another_teams_player_is_refused(app, session_factory, admins, seeded, second_team):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "slot.update",
             slot_ids=[seeded["future_slot"].id], player_id=second_team["stranger"].id)

    assert "player_id" in caught.value.errors


def test_the_form_starts_from_the_shift_as_it_is(client, admins, seeded):
    login(client, admins["ansvarig"])
    future = seeded["future_slot"]

    page = client.get(f"/admin/f14-bla/do/slot.update?slot_ids={future.id}").get_data(as_text=True)

    assert f'value="{future.date.isoformat()}"' in page and 'value="18:00"' in page
    assert "Gör platsen ledig" in page


def test_removing_a_shift_takes_its_swaps_and_mails_both_families(
    app, session_factory, admins, seeded, second_team, mails
):
    _swap(session_factory, seeded, second_team)
    _swap(session_factory, seeded, second_team, status="accepted")
    seat = seeded["future_slot"].id

    plan = _run(app, session_factory, admins["ansvarig"], "slot.delete", slot_ids=[seat])

    assert [(c.op, c.detail) for c in plan.changes] == [("delete", "Tova Exempel")]
    session = session_factory()
    assert session.get(Slot, seat) is None and session.query(SwapRequest).count() == 0
    assert len(mails) == 2


def test_another_teams_shift_cannot_be_removed(app, session_factory, admins, second_team):
    with pytest.raises(ValidationError):
        _run(app, session_factory, admins["ansvarig"], "slot.delete",
             slot_ids=[second_team["stranger_slot"].id])


def test_both_doors_plan_the_same(client, admins, api_keys, csrf_token, seeded, session_factory):
    ids = _undated(session_factory, seeded["team"].id)
    api = client.post("/admin/api/slot.update", json={
        "team": "f14-bla", "slot_ids": ids, "day": "2027-02-05",
    }, headers=bearer(api_keys["ansvarig"])).get_json()
    removal = client.post("/admin/api/slot.delete", json={"team": "f14-bla", "slot_ids": ids},
                          headers=bearer(api_keys["ansvarig"])).get_json()
    login(client, admins["ansvarig"])

    page = client.post("/admin/f14-bla/do/slot.update", data={
        "csrf_token": csrf_token(), "slot_ids": [str(i) for i in ids], "day": "2027-02-05",
    }).get_data(as_text=True)
    gone = client.post("/admin/f14-bla/do/slot.delete", data={
        "csrf_token": csrf_token(), "slot_ids": [str(i) for i in ids],
    }).get_data(as_text=True)

    assert all(c["summary"] in page and c["detail"] in page for c in api["changes"])
    assert all(c["summary"] in gone for c in removal["changes"])


def test_a_moved_shift_mails_its_family(app, session_factory, admins, seeded, moved):
    seat = seeded["future_slot"]

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update", slot_ids=[seat.id],
                start="17:00", end="20:00")

    (emails, kw), = moved
    assert emails and all("@" in e for e in emails)
    assert kw["before"].endswith("18:00–21:00 Arena värdskap · Cafe")
    assert kw["after"].endswith("17:00–20:00 Arena värdskap · Cafe")
    assert kw["swap_cancelled"] is False
    assert any(w.startswith("Tova Exempel får ett mejl om att passet flyttas:") for w in plan.warnings)
    assert not any("@" in w for w in plan.warnings)


def test_a_moved_shift_with_a_swap_is_one_mail_per_family(
    app, session_factory, admins, seeded, second_team, mails, moved
):
    _swap(session_factory, seeded, second_team)

    _run(app, session_factory, admins["ansvarig"], "slot.update",
         slot_ids=[seeded["future_slot"].id], start="17:00", end="20:00")

    assert [kw["swap_cancelled"] for _, kw in moved] == [True]
    assert len(mails) == 1   # only the neighbour's family gets the cancel mail


def test_no_move_mail_for_a_note_a_first_date_or_a_family_without_mail(
    app, session_factory, admins, seeded, moved
):
    seat = seeded["future_slot"].id
    _run(app, session_factory, admins["ansvarig"], "slot.update", slot_ids=[seat], note="Nycklar")
    undated = _undated(session_factory, seeded["team"].id, count=1)
    session = session_factory()
    session.get(Slot, undated[0]).player_id = seeded["player"].id   # a held seat, first date
    session.commit()
    _run(app, session_factory, admins["ansvarig"], "slot.update", slot_ids=undated, day="2027-02-05")
    ida = _ida(session_factory, seeded)
    _run(app, session_factory, admins["ansvarig"], "slot.update", slot_ids=[seat], player_id=ida)
    session = session_factory()
    for parent in session.get(Slot, seat).player.parents:
        parent.email_notifications = False
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update", slot_ids=[seat], start="17:00")

    assert moved == []
    assert not any("får ett mejl om att passet flyttas" in w for w in plan.warnings)


def test_siblings_on_one_shift_get_one_move_mail(app, session_factory, admins, seeded, moved):
    session = session_factory()
    team = session.get(Team, seeded["team"].id)
    sibling = make_family(session, team, "Tilda Exempel", [])
    sibling.parents = list(session.get(Player, seeded["player"].id).parents)   # same parents
    first = session.get(Slot, seeded["future_slot"].id)
    twin = Slot(
        team_id=team.id, date=first.date, start_time=first.start_time, end_time=first.end_time,
        station=first.station, venue=first.venue, duty_name=first.duty_name,
        child_name=sibling.name, player_id=sibling.id,
    )
    session.add(twin)
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], "slot.update",
                slot_ids=[first.id, twin.id], start="17:00", end="20:00")

    assert len(moved) == 1
    assert sorted(w.split(" får ")[0] for w in plan.warnings if "får ett mejl om att passet flyttas" in w) == [
        "Tilda Exempel", "Tova Exempel"]
    assert not any("@" in w for w in plan.warnings)
