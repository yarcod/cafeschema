"""slots.from_pattern: a whole café weekend as väntande Pass in one step."""

from datetime import date

import pytest

from duty_web.admin.actions import REGISTRY
from duty_web.admin.actions.base import ValidationError
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.permissions import admin_context
from duty_web.models import PendingPass, Person, Slot
from duty_web.places import find_or_create_station

from .conftest import bearer, login

SECRET = "test-secret"
FRI, SUN = "2026-11-20", "2026-11-22"


def _arena_b(session_factory):
    session = session_factory()
    station = find_or_create_station(session, "Wallenstam arena", "Café Arena B, nedre plan")
    session.commit()
    return station.id


def _run(app, session_factory, person, *, slug="f14-bla", apply=True, **fields):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, person.id), slug)
    action = REGISTRY["slots.from_pattern"]
    with app.test_request_context():
        plan, token = dry_run(session, ctx, action, action.parse(fields, {}), secret_key=SECRET)
        if apply:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")
    return plan


def test_the_action_stages_two_seats_per_pass_at_the_default_cafe(app, session_factory, admins):
    arena_b = _arena_b(session_factory)
    before = session_factory().query(Slot).count()

    plan = _run(app, session_factory, admins["ansvarig"], days=[FRI, "2026-11-21", SUN])

    session = session_factory()
    entries = session.query(PendingPass).all()
    assert len(entries) == 11 and plan.public()["counts"]["add"] == 11
    assert {e.duty_name for e in entries} == {"Cafépass"}
    assert {e.fields["station_id"]["to"] for e in entries} == {arena_b}
    assert all(len(e.seats) == 2 for e in entries)
    assert session.query(Slot).count() == before


def test_three_seats_applies_to_the_whole_weekend(app, session_factory, admins):
    _arena_b(session_factory)

    _run(app, session_factory, admins["ansvarig"], date_from=FRI, date_to=SUN, seats=3)

    assert {len(e.seats) for e in session_factory().query(PendingPass)} == {3}


def test_four_seats_is_refused(app, session_factory, admins):
    _arena_b(session_factory)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], date_from=FRI, date_to=SUN, seats=4)

    assert caught.value.errors == {"seats": "Välj 2 eller 3 föräldrar per pass"}


def test_adjusted_rows_replace_the_pattern(app, session_factory, admins):
    _arena_b(session_factory)

    _run(app, session_factory, admins["ansvarig"], date_from=FRI, date_to=SUN, rows=[
        {"day": "2026-11-21", "start": "9", "end": "11"},
        {"day": "2026-11-22", "start": "10:00", "end": "12:00"},
    ])

    got = sorted((e.fields["date"]["to"], e.fields["start_time"]["to"]) for e in session_factory().query(PendingPass))
    assert got == [("2026-11-21", "09:00"), ("2026-11-22", "10:00")]


def test_an_adjusted_row_must_end_after_it_starts(app, session_factory, admins):
    _arena_b(session_factory)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], date_from=FRI, date_to=SUN,
             rows=[{"day": FRI, "start": "12", "end": "9"}])

    assert caught.value.errors == {"rows[0].end": "Sluttiden måste vara efter starttiden"}


def test_a_span_with_no_matching_day_is_refused(app, session_factory, admins):
    _arena_b(session_factory)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], date_from="2026-11-23", date_to="2026-11-25")

    assert caught.value.errors == {"days": "Inga fredagar, lördagar eller söndagar i de valda dagarna"}


def test_an_unknown_pattern_is_refused(app, session_factory, admins):
    _arena_b(session_factory)

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], date_from=FRI, date_to=SUN, pattern="nope")

    assert caught.value.errors == {"pattern": "Okänt mönster"}


def test_without_the_default_cafe_a_place_must_be_chosen(app, session_factory, admins):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], date_from=FRI, date_to=SUN)

    assert caught.value.errors == {"station_id": "Välj en plats"}


def test_all_teams_get_the_same_pattern(app, session_factory, admins):
    _arena_b(session_factory)

    a = _run(app, session_factory, admins["club"], slug="f14-bla", date_from=FRI, date_to=SUN, apply=False)
    b = _run(app, session_factory, admins["club"], slug="p13", date_from=FRI, date_to=SUN, apply=False)

    assert [c.summary for c in a.changes] == [c.summary for c in b.changes]


def test_the_form_offers_weekends_and_the_default_cafe(client, admins, session_factory):
    arena_b = _arena_b(session_factory)
    login(client, admins["ansvarig"])

    page = client.get(f"/admin/f14-bla/do/slots.from_pattern?date_from={FRI}&date_to={SUN}").get_data(as_text=True)

    assert "Nästa helg" in page and "v 4" in page
    assert f'<option value="{arena_b}" selected' in page
    assert "Lägg till 11 pass som väntande" in page and 'name="row-10-start"' in page


def test_the_form_stages_and_skips_removed_rows(client, admins, csrf_token, session_factory):
    _arena_b(session_factory)
    login(client, admins["ansvarig"])
    data = {"csrf_token": csrf_token(), "date_from": FRI, "date_to": "2026-11-21", "seats": "2"}
    data.update({"row-0-day": FRI, "row-0-start": "17:30", "row-0-end": "19:30"})
    data.update({"row-1-day": "2026-11-21", "row-1-start": "08:30", "row-1-end": "10:30", "row-1-skip": "1"})

    response = client.post("/admin/f14-bla/do/slots.from_pattern", data=data)

    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/f14-bla/pass")
    assert session_factory().query(PendingPass).count() == 1


def test_the_pass_page_leads_with_the_cafe_weekend(client, admins):
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "+ Lägg till cafépass" in page and "slots.from_pattern" in page


def test_discovery_describes_the_pattern(client, api_keys):
    pattern = client.get("/admin/api", headers=bearer(api_keys["club"])).get_json()["patterns"]["cafe_weekend"]

    assert pattern["activity"] == "Cafépass" and pattern["seats"] == 2
    assert pattern["seat_choices"] == [2, 3]
    assert pattern["sessions"]["fre"] == [["17:30", "19:30"]] and len(pattern["sessions"]["lör"]) == 5


def test_an_agent_can_ask_for_it_by_name(client, api_keys, session_factory):
    _arena_b(session_factory)

    body = client.post("/admin/api/slots.from_pattern", json={
        "team": "f14-bla", "date_from": FRI, "date_to": SUN, "dry_run": True,
    }, headers=bearer(api_keys["club"])).get_json()

    assert body["counts"]["add"] == 11 and "plan_token" in body
