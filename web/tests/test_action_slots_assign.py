"""slots.assign: fair staffing of empty seats (spec D7)."""

import re
from datetime import date, time, timedelta

import pytest

from duty_web.admin.actions import REGISTRY
from duty_web.admin.actions.base import ValidationError
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.permissions import admin_context
from duty_web.admin.plan_tokens import PlanTokenError
from duty_web.admin.slot_ops import shift_label
from duty_web.models import Person, Player, Slot, Team

from .conftest import bearer, login, make_family

SECRET = "test-secret"


@pytest.fixture
def day(seeded):
    # Two days after the seeded future shift: same month, so same season.
    return seeded["future_slot"].date + timedelta(days=2)


def _seats(session_factory, team_id, day, count=1, start=9, player_id=None):
    session = session_factory()
    slots = [
        Slot(team_id=team_id, date=day, start_time=time(start), end_time=time(start + 3),
             station="Café", venue="Mölnlycke idrottshall", duty_name="Bästkustcupen",
             player_id=player_id)
        for _ in range(count)
    ]
    session.add_all(slots)
    session.commit()
    return [slot.id for slot in slots]


def _ida(session_factory, seeded, exempt=False):
    session = session_factory()
    player = make_family(session, session.get(Team, seeded["team"].id), "Ida Ny",
                         [("Iris Ny", "iris@exempel.se")])
    player.exempt = exempt
    session.commit()
    return player.id


def _exempt(session_factory, *player_ids):
    session = session_factory()
    for player_id in player_ids:
        session.get(Player, player_id).exempt = True
    session.commit()


def _run(app, session_factory, person, *, apply=True, **fields):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, person.id), "f14-bla")
    action = REGISTRY["slots.assign"]
    with app.test_request_context():
        plan, token = dry_run(session, ctx, action, action.parse(fields, {}), secret_key=SECRET)
        if apply:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")
    return plan


def _token(body):
    return re.search(r'name="plan_token" value="([^"]+)"', body).group(1)


def _label(day, start=9):
    return shift_label(day, time(start), time(start + 3), "Bästkustcupen", "Café")


def test_the_player_with_fewest_shifts_gets_the_seat(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    (seat,) = _seats(session_factory, seeded["team"].id, day)

    plan = _run(app, session_factory, admins["ansvarig"], slot_ids=[seat], seed=5)

    assert [c.summary for c in plan.changes] == [f"Ida Ny → {_label(day)}"]
    assert "Ida Ny: 0 → 1 pass" in plan.notes
    assert any(note.startswith("Laget: i snitt") for note in plan.notes)
    slot = session_factory().get(Slot, seat)
    assert (slot.player_id, slot.child_name) == (ida, "Ida Ny")


def test_staffing_an_imported_seat_keeps_the_schedules_name(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    (seat,) = _seats(session_factory, seeded["team"].id, day)
    session = session_factory()
    session.get(Slot, seat).child_name = "Elin Exempel"   # the coach's spelling, not on the roster
    session.commit()

    _run(app, session_factory, admins["ansvarig"], slot_ids=[seat], seed=5)

    slot = session_factory().get(Slot, seat)
    assert (slot.player_id, slot.child_name) == (ida, "Elin Exempel")


def test_a_leaders_child_is_never_picked(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded, exempt=True)
    seats = _seats(session_factory, seeded["team"].id, day, count=2)

    _run(app, session_factory, admins["ansvarig"], slot_ids=seats, seed=1)

    session = session_factory()
    assert ida not in {session.get(Slot, s).player_id for s in seats}


def test_a_player_without_parents_is_never_picked(app, session_factory, admins, seeded, day):
    session = session_factory()
    orphan = Player(name="Utan Förälder", team_id=seeded["team"].id)
    session.add(orphan)
    session.commit()
    (seat,) = _seats(session_factory, seeded["team"].id, day)

    _run(app, session_factory, admins["ansvarig"], slot_ids=[seat], seed=1)

    assert session_factory().get(Slot, seat).player_id != orphan.id


def test_a_seat_nobody_can_take_stays_free_with_a_reason(
    app, session_factory, admins, seeded, second_team, day
):
    _ida(session_factory, seeded)
    _exempt(session_factory, seeded["player"].id, second_team["neighbour_slot"].player_id)
    seats = _seats(session_factory, seeded["team"].id, day, count=2)

    plan = _run(app, session_factory, admins["ansvarig"], slot_ids=seats, seed=1)

    assert len(plan.changes) == 1
    assert plan.warnings == [f"{_label(day)}: Ledigt, alla kvarvarande har redan pass den dagen."]


def test_a_locked_seat_is_kept_and_counted(app, session_factory, admins, seeded, second_team, day):
    _ida(session_factory, seeded)
    first = _seats(session_factory, seeded["team"].id, day, start=9)[0]
    later = _seats(session_factory, seeded["team"].id, day, start=12)[0]
    moa = second_team["neighbour_slot"].player_id

    plan = _run(app, session_factory, admins["ansvarig"], slot_ids=[first, later],
                locked={str(first): moa}, seed=2)

    assert (plan.changes[0].summary, plan.changes[0].detail) == (f"Moa Granne → {_label(day)}", "låst")
    assert plan.changes[1].summary == f"Ida Ny → {_label(day, 12)}"
    assert "Moa Granne: 1 → 2 pass" in plan.notes


def test_a_lock_on_a_day_the_player_already_works_is_refused(
    app, session_factory, admins, seeded, second_team
):
    busy_day = second_team["neighbour_slot"].date   # Moa works 13:00 that day
    (seat,) = _seats(session_factory, seeded["team"].id, busy_day)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], slot_ids=[seat],
             locked={str(seat): second_team["neighbour_slot"].player_id})

    assert "Moa Granne har redan ett pass" in caught.value.errors["locked"]


def test_two_locks_on_one_day_for_one_player_are_refused(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    morning = _seats(session_factory, seeded["team"].id, day, start=9)[0]
    noon = _seats(session_factory, seeded["team"].id, day, start=12)[0]

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], slot_ids=[morning, noon],
             locked={str(morning): ida, str(noon): ida})

    assert "Ida Ny har redan ett pass" in caught.value.errors["locked"]


def test_a_lock_onto_an_undated_shift_the_player_is_already_on_is_refused(
    app, session_factory, admins, seeded
):
    ida = _ida(session_factory, seeded)
    _seats(session_factory, seeded["team"].id, None, player_id=ida)
    (seat,) = _seats(session_factory, seeded["team"].id, None)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], slot_ids=[seat], locked={str(seat): ida})

    assert "Ida Ny står redan på" in caught.value.errors["locked"]


def test_seats_from_two_seasons_are_refused(app, session_factory, admins, seeded):
    july = _seats(session_factory, seeded["team"].id, date(2027, 7, 31))[0]
    august = _seats(session_factory, seeded["team"].id, date(2027, 8, 1))[0]

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], slot_ids=[july, august], seed=1)

    assert "slot_ids" in caught.value.errors


def test_undated_staffing_keeps_the_season_it_was_reviewed_in(app, session_factory, admins, seeded):
    (seat,) = _seats(session_factory, seeded["team"].id, None)
    action = REGISTRY["slots.assign"]

    inp = action.parse({"slot_ids": [seat], "seed": 1}, {})
    fixed = action.parse({"slot_ids": [seat], "seed": 1, "season": "2026-08-01"}, {})

    assert inp.season is not None and inp.season.endswith("-08-01")
    assert fixed.season == "2026-08-01"


def test_a_seat_filled_after_review_is_a_conflict(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    (seat,) = _seats(session_factory, seeded["team"].id, day)
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    action = REGISTRY["slots.assign"]
    with app.test_request_context():
        _, token = dry_run(session, ctx, action, action.parse({"slot_ids": [seat], "seed": 1}, {}),
                           secret_key=SECRET)
        other = session_factory()
        other.get(Slot, seat).player_id = ida
        other.commit()

        with pytest.raises(PlanTokenError) as caught:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert caught.value.status == 409


def test_filled_seats_in_the_selection_are_left_alone(app, session_factory, admins, seeded, day):
    _ida(session_factory, seeded)
    (seat,) = _seats(session_factory, seeded["team"].id, day)

    plan = _run(app, session_factory, admins["ansvarig"], apply=False,
                slot_ids=[seeded["future_slot"].id, seat], seed=1)

    assert "1 av de valda platserna är redan bemannade och lämnas orörda." in plan.notes
    assert len(plan.changes) == 1


@pytest.mark.parametrize("fields, key", [
    ({"slot_ids": []}, "slot_ids"),
    ({"slot_ids": [999]}, "slot_ids"),
])
def test_unknown_seats_are_refused(app, session_factory, admins, fields, key):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], **fields)

    assert key in caught.value.errors


def test_another_teams_seat_is_unknown(app, session_factory, admins, second_team):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], slot_ids=[second_team["stranger_slot"].id])

    assert "slot_ids" in caught.value.errors


def test_a_lock_to_someone_outside_the_team_is_refused(app, session_factory, admins, seeded, second_team, day):
    (seat,) = _seats(session_factory, seeded["team"].id, day)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], slot_ids=[seat],
             locked={str(seat): second_team["stranger"].id})

    assert "locked" in caught.value.errors


def test_an_agent_without_a_seed_still_applies_what_it_reviewed(
    client, admins, api_keys, seeded, session_factory, day
):
    _ida(session_factory, seeded)
    seats = _seats(session_factory, seeded["team"].id, day, count=2)
    headers = bearer(api_keys["ansvarig"])

    dry = client.post("/admin/api/slots.assign", json={"team": "f14-bla", "slot_ids": seats},
                      headers=headers).get_json()
    applied = client.post("/admin/api/slots.assign", json={
        "team": "f14-bla", "dry_run": False, "plan_token": dry["plan_token"],
    }, headers=headers)

    assert applied.status_code == 200 and applied.get_json()["changes"] == dry["changes"]


def test_the_form_lists_the_picked_seats(client, admins, seeded, session_factory, day):
    (seat,) = _seats(session_factory, seeded["team"].id, day)
    login(client, admins["ansvarig"])

    page = client.get(f"/admin/f14-bla/do/slots.assign?slot_ids={seat}").get_data(as_text=True)

    assert _label(day) in page and 'name="seed"' in page and f'name="lock-{seat}"' in page


def test_both_doors_plan_the_same_and_granska_can_reshuffle(
    client, admins, api_keys, csrf_token, seeded, session_factory, day
):
    _ida(session_factory, seeded)
    seats = _seats(session_factory, seeded["team"].id, day, count=2)
    api = client.post("/admin/api/slots.assign", json={"team": "f14-bla", "slot_ids": seats, "seed": 5},
                      headers=bearer(api_keys["ansvarig"])).get_json()
    login(client, admins["ansvarig"])

    page = client.post("/admin/f14-bla/do/slots.assign", data={
        "csrf_token": csrf_token(), "slot_ids": [str(s) for s in seats], "seed": "5",
    }).get_data(as_text=True)

    for change in api["changes"]:
        assert change["summary"] in page
    assert "Slumpa om" in page and "Ida Ny: 0 → 1 pass" in page


def test_slumpa_om_then_genomfor_staffs_the_seats(
    client, admins, csrf_token, seeded, session_factory, day
):
    _ida(session_factory, seeded)
    seats = _seats(session_factory, seeded["team"].id, day, count=2)
    login(client, admins["ansvarig"])
    first = client.post("/admin/f14-bla/do/slots.assign", data={
        "csrf_token": csrf_token(), "slot_ids": [str(s) for s in seats], "seed": "5",
    }).get_data(as_text=True)

    again = client.post("/admin/f14-bla/omplanera", data={
        "csrf_token": csrf_token(), "plan_token": _token(first), "reshuffle": "1",
    })
    done = client.post("/admin/f14-bla/genomfor", data={
        "csrf_token": csrf_token(), "plan_token": _token(again.get_data(as_text=True)),
    })

    assert again.status_code == 200 and _token(again.get_data(as_text=True)) != _token(first)
    assert done.status_code == 302 and "/admin/f14-bla/pass" in done.headers["Location"]
    session = session_factory()
    assert all(session.get(Slot, s).player_id is not None for s in seats)
