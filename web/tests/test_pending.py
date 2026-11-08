"""Staged Pass edits: what conflicts, and the schedule with them applied."""

from __future__ import annotations

from datetime import date, time

from duty_web.admin.pending import (
    SeatView,
    conflict_of,
    effective_seats,
    encode_slot,
    events_of,
    new_pass,
    pending_count,
    team_entries,
)
from duty_web.models import (
    PENDING_EDIT,
    PENDING_REMOVE,
    PendingPass,
    Player,
    Slot,
    Team,
)

from .conftest import make_family


def _two_seats(session, seeded):
    """Two seats of one Pass, held by Tova and by a second player."""
    team = seeded["team"]
    other = make_family(session, session.get(Team, team.id), "Ida Exempel", [("Ivar Exempel", "ivar@exempel.se")])
    a = Slot(team_id=team.id, date=date(2027, 3, 5), start_time=time(18), end_time=time(21),
             station="Cafe", duty_name="Cafépass", venue="Arena", player_id=seeded["player"].id)
    b = Slot(team_id=team.id, date=date(2027, 3, 5), start_time=time(18), end_time=time(21),
             station="Cafe", duty_name="Cafépass", venue="Arena", player_id=other.id)
    session.add_all([a, b])
    session.commit()
    return a, b, other


def _time_entry(team_id, a, b, start="17:30"):
    return PendingPass(
        team_id=team_id, kind=PENDING_EDIT,
        fields={"start_time": {"from": "18:00", "to": start}},
        seats=[{"slot_id": s.id, "from": s.player_id, "to": s.player_id, "drawn": False} for s in (a, b)],
    )


def _live(session, team_id):
    return {s.id: s for s in session.query(Slot).filter_by(team_id=team_id)}


def _players(session, team_id):
    return {p.id for p in session.query(Player).filter_by(team_id=team_id)}


def test_a_swap_on_another_seat_does_not_conflict_with_a_staged_time(session_factory, seeded):
    session = session_factory()
    a, b, other = _two_seats(session, seeded)
    entry = _time_entry(seeded["team"].id, a, b)
    a.player_id = other.id
    session.commit()

    live = _live(session, seeded["team"].id)

    assert conflict_of(entry, live, _players(session, seeded["team"].id)) is None


def test_a_staged_field_changed_elsewhere_conflicts(session_factory, seeded):
    session = session_factory()
    a, b, _ = _two_seats(session, seeded)
    entry = _time_entry(seeded["team"].id, a, b)
    a.start_time = time(19)
    session.commit()

    conflict = conflict_of(entry, _live(session, seeded["team"].id), _players(session, seeded["team"].id))

    assert conflict.fields == ("start_time",)
    assert conflict.missing is False


def test_a_staged_placement_the_live_seat_has_left_behind_conflicts(session_factory, seeded):
    session = session_factory()
    a, b, other = _two_seats(session, seeded)
    entry = _time_entry(seeded["team"].id, a, b)
    entry.seats = [{**entry.seats[0], "to": other.id}, entry.seats[1]]
    a.player_id = None
    session.commit()

    conflict = conflict_of(entry, _live(session, seeded["team"].id), _players(session, seeded["team"].id))

    assert conflict.seat_ids == (a.id,)


def test_a_deleted_seat_is_missing(session_factory, seeded):
    session = session_factory()
    a, b, _ = _two_seats(session, seeded)
    entry = _time_entry(seeded["team"].id, a, b)
    live = _live(session, seeded["team"].id)
    del live[b.id]

    assert conflict_of(entry, live, _players(session, seeded["team"].id)).missing is True


def test_a_staged_player_who_left_is_missing(session_factory, seeded):
    session = session_factory()
    a, b, other = _two_seats(session, seeded)
    entry = _time_entry(seeded["team"].id, a, b)
    entry.seats = [{**entry.seats[0], "to": other.id}, entry.seats[1]]

    assert conflict_of(entry, _live(session, seeded["team"].id), set()).missing is True


def test_a_seat_that_keeps_its_owner_is_not_missing_when_that_player_left(session_factory, seeded):
    session = session_factory()
    a, b, _ = _two_seats(session, seeded)
    entry = _time_entry(seeded["team"].id, a, b)

    assert conflict_of(entry, _live(session, seeded["team"].id), set()) is None


def test_effective_seats_overlay_edits_add_new_and_drop_removed(session_factory, seeded):
    session = session_factory()
    team_id = seeded["team"].id
    a, b, other = _two_seats(session, seeded)
    edit = _time_entry(team_id, a, b)
    edit.seats = [{**edit.seats[0], "to": None}, edit.seats[1]]
    removal = PendingPass(
        team_id=team_id, kind=PENDING_REMOVE, fields={},
        seats=[{"slot_id": seeded["slot"].id, "from": seeded["player"].id, "to": None, "drawn": False}],
    )
    new = new_pass(team_id, "Kioskpass", date(2027, 4, 2), time(9), time(12), None, 2)
    new.seats = [{**new.seats[0], "to": other.id, "drawn": True}, new.seats[1]]
    session.add_all([edit, removal, new])
    session.commit()
    before = session.query(Slot).count()

    seats = effective_seats(session, team_id)

    cafe = [s for s in seats if s.duty_name == "Cafépass"]
    assert {(s.start_time, s.player_id) for s in cafe} == {(time(17, 30), None), (time(17, 30), other.id)}
    assert all(s.entry_id == edit.id for s in cafe)
    kiosk = sorted((s for s in seats if s.duty_name == "Kioskpass"), key=lambda s: -s.ref)
    assert [(s.date, s.player_id, s.drawn, s.slot_id) for s in kiosk] == [
        (date(2027, 4, 2), other.id, True, None),
        (date(2027, 4, 2), None, False, None),
    ]
    assert [s.ref for s in kiosk] == [-(new.id * 1000 + 1), -(new.id * 1000 + 2)]
    assert seeded["slot"].id not in {s.slot_id for s in seats}
    assert seeded["future_slot"].id in {s.slot_id for s in seats}
    assert session.query(Slot).count() == before


def test_a_new_seats_station_gives_its_venue_and_name(session_factory, seeded):
    from duty_web.places import find_or_create_station

    session = session_factory()
    cafe = find_or_create_station(session, "Mölnlycke idrottshall", "Café")
    new = new_pass(seeded["team"].id, "Cafépass", None, time(9), time(12), cafe.id, 1)
    session.add(new)
    session.commit()

    seat = next(s for s in effective_seats(session, seeded["team"].id) if s.entry_id == new.id)

    assert (seat.venue, seat.station, seat.station_id, seat.date) == (
        "Mölnlycke idrottshall", "Café", cafe.id, None,
    )


def test_a_conflicted_entry_counts_as_live_when_skipped(session_factory, seeded):
    session = session_factory()
    a, b, _ = _two_seats(session, seeded)
    session.add(_time_entry(seeded["team"].id, a, b))
    a.start_time = time(19)
    session.commit()

    plain = effective_seats(session, seeded["team"].id)
    skipped = effective_seats(session, seeded["team"].id, skip_conflicted=True)

    assert {s.start_time for s in plain if s.duty_name == "Cafépass"} == {time(17, 30)}
    assert {(s.slot_id, s.start_time) for s in skipped if s.duty_name == "Cafépass"} == {
        (a.id, time(19)), (b.id, time(18)),
    }


def _view(duty, day):
    return SeatView(
        ref=1, slot_id=None, entry_id=None, date=day, start_time=time(18), end_time=time(21),
        duty_name=duty, venue="", station="", station_id=None, note=None, player_id=None, child_name=None,
    )


def test_events_split_on_a_gap_and_on_activity():
    fri, sat, sun = date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22)
    seats = [
        _view("Cafépass", fri), _view("Cafépass", sat), _view("Cafépass", sun),
        _view("Cafépass", date(2026, 11, 28)), _view("Cup", fri), _view("Cafépass", None),
    ]

    events = events_of(seats)

    assert {k: len(v) for k, v in events.items()} == {
        "Cafépass|2026-11-20": 3,
        "Cafépass|2026-11-28": 1,
        "Cup|2026-11-20": 1,
        "Cafépass|": 1,
    }


def test_other_teams_entries_are_never_loaded(session_factory, seeded, second_team):
    session = session_factory()
    session.add(PendingPass(team_id=second_team["team"].id, kind=PENDING_REMOVE, fields={},
                            seats=[{"slot_id": second_team["sibling_slot"].id, "from": None, "to": None, "drawn": False}]))
    session.commit()

    assert team_entries(session, seeded["team"].id) == []
    assert pending_count(session, seeded["team"].id) == 0
    assert pending_count(session, second_team["team"].id) == 1
    assert second_team["sibling_slot"].id not in {
        s.slot_id for s in effective_seats(session, seeded["team"].id)
    }


def test_deleting_a_staged_slot_is_not_refused_by_the_database(session_factory, seeded):
    session = session_factory()
    a, b, _ = _two_seats(session, seeded)
    session.add(_time_entry(seeded["team"].id, a, b))
    session.commit()

    session.delete(session.get(Slot, a.id))
    session.commit()

    assert session.get(Slot, a.id) is None


def test_encode_slot_uses_the_stored_field_forms(session_factory, seeded):
    session = session_factory()
    a, _, _ = _two_seats(session, seeded)

    assert encode_slot(a) == {
        "date": "2027-03-05", "start_time": "18:00", "end_time": "21:00",
        "station_id": None, "note": None,
    }
