"""The parent agenda's shape, shared by Hela laget, Pass and Granska."""

from datetime import date, time

from duty_web.agenda import group_days, group_shifts
from duty_web.models import Slot

from .conftest import login


def _slot(day, start, duty="Cafépass", name=None, station="Café", station_id=None, note=None):
    return Slot(
        date=day, start_time=time(start), end_time=time(start + 3), station=station,
        duty_name=duty, venue="Wallenstam arena", child_name=name,
        station_id=station_id, note=note,
    )


def test_the_seats_of_one_shift_are_one_card():
    a, b = _slot(date(2026, 12, 12), 9, name="A"), _slot(date(2026, 12, 12), 9, name="B")

    (shift,) = group_shifts([a, b])

    assert shift["slots"] == [a, b]
    assert (shift["start_time"], shift["duty_name"]) == (time(9), "Cafépass")


def test_two_stations_at_one_venue_are_two_cards():
    day = date(2026, 12, 12)
    cafe, kiosk = _slot(day, 9, station_id=1), _slot(day, 9, station="Kiosk", station_id=2)

    shifts = group_shifts([cafe, kiosk])

    assert [s["slots"] for s in shifts] == [[cafe], [kiosk]]
    assert [s["station"] for s in shifts] == ["Café", "Kiosk"]


def test_unlinked_seats_group_by_their_station_text():
    day = date(2026, 12, 12)

    shifts = group_shifts([_slot(day, 9), _slot(day, 9, station="Kiosk"), _slot(day, 9)])

    assert [len(s["slots"]) for s in shifts] == [2, 1]


def test_undated_shifts_on_different_weekdays_stay_apart():
    sat, sun = _slot(None, 9, note="lör"), _slot(None, 9, note="sön")

    assert len(group_shifts([sat, sun, _slot(None, 9, note="lör")])) == 2


def test_a_day_knows_where_each_shift_sits_in_it():
    day = date(2026, 12, 12)

    (only,) = group_days(group_shifts([_slot(day, 12), _slot(day, 9)]))

    first, last = only["shifts"]
    assert [first["start_time"], last["start_time"]] == [time(9), time(12)]
    assert (first["is_first"], first["is_last"], first["of"]) == (True, False, 2)
    assert (last["position"], last["is_last"]) == (2, True)


def test_undated_shifts_come_last():
    dated, undated = _slot(date(2026, 12, 12), 9), _slot(None, 9)

    assert [s["date"] for s in group_shifts([undated, dated])] == [date(2026, 12, 12), None]


def test_hela_laget_still_renders_the_agenda(client, seeded):
    login(client, seeded["person"])

    page = client.get("/team?year=2026&month=1").get_data(as_text=True)

    assert 'id="dag-16"' in page and "18:00–21:00" in page
