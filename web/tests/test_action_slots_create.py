"""slots.create: a weekend cup's days × shift rows become empty seats."""

from datetime import date, time

import pytest

from duty_web.admin.actions import REGISTRY
from duty_web.admin.actions.base import ValidationError
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.permissions import admin_context
from duty_web.models import Person, Slot
from duty_web.places import find_or_create_station

from .conftest import bearer, login

SECRET = "test-secret"
SAT, SUN = "2027-03-13", "2027-03-14"


def _station(session_factory, venue="Mölnlycke idrottshall", name="Café"):
    session = session_factory()
    station = find_or_create_station(session, venue, name)
    session.commit()
    return station.id


def _run(app, session_factory, person, *, apply=True, **fields):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, person.id), "f14-bla")
    action = REGISTRY["slots.create"]
    with app.test_request_context():
        plan, token = dry_run(session, ctx, action, action.parse(fields, {}), secret_key=SECRET)
        if apply:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")
    return plan


def test_a_weekend_cup_becomes_empty_seats(app, session_factory, admins):
    cafe = _station(session_factory)

    plan = _run(app, session_factory, admins["ansvarig"], days=[SAT, SUN], activity="Bästkustcupen", rows=[
        {"start": "9", "end": "12", "station_id": cafe, "seats": 2},
        {"start": "12:00", "end": "15:00", "station_id": cafe, "seats": 1, "on_days": ["sön"]},
    ])

    assert [c.summary for c in plan.changes] == [
        "lör 13 mar 09:00–12:00 Bästkustcupen · Café",
        "sön 14 mar 09:00–12:00 Bästkustcupen · Café",
        "sön 14 mar 12:00–15:00 Bästkustcupen · Café",
    ]
    assert plan.changes[0].detail == "2 platser · Mölnlycke idrottshall"
    seats = session_factory().query(Slot).filter_by(duty_name="Bästkustcupen").all()
    assert len(seats) == 5
    assert {(s.station, s.venue, s.station_id, s.player_id, s.origin) for s in seats} == {
        ("Café", "Mölnlycke idrottshall", cafe, None, "admin")
    }


def test_a_range_is_every_day_in_it(app, session_factory, admins):
    cafe = _station(session_factory)

    plan = _run(app, session_factory, admins["ansvarig"], apply=False, date_from=SAT, date_to=SUN,
                activity="Cup", rows=[{"start": "9", "end": "12", "station_id": cafe}])

    assert [c.summary[:10] for c in plan.changes] == ["lör 13 mar", "sön 14 mar"]
    assert plan.preview_dates == {date(2027, 3, 13), date(2027, 3, 14)}


def test_an_old_activity_name_gets_its_current_one(app, session_factory, admins):
    cafe = _station(session_factory)

    plan = _run(app, session_factory, admins["ansvarig"], apply=False, days=[SAT],
                activity="  arena värdskap  höst ", rows=[{"start": "9", "end": "12", "station_id": cafe}])

    assert "Cafépass" in plan.changes[0].summary


@pytest.mark.parametrize("row, key", [
    ({"start": "12", "end": "9", "station_id": 1}, "rows[0].end"),
    ({"start": "nio", "end": "12", "station_id": 1}, "rows[0].start"),
    ({"start": "9", "end": "12", "station_id": 999}, "rows[0].station_id"),
    ({"start": "9", "end": "12", "station_id": 1, "seats": 11}, "rows[0].seats"),
    ({"start": "9", "end": "12", "station_id": 1, "on_days": ["2027-04-01"]}, "rows[0].on_days"),
    ({"start": "9", "end": "12", "station_id": 1, "on_days": ["mån"]}, "rows[0].on_days"),
])
def test_a_bad_row_is_refused_next_to_its_field(app, session_factory, admins, row, key):
    _station(session_factory)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], days=[SAT], activity="Cup", rows=[row])

    assert key in caught.value.errors


@pytest.mark.parametrize("fields, key", [
    ({"days": [SAT], "activity": " ", "rows": [{"start": "9", "end": "12", "station_id": 1}]}, "activity"),
    ({"days": [SAT], "activity": "Cup", "rows": []}, "rows"),
    ({"date_from": "2027-03-01", "date_to": "2027-04-15", "activity": "Cup", "rows": []}, "date_to"),
    ({"date_from": SUN, "date_to": SAT, "activity": "Cup", "rows": []}, "date_to"),
    ({"activity": "Cup", "rows": []}, "days"),
])
def test_bad_days_or_activity_are_refused(app, session_factory, admins, fields, key):
    _station(session_factory)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], **fields)

    assert key in caught.value.errors


def test_too_many_seats_at_once_is_refused(app, session_factory, admins):
    cafe = _station(session_factory)
    rows = [{"start": f"{h}", "end": f"{h + 1}", "station_id": cafe, "seats": 10} for h in range(8, 12)]

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], date_from="2027-03-01", date_to="2027-03-08",
             activity="Cup", rows=rows)

    assert "rows" in caught.value.errors


def test_an_overlapping_shift_at_the_same_station_is_a_warning(app, session_factory, admins):
    cafe = _station(session_factory)
    session = session_factory()
    session.add(Slot(
        team_id=admins["team"].id, date=date(2027, 3, 13), start_time=time(10), end_time=time(13),
        station="Café", venue="Mölnlycke idrottshall", duty_name="Bästkustcupen", station_id=cafe,
    ))
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], apply=False, days=[SAT],
                activity="Bästkustcupen", rows=[{"start": "9", "end": "12", "station_id": cafe}])

    assert plan.warnings == [
        "Laget har redan ett pass som överlappar: lör 13 mar 10:00–13:00 Bästkustcupen · Café"
    ]


def test_the_form_prefills_a_shortcut(client, admins, session_factory):
    _station(session_factory)
    login(client, admins["ansvarig"])

    page = client.get(f"/admin/f14-bla/do/slots.create?date_from={SAT}&date_to={SUN}").get_data(as_text=True)

    assert f'value="{SAT}"' in page and f'value="{SUN}"' in page
    assert "Den här helgen" in page and "Café (Mölnlycke idrottshall)" in page


def test_the_form_goes_to_granska_with_the_phone_and_writes_nothing(
    client, admins, csrf_token, session_factory
):
    cafe = _station(session_factory)
    login(client, admins["ansvarig"])

    page = client.post("/admin/f14-bla/do/slots.create", data={
        "csrf_token": csrf_token(), "date_from": SAT, "date_to": SUN, "activity": "Bästkustcupen",
        "row-0-start": "9", "row-0-end": "12", "row-0-station_id": str(cafe), "row-0-seats": "2",
        "row-2-start": "12", "row-2-end": "15", "row-2-station_id": str(cafe), "row-2-seats": "1",
        "row-2-on_days": "lör",
    }).get_data(as_text=True)

    assert "Genomför 3 ändringar" in page
    assert 'class="phone"' in page and "Ledigt" in page
    assert session_factory().query(Slot).filter_by(duty_name="Bästkustcupen").count() == 0


def test_a_form_error_shows_by_its_own_row(client, admins, csrf_token, session_factory):
    cafe = _station(session_factory)
    login(client, admins["ansvarig"])

    response = client.post("/admin/f14-bla/do/slots.create", data={
        "csrf_token": csrf_token(), "date_from": SAT, "activity": "Cup",
        "row-2-start": "12", "row-2-end": "9", "row-2-station_id": str(cafe),
    })

    assert response.status_code == 400
    assert "Sluttiden måste vara efter starttiden" in response.get_data(as_text=True)


def test_both_doors_plan_the_same(client, admins, api_keys, csrf_token, session_factory):
    cafe = _station(session_factory)
    api = client.post("/admin/api/slots.create", json={
        "team": "f14-bla", "days": [SAT, SUN], "activity": "Bästkustcupen",
        "rows": [{"start": "9", "end": "12", "station_id": cafe, "seats": 2}],
    }, headers=bearer(api_keys["ansvarig"])).get_json()
    login(client, admins["ansvarig"])
    page = client.post("/admin/f14-bla/do/slots.create", data={
        "csrf_token": csrf_token(), "date_from": SAT, "date_to": SUN, "activity": "Bästkustcupen",
        "row-0-start": "9", "row-0-end": "12", "row-0-station_id": str(cafe), "row-0-seats": "2",
    }).get_data(as_text=True)

    assert api["counts"]["add"] == 2
    for change in api["changes"]:
        assert change["summary"] in page
