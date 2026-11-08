"""pending.*: admin edits are staged as väntande and applied at once."""

from datetime import date, time

import pytest

from duty_web.admin import ratelimit
from duty_web.admin.actions import REGISTRY
from duty_web.admin.actions.base import ValidationError
from duty_web.admin.pending import pending_count, team_entries
from duty_web.models import PENDING_EDIT, PENDING_NEW, PENDING_REMOVE, PendingPass, Slot, Team

from .conftest import bearer, login, make_family, run_action


@pytest.fixture
def two_seats(session_factory, seeded):
    """One undated Pass with two seats: Tova on one, Ida on the other."""
    session = session_factory()
    team = session.get(Team, seeded["team"].id)
    ida = make_family(session, team, "Ida Exempel", [("Ivar Exempel", "ivar@exempel.se")])
    seats = [
        Slot(team_id=team.id, date=None, start_time=time(18), end_time=time(21),
             station="Cafe", duty_name="Cafépass", venue="Arena", child_name=name, player_id=pid)
        for name, pid in (("Tova Exempel", seeded["player"].id), ("Ida Exempel", ida.id))
    ]
    session.add_all(seats)
    session.commit()
    return {"a": seats[0].id, "b": seats[1].id, "ida": ida.id, "team": team.id}


def _edit(app, session_factory, admins, **fields):
    return run_action(app, session_factory, admins["ansvarig"], "pending.edit", **fields)


def _entries(session_factory, team_id):
    session = session_factory()
    return team_entries(session, team_id)


def test_an_edit_is_saved_as_vantande_and_the_pass_is_untouched(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], day="2026-11-20", start="17:30", end="19:30")

    session = session_factory()
    assert session.get(Slot, two_seats["a"]).date is None
    assert session.get(Slot, two_seats["a"]).start_time == time(18)
    (entry,) = team_entries(session, two_seats["team"])
    assert entry.kind == PENDING_EDIT
    assert entry.fields["date"] == {"from": None, "to": "2026-11-20"}
    assert entry.fields["start_time"] == {"from": "18:00", "to": "17:30"}
    # Every seat of the Pass is held, not just the one that was ticked.
    assert {s["slot_id"] for s in entry.seats} == {two_seats["a"], two_seats["b"]}


def test_changing_a_value_back_leaves_no_entry(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], start="17:30")
    assert pending_count(session_factory(), two_seats["team"]) == 1

    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], start="18:00")

    assert pending_count(session_factory(), two_seats["team"]) == 0


def test_a_blank_value_goes_back_to_live(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], start="17:30", note="Lördagar")
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], start="")

    (entry,) = _entries(session_factory, two_seats["team"])
    assert set(entry.fields) == {"note"}


def test_saving_one_cell_leaves_other_staged_values_alone(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], day="2026-11-20", start="17:30")
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], note="Lördagar")

    (entry,) = _entries(session_factory, two_seats["team"])
    assert entry.fields["date"]["to"] == "2026-11-20"
    assert entry.fields["start_time"]["to"] == "17:30"
    assert entry.fields["note"]["to"] == "Lördagar"


def test_an_empty_note_clears_the_note(app, session_factory, seeded, admins, two_seats):
    session = session_factory()
    session.get(Slot, two_seats["a"]).note = "Gammal"
    session.get(Slot, two_seats["b"]).note = "Gammal"
    session.commit()

    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], note="")

    (entry,) = _entries(session_factory, two_seats["team"])
    assert entry.fields["note"] == {"from": "Gammal", "to": None}


def test_a_new_edit_rebases_only_that_field(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], start="17:30", end="20:00")
    session = session_factory()
    for seat in (session.get(Slot, two_seats["a"]), session.get(Slot, two_seats["b"])):
        seat.start_time = time(17)
    session.commit()

    # Re-posting the staged end changes nothing, so the start stays conflicted.
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], end="20:00")
    (entry,) = _entries(session_factory, two_seats["team"])
    assert entry.fields["start_time"]["from"] == "18:00"

    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], start="17:15")
    (entry,) = _entries(session_factory, two_seats["team"])
    assert entry.fields["start_time"] == {"from": "17:00", "to": "17:15"}


def test_a_seat_is_staged_by_slot_id_and_ledigt_is_empty(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, entry_id=None, slot_ids=[two_seats["a"]],
          seats={str(two_seats["a"]): ""})

    (entry,) = _entries(session_factory, two_seats["team"])
    seat = next(s for s in entry.seats if s["slot_id"] == two_seats["a"])
    assert seat["to"] is None and seat["from"] == seeded["player"].id


def test_one_player_twice_on_a_pass_is_refused(app, session_factory, seeded, admins, two_seats):
    with pytest.raises(ValidationError) as caught:
        _edit(app, session_factory, admins, slot_ids=[two_seats["a"]],
              seats={str(two_seats["a"]): str(two_seats["ida"])})
    assert "Spelaren står redan på passet" in caught.value.errors.values()


def test_a_player_must_be_on_the_team(app, session_factory, seeded, admins, second_team, two_seats):
    stranger = second_team["stranger"].id
    with pytest.raises(ValidationError) as caught:
        _edit(app, session_factory, admins, slot_ids=[two_seats["a"]],
              seats={str(two_seats["a"]): str(stranger)})
    assert "Spelaren finns inte i laget" in caught.value.errors.values()


def test_the_end_must_be_after_the_start(app, session_factory, seeded, admins, two_seats):
    with pytest.raises(ValidationError) as caught:
        _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], end="17:00")
    assert "Sluttiden måste vara efter starttiden" in caught.value.errors.values()


def test_seats_from_two_passes_are_refused(app, session_factory, seeded, admins, two_seats):
    session = session_factory()
    other = Slot(team_id=two_seats["team"], date=None, start_time=time(9), end_time=time(10),
                 station="Kiosk", duty_name="Kioskpass", venue="Arena")
    session.add(other)
    session.commit()
    with pytest.raises(ValidationError) as caught:
        _edit(app, session_factory, admins, slot_ids=[two_seats["a"], other.id], note="x")
    assert caught.value.errors == {"slot_ids": "Välj platser från ett och samma pass"}


def test_nothing_chosen_asks_for_a_pass(app, session_factory, seeded, admins, two_seats):
    with pytest.raises(ValidationError) as caught:
        _edit(app, session_factory, admins, note="x")
    assert caught.value.errors == {"slot_ids": "Välj ett pass"}


def test_another_teams_slot_is_unknown(app, session_factory, seeded, admins, second_team, two_seats):
    with pytest.raises(ValidationError):
        _edit(app, session_factory, admins, slot_ids=[second_team["sibling_slot"].id], note="x")
    assert pending_count(session_factory(), second_team["team"].id) == 0


def test_an_unknown_station_is_refused(app, session_factory, seeded, admins, two_seats):
    with pytest.raises(ValidationError) as caught:
        _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], station_id="9999")
    assert "station_id" in caught.value.errors


def test_remove_marks_the_whole_pass_and_replaces_an_edit(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], note="x")

    run_action(app, session_factory, admins["ansvarig"], "pending.remove", slot_ids=[two_seats["a"]])

    (entry,) = _entries(session_factory, two_seats["team"])
    assert entry.kind == PENDING_REMOVE and entry.fields == {}
    assert {s["slot_id"] for s in entry.seats} == {two_seats["a"], two_seats["b"]}
    assert session_factory().get(Slot, two_seats["a"]) is not None


def test_a_pass_marked_for_removal_cannot_be_edited(app, session_factory, seeded, admins, two_seats):
    run_action(app, session_factory, admins["ansvarig"], "pending.remove", slot_ids=[two_seats["a"]])
    with pytest.raises(ValidationError) as caught:
        _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], note="x")
    assert "Passet är markerat för borttagning" in caught.value.errors.values()


def test_remove_of_a_new_pass_deletes_the_entry(app, session_factory, seeded, admins, two_seats):
    session = session_factory()
    from duty_web.admin.pending import new_pass

    entry = new_pass(two_seats["team"], "Cafépass", date(2027, 1, 1), time(9), time(10), None, 2)
    session.add(entry)
    session.commit()

    run_action(app, session_factory, admins["ansvarig"], "pending.remove", entry_id=entry.id)

    assert pending_count(session_factory(), two_seats["team"]) == 0


def test_a_new_pass_is_edited_by_entry_id_and_new_seat_keys(app, session_factory, seeded, admins, two_seats):
    from duty_web.admin.pending import new_pass

    session = session_factory()
    entry = new_pass(two_seats["team"], "Cafépass", date(2027, 1, 1), time(9), time(10), None, 2)
    session.add(entry)
    session.commit()

    _edit(app, session_factory, admins, entry_id=entry.id, start="08:30",
          seats={"new-1": str(two_seats["ida"])})

    row = session_factory().get(PendingPass, entry.id)
    assert row.kind == PENDING_NEW and row.fields["start_time"]["to"] == "08:30"
    assert row.seats[1]["to"] == two_seats["ida"]


def test_undo_and_discard_remove_entries(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], note="x")
    (entry,) = _entries(session_factory, two_seats["team"])
    run_action(app, session_factory, admins["ansvarig"], "pending.undo", entry_id=entry.id)
    assert pending_count(session_factory(), two_seats["team"]) == 0

    with pytest.raises(ValidationError) as caught:
        run_action(app, session_factory, admins["ansvarig"], "pending.discard", confirm="1")
    assert caught.value.errors == {"batch": "Inget väntande att slänga"}

    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], note="x")
    run_action(app, session_factory, admins["ansvarig"], "pending.remove", slot_ids=[two_seats["b"]])
    run_action(app, session_factory, admins["ansvarig"], "pending.discard", confirm="1")
    assert pending_count(session_factory(), two_seats["team"]) == 0


def test_discard_needs_confirmation(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], note="x")
    with pytest.raises(ValidationError):
        run_action(app, session_factory, admins["ansvarig"], "pending.discard", confirm="false")
    assert pending_count(session_factory(), two_seats["team"]) == 1


def test_keep_takes_live_values_as_the_new_starting_point(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], start="17:30", note="x")
    session = session_factory()
    for sid in (two_seats["a"], two_seats["b"]):
        session.get(Slot, sid).start_time = time(17, 30)
    session.commit()
    (entry,) = _entries(session_factory, two_seats["team"])

    run_action(app, session_factory, admins["ansvarig"], "pending.keep", entry_id=entry.id)

    (entry,) = _entries(session_factory, two_seats["team"])
    # The start now equals live, so only the note is left.
    assert set(entry.fields) == {"note"}


def test_keep_refuses_a_missing_seat(app, session_factory, seeded, admins, two_seats):
    _edit(app, session_factory, admins, slot_ids=[two_seats["a"]], note="x")
    session = session_factory()
    session.delete(session.get(Slot, two_seats["b"]))
    session.commit()
    (entry,) = _entries(session_factory, two_seats["team"])

    with pytest.raises(ValidationError) as caught:
        run_action(app, session_factory, admins["ansvarig"], "pending.keep", entry_id=entry.id)
    assert caught.value.errors == {"entry_id": "Passet finns inte längre"}


def test_html_door_applies_at_once_and_answers_autosave(client, admins, csrf_token, two_seats, session_factory):
    login(client, admins["ansvarig"])
    r = client.post("/admin/f14-bla/do/pending.edit", headers={"X-Autosave": "1"},
                    data={"csrf_token": csrf_token(), "slot_ids": [two_seats["a"]], "note": "Lördagar"})

    assert r.status_code == 200 and r.get_json()["pending"] == 1
    assert r.get_json()["row"] == {"pending": True, "changed": ["note"]}
    assert pending_count(session_factory(), two_seats["team"]) == 1


def test_html_door_reads_seat_fields(client, admins, csrf_token, two_seats):
    login(client, admins["ansvarig"])
    r = client.post("/admin/f14-bla/do/pending.edit", headers={"X-Autosave": "1"},
                    data={"csrf_token": csrf_token(), "slot_ids": [two_seats["a"]],
                          f"seat-{two_seats['a']}": ""})

    assert r.get_json()["row"] == {"pending": True, "changed": [f"seat-{two_seats['a']}"]}


def test_html_door_without_autosave_redirects_to_the_pass_tab(client, admins, csrf_token, two_seats):
    login(client, admins["ansvarig"])
    r = client.post("/admin/f14-bla/do/pending.edit",
                    data={"csrf_token": csrf_token(), "slot_ids": [two_seats["a"]], "note": "x"})

    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/f14-bla/pass")


def test_html_door_answers_autosave_errors(client, admins, csrf_token, two_seats):
    login(client, admins["ansvarig"])
    r = client.post("/admin/f14-bla/do/pending.edit", headers={"X-Autosave": "1"},
                    data={"csrf_token": csrf_token(), "slot_ids": [two_seats["a"]], "end": "17:00"})

    assert r.status_code == 400 and r.get_json()["errors"]


def test_api_door_keeps_dry_run_then_token(client, api_keys, two_seats, session_factory):
    body = {"team": "f14-bla", "slot_ids": [two_seats["a"]], "note": "Lördagar"}
    first = client.post("/admin/api/pending.edit", json=body, headers=bearer(api_keys["ansvarig"]))
    assert first.status_code == 200 and first.get_json()["plan_token"]
    assert pending_count(session_factory(), two_seats["team"]) == 0

    done = client.post("/admin/api/pending.edit", headers=bearer(api_keys["ansvarig"]),
                       json={"team": "f14-bla", "dry_run": False,
                             "plan_token": first.get_json()["plan_token"]})
    assert done.status_code == 200 and done.get_json()["applied"] is True
    assert pending_count(session_factory(), two_seats["team"]) == 1


def test_autosaving_many_cells_does_not_hit_the_form_limit(client, admins, csrf_token, two_seats):
    login(client, admins["ansvarig"])
    for i in range(100):
        r = client.post("/admin/f14-bla/do/pending.edit", headers={"X-Autosave": "1"},
                        data={"csrf_token": csrf_token(), "slot_ids": [two_seats["a"]], "note": f"n{i}"})
        assert r.status_code == 200


def test_other_actions_still_use_the_form_limit(client, admins, csrf_token, monkeypatch):
    monkeypatch.setattr(ratelimit, "admin_limiter", ratelimit.RateLimiter(0, 60))
    login(client, admins["ansvarig"])
    r = client.post("/admin/f14-bla/do/venue.create", data={"csrf_token": csrf_token(), "name": "X"})
    assert r.status_code == 429
