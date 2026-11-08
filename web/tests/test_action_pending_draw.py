"""pending.draw: Slumpa lediga fills an event's empty seats as väntande."""

from datetime import date, time, timedelta

import pytest

from duty_web.admin.actions import REGISTRY
from duty_web.admin.actions.base import ValidationError
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.pending import effective_seats, new_pass, team_entries
from duty_web.admin.permissions import admin_context
from duty_web.admin.slot_ops import shift_label
from duty_web.models import Person, Player, Slot, Team

from .conftest import login, make_family, run_action

DUTY = "Bästkustcupen"


@pytest.fixture
def day(seeded):
    # A weekday just after the seeded future shift: same month, so same
    # season, and never that shift's weekend, whatever the 16th falls on.
    day = seeded["future_slot"].date + timedelta(days=2)
    while day.weekday() >= 4:
        day += timedelta(days=1)
    return day


def _seats(session_factory, team_id, day, count=1, start=9, player_id=None, duty=DUTY):
    session = session_factory()
    slots = [
        Slot(team_id=team_id, date=day, start_time=time(start), end_time=time(start + 3),
             station="Café", venue="Mölnlycke idrottshall", duty_name=duty, player_id=player_id)
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


def _draw(app, session_factory, admins, *, apply=True, **fields):
    return run_action(app, session_factory, admins["ansvarig"], "pending.draw", apply=apply, **fields)


def _edit(app, session_factory, admins, **fields):
    return run_action(app, session_factory, admins["ansvarig"], "pending.edit", **fields)


def _key(day, duty=DUTY):
    return f"{duty}|{day.isoformat()}"


def _label(day, start=9):
    return shift_label(day, time(start), time(start + 3), DUTY, "Café")


def _names(session_factory, team_id, day=None):
    """Who is on each seat once everything väntande is published, by (day, start)."""
    session = session_factory()
    players = {p.id: p.name for p in session.query(Player).filter(Player.team_id == team_id)}
    return sorted(
        (str(s.date), s.start_time.hour, players.get(s.player_id))
        for s in effective_seats(session, team_id)
        if day is None or s.date == day
    )


def test_a_draw_fills_only_that_event(app, session_factory, admins, seeded, day):
    _ida(session_factory, seeded)
    later = day + timedelta(days=10)
    _seats(session_factory, seeded["team"].id, day)
    _seats(session_factory, seeded["team"].id, later)

    _draw(app, session_factory, admins, event=_key(day), seed=3)

    named = {d: name for d, _, name in _names(session_factory, seeded["team"].id)}
    assert named[str(day)] is not None and named[str(later)] is None


def test_drawn_names_are_vantande_and_slots_stay_empty(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    (seat,) = _seats(session_factory, seeded["team"].id, day)

    plan = _draw(app, session_factory, admins, event=_key(day), seed=5)

    assert [c.summary for c in plan.changes] == [f"Ida Ny → {_label(day)}"]
    assert "Ida Ny: 0 → 1 pass" in plan.notes
    assert any(note.startswith("Laget: i snitt") for note in plan.notes)
    assert session_factory().get(Slot, seat).player_id is None
    (entry,) = team_entries(session_factory(), seeded["team"].id)
    assert [(s["slot_id"], s["from"], s["to"], s["drawn"]) for s in entry.seats] == [(seat, None, ida, True)]


def test_the_fewest_shifts_goes_first_counting_vantande_placements(
    app, session_factory, admins, seeded, day
):
    ida = _ida(session_factory, seeded)
    _exempt(session_factory, _neighbour(session_factory))
    # Tova has the seeded shift (1); Ida is staged on two others (2).
    for offset in (3, 4):
        (seat,) = _seats(session_factory, seeded["team"].id, day + timedelta(days=offset))
        _edit(app, session_factory, admins, slot_ids=[seat], seats={str(seat): str(ida)})
    (target,) = _seats(session_factory, seeded["team"].id, day + timedelta(days=20))

    _draw(app, session_factory, admins, event=_key(day + timedelta(days=20)), seed=1)

    (entry,) = [e for e in team_entries(session_factory(), seeded["team"].id)
                if any(s["slot_id"] == target for s in e.seats)]
    assert entry.seats[0]["to"] == seeded["player"].id


def _neighbour(session_factory):
    return session_factory().query(Player).filter_by(name="Moa Granne").one().id


def test_one_shift_a_day_counts_other_vantande_edits(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    _exempt(session_factory, seeded["player"].id, _neighbour(session_factory))
    (morning,) = _seats(session_factory, seeded["team"].id, day, start=9)
    _seats(session_factory, seeded["team"].id, day, start=13)
    _edit(app, session_factory, admins, slot_ids=[morning], seats={str(morning): str(ida)})

    plan = _draw(app, session_factory, admins, event=_key(day), seed=1)

    assert plan.changes == []
    assert plan.warnings == [f"{_label(day, 13)}: Ledigt, alla kvarvarande har redan pass den dagen."]


def test_a_new_pass_can_be_drawn_before_it_exists_in_slots(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    _exempt(session_factory, seeded["player"].id, _neighbour(session_factory))
    session = session_factory()
    session.add(new_pass(seeded["team"].id, DUTY, day, time(9), time(12), None, seats=2))
    session.commit()

    plan = _draw(app, session_factory, admins, event=_key(day), seed=1)

    (entry,) = team_entries(session_factory(), seeded["team"].id)
    # Either seat will do: both are identical.
    assert sorted((s["to"] is None, s["to"] == ida, s["drawn"]) for s in entry.seats) == [
        (False, True, True), (True, False, False),
    ]
    assert len(plan.warnings) == 1 and "Ledigt" in plan.warnings[0]
    assert session_factory().query(Slot).filter_by(duty_name=DUTY).count() == 0


def test_same_seed_same_result_between_dry_run_and_apply(app, session_factory, admins, seeded, day):
    _ida(session_factory, seeded)
    _seats(session_factory, seeded["team"].id, day, count=2)
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    action = REGISTRY["pending.draw"]
    with app.test_request_context():
        inp = action.parse({"event": _key(day)}, {})
        plan, token = dry_run(session, ctx, action, inp, secret_key="test-secret")
        apply_plan(session, ctx, token, secret_key="test-secret", via="webb")

    assert inp.seed is not None
    players = {p.id: p.name for p in session_factory().query(Player)}
    (entry,) = team_entries(session_factory(), seeded["team"].id)
    assert sorted(f"{players[s['to']]} → {_label(day)}" for s in entry.seats) == sorted(
        c.summary for c in plan.changes
    )


def test_redraw_keeps_names_picked_by_hand(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    _exempt(session_factory, _neighbour(session_factory))
    first, second = (_seats(session_factory, seeded["team"].id, day, start=h)[0] for h in (9, 13))
    _edit(app, session_factory, admins, slot_ids=[first], seats={str(first): str(ida)})
    _draw(app, session_factory, admins, event=_key(day), seed=1)

    _draw(app, session_factory, admins, redraw=True, seed=2)

    (entry,) = [e for e in team_entries(session_factory(), seeded["team"].id)
                if any(s["slot_id"] == first for s in e.seats)]
    hand = next(s for s in entry.seats if s["slot_id"] == first)
    assert (hand["to"], hand["drawn"]) == (ida, False)
    other = [e for e in team_entries(session_factory(), seeded["team"].id)
             if any(s["slot_id"] == second for s in e.seats)]
    # Tova is the only one free on the day, so the redraw lands on her again.
    assert [s["to"] for e in other for s in e.seats if s["slot_id"] == second] == [seeded["player"].id]


def test_redraw_with_nobody_left_leaves_the_seat_ledigt_and_drops_the_entry(
    app, session_factory, admins, seeded, day
):
    ida = _ida(session_factory, seeded)
    _exempt(session_factory, seeded["player"].id, _neighbour(session_factory))
    (seat,) = _seats(session_factory, seeded["team"].id, day)
    _draw(app, session_factory, admins, event=_key(day), seed=1)
    _exempt(session_factory, ida)

    plan = _draw(app, session_factory, admins, redraw=True, seed=2)

    assert [c.summary for c in plan.changes] == [f"Ida Ny → Ledigt {_label(day)}"]
    assert team_entries(session_factory(), seeded["team"].id) == []
    assert session_factory().get(Slot, seat).player_id is None


def test_editing_a_drawn_seat_makes_it_hand_picked(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    (seat,) = _seats(session_factory, seeded["team"].id, day)
    _draw(app, session_factory, admins, event=_key(day), seed=1)
    (entry,) = team_entries(session_factory(), seeded["team"].id)
    drawn_to = entry.seats[0]["to"]
    other = ida if drawn_to != ida else seeded["player"].id

    _edit(app, session_factory, admins, slot_ids=[seat], seats={str(seat): str(other)})

    (entry,) = team_entries(session_factory(), seeded["team"].id)
    assert (entry.seats[0]["to"], entry.seats[0]["drawn"]) == (other, False)


def test_an_event_across_seasons_is_drawn_one_season_at_a_time(app, session_factory, admins, seeded):
    _ida(session_factory, seeded)
    july, august = date(2027, 7, 31), date(2027, 8, 1)
    for d in (july, august):
        _seats(session_factory, seeded["team"].id, d, duty="Cafépass")
    key = f"Cafépass|{july.isoformat()}"

    _draw(app, session_factory, admins, event=key, seed=1)
    after_first = {d: n for d, _, n in _names(session_factory, seeded["team"].id) if d in (str(july), str(august))}
    _draw(app, session_factory, admins, event=key, seed=1)
    after_second = {d: n for d, _, n in _names(session_factory, seeded["team"].id) if d in (str(july), str(august))}

    assert (after_first[str(july)] is not None, after_first[str(august)]) == (True, None)
    assert after_second[str(august)] is not None


def test_exempt_and_unreachable_players_are_not_drawn(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded, exempt=True)
    session = session_factory()
    orphan = Player(name="Utan Förälder", team_id=seeded["team"].id)
    session.add(orphan)
    session.commit()
    seats = _seats(session_factory, seeded["team"].id, day, count=3)

    _draw(app, session_factory, admins, event=_key(day), seed=1)

    drawn = {s["to"] for e in team_entries(session_factory(), seeded["team"].id) for s in e.seats}
    assert not drawn & {orphan.id, ida}
    assert len(seats) == 3


def test_a_removed_pass_and_a_conflicted_entry_are_not_drawn(app, session_factory, admins, seeded, day):
    _ida(session_factory, seeded)
    gone = _seats(session_factory, seeded["team"].id, day)[0]
    stale = _seats(session_factory, seeded["team"].id, day, start=13)[0]
    run_action(app, session_factory, admins["ansvarig"], "pending.remove", slot_ids=[gone])
    _edit(app, session_factory, admins, slot_ids=[stale], note="Ny")
    session = session_factory()
    session.get(Slot, stale).note = "Ändrat av Excel"
    session.commit()

    plan = _draw(app, session_factory, admins, event=_key(day), seed=1)

    assert plan.changes == [] and plan.warnings == []


def test_a_seat_nobody_can_take_stays_free_with_a_reason(app, session_factory, admins, seeded, day):
    _ida(session_factory, seeded)
    _exempt(session_factory, seeded["player"].id, _neighbour(session_factory))
    _seats(session_factory, seeded["team"].id, day, count=2)

    plan = _draw(app, session_factory, admins, event=_key(day), seed=1)

    assert len(plan.changes) == 1
    assert plan.warnings == [f"{_label(day)}: Ledigt, alla kvarvarande har redan pass den dagen."]


@pytest.mark.parametrize("fields, message", [
    ({}, "Välj ett evenemang"),
    ({"event": "Finns inte|2030-01-01"}, "Okänt evenemang"),
])
def test_an_event_is_required_and_must_exist(app, session_factory, admins, fields, message):
    with pytest.raises(ValidationError) as caught:
        _draw(app, session_factory, admins, **fields)

    assert caught.value.errors["event"] == message


def test_another_teams_event_is_unknown(app, session_factory, admins, seeded, second_team, day):
    _seats(session_factory, second_team["team"].id, day, duty="Annat lags cup")

    with pytest.raises(ValidationError) as caught:
        _draw(app, session_factory, admins, event=_key(day, "Annat lags cup"))

    assert caught.value.errors["event"] == "Okänt evenemang"


# The rules the draw keeps from before: a family has one shift a weekend and
# never two at once on different teams.

def _busy_on_second_team(session_factory, second_team, day, who="sibling"):
    """Put a player of the second team on 09–12 that day."""
    _seats(session_factory, second_team["team"].id, day, start=9, player_id=second_team[who].id)


def _saturday(day):
    return day + timedelta(days=5 - day.weekday())


def test_a_family_with_a_shift_that_weekend_is_not_drawn_again(app, session_factory, admins, seeded, day):
    ida = _ida(session_factory, seeded)
    _exempt(session_factory, seeded["player"].id, _neighbour(session_factory))
    saturday = _saturday(day)
    _seats(session_factory, seeded["team"].id, saturday, player_id=ida, duty="Annat pass")
    _seats(session_factory, seeded["team"].id, saturday + timedelta(days=1))

    sunday = saturday + timedelta(days=1)

    plan = _draw(app, session_factory, admins, event=_key(sunday), seed=1)

    assert plan.changes == []
    assert plan.warnings == [f"{_label(sunday)}: Ledigt, alla kvarvarande har redan pass den helgen."]


def test_a_parent_busy_on_another_team_is_not_picked_at_the_same_time(
    app, session_factory, admins, seeded, second_team, day
):
    _exempt(session_factory, _neighbour(session_factory))
    _busy_on_second_team(session_factory, second_team, day)
    _seats(session_factory, seeded["team"].id, day, start=10)

    plan = _draw(app, session_factory, admins, event=_key(day), seed=1)

    assert plan.changes == []
    assert plan.warnings == [
        f"{_label(day, 10)}: Ledigt, alla kvarvarande har redan pass samtidigt i ett annat lag."
    ]


def test_a_parent_can_take_a_shift_right_after_one_on_another_team(
    app, session_factory, admins, seeded, second_team, day
):
    _exempt(session_factory, _neighbour(session_factory))
    _busy_on_second_team(session_factory, second_team, day)
    _seats(session_factory, seeded["team"].id, day, start=12)

    plan = _draw(app, session_factory, admins, event=_key(day), seed=1)

    assert [c.summary for c in plan.changes] == [f"Tova Exempel → {_label(day, 12)}"]


def test_another_family_on_another_team_blocks_nobody(
    app, session_factory, admins, seeded, second_team, day
):
    _exempt(session_factory, _neighbour(session_factory))
    _busy_on_second_team(session_factory, second_team, day, who="stranger")
    _seats(session_factory, seeded["team"].id, day, start=10)

    plan = _draw(app, session_factory, admins, event=_key(day), seed=1)

    assert [c.summary for c in plan.changes] == [f"Tova Exempel → {_label(day, 10)}"]


def test_another_teams_vantande_edits_block_nobody(
    app, session_factory, admins, seeded, second_team, day
):
    # Only the other team's live seats count; what it has staged is its own.
    _exempt(session_factory, _neighbour(session_factory))
    (other,) = _seats(session_factory, second_team["team"].id, day, start=9)
    run_action(app, session_factory, admins["club"], "pending.edit", slug="p13", slot_ids=[other],
               seats={str(other): str(second_team["sibling"].id)})
    _seats(session_factory, seeded["team"].id, day, start=10)

    plan = _draw(app, session_factory, admins, event=_key(day), seed=1)

    assert [c.summary for c in plan.changes] == [f"Tova Exempel → {_label(day, 10)}"]


def test_the_button_shows_without_vantande_edits(client, admins, seeded, session_factory, day):
    _seats(session_factory, seeded["team"].id, day)
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "Slumpa lediga" in page and f'value="{_key(day)}"' in page


def test_the_button_hides_when_an_event_is_full(client, admins, seeded, session_factory, day):
    _seats(session_factory, seeded["team"].id, day, player_id=seeded["player"].id)
    login(client, admins["ansvarig"])

    assert "Slumpa lediga" not in client.get("/admin/f14-bla/pass").get_data(as_text=True)


def test_pressing_the_button_stages_names_and_returns_to_pass(
    client, admins, seeded, session_factory, csrf_token, day
):
    _ida(session_factory, seeded)
    _seats(session_factory, seeded["team"].id, day)
    login(client, admins["ansvarig"])

    response = client.post("/admin/f14-bla/do/pending.draw", data={
        "csrf_token": csrf_token(), "event": _key(day),
    })

    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/f14-bla/pass")
    assert len(team_entries(session_factory(), seeded["team"].id)) == 1


def test_granska_offers_slumpa_om_only_when_something_was_drawn(
    client, admins, seeded, session_factory, csrf_token, app, day
):
    ida = _ida(session_factory, seeded)
    first, _ = (_seats(session_factory, seeded["team"].id, day, start=h)[0] for h in (9, 13))
    login(client, admins["ansvarig"])
    _edit(app, session_factory, admins, slot_ids=[first], seats={str(first): str(ida)})

    by_hand = client.get("/admin/f14-bla/granska").get_data(as_text=True)
    _draw(app, session_factory, admins, event=_key(day), seed=1)
    drawn = client.get("/admin/f14-bla/granska").get_data(as_text=True)

    assert "Slumpa om" not in by_hand
    assert "Slumpa om" in drawn and 'name="redraw" value="1"' in drawn


def test_an_undated_event_is_drawn_in_this_season(app, session_factory, admins, seeded):
    ida = _ida(session_factory, seeded)
    _exempt(session_factory, seeded["player"].id, _neighbour(session_factory))
    (seat,) = _seats(session_factory, seeded["team"].id, None, duty="Cafépass")

    plan = _draw(app, session_factory, admins, event="Cafépass|", seed=1)

    assert [c.summary.startswith("Ida Ny → ") for c in plan.changes] == [True]
    (entry,) = team_entries(session_factory(), seeded["team"].id)
    assert [(s["slot_id"], s["to"]) for s in entry.seats] == [(seat, ida)]
