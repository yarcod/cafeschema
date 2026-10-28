"""The Pass tab and GET /admin/api/slots."""

import re
from datetime import time

from duty_web.models import Slot

from .conftest import bearer, login


def _open_seat(session_factory, seeded, day=None):
    session = session_factory()
    slot = Slot(
        team_id=seeded["team"].id, date=day, start_time=time(9), end_time=time(12),
        station="Café", duty_name="Bästkustcupen", venue="Mölnlycke idrottshall",
    )
    session.add(slot)
    session.commit()
    return slot


def test_admin_lands_on_pass(client, admins):
    login(client, admins["ansvarig"])

    response = client.get("/admin/")

    assert response.headers["Location"].endswith("/admin/f14-bla/pass")


def test_pass_lists_coming_shifts_with_names_never_addresses(client, admins, seeded):
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "Tova Exempel" in page and "18:00–21:00" in page
    # No address; the font link in the layout has an "@" of its own.
    assert not re.search(r"[\w.+-]+@[A-Za-z][\w-]*\.[A-Za-z]{2,}", page)
    assert "+ Lägg till pass" in page and "Importera Excel" in page


def test_an_open_seat_can_be_picked_for_staffing(client, admins, seeded, session_factory):
    seat = _open_seat(session_factory, seeded, seeded["future_slot"].date)
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert f'name="slot_ids" value="{seat.id}"' in page
    assert "1 ledig plats" in page and "Bemanna valda platser" in page


def test_past_days_are_hidden_until_asked_for(client, admins, seeded):
    login(client, admins["ansvarig"])
    past = f'slot_ids={seeded["slot"].id}"'   # the end of its one-seat Ändra link

    hidden = client.get("/admin/f14-bla/pass").get_data(as_text=True)
    shown = client.get("/admin/f14-bla/pass?tidigare=1").get_data(as_text=True)

    assert past not in hidden and past in shown


def test_each_seat_of_a_two_seat_shift_has_its_own_link(client, admins, seeded, session_factory):
    day = seeded["future_slot"].date
    first, second = _open_seat(session_factory, seeded, day), _open_seat(session_factory, seeded, day)
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert f'slot_ids={first.id}&amp;slot_ids={second.id}"' in page   # the shift's Ändra
    for seat in (first, second):
        assert f'slot_ids={seat.id}">Ändra spelare' in page


def test_two_stations_at_once_are_two_cards_with_their_own_seats(
    client, admins, seeded, session_factory
):
    day = seeded["future_slot"].date
    cafe = _open_seat(session_factory, seeded, day)
    session = session_factory()
    kiosk = Slot(
        team_id=seeded["team"].id, date=day, start_time=time(9), end_time=time(12),
        station="Kiosk", duty_name="Bästkustcupen", venue="Mölnlycke idrottshall",
    )
    session.add(kiosk)
    session.commit()
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert f'slot_ids={cafe.id}&amp;slot_ids={kiosk.id}' not in page
    assert "Kiosk · Mölnlycke idrottshall" in page and "Café · Mölnlycke idrottshall" in page


def test_undated_shifts_get_sätt_datum(client, admins, seeded, session_factory):
    _open_seat(session_factory, seeded)
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "Datum ej satt" in page and "Sätt datum" in page


def test_pass_does_not_exist_for_a_parent(client, admins):
    login(client, admins["parent"])

    assert client.get("/admin/f14-bla/pass").status_code == 404


def test_agents_read_the_same_slots_without_addresses(client, admins, api_keys, seeded):
    response = client.get("/admin/api/slots?team=f14-bla", headers=bearer(api_keys["ansvarig"]))

    assert response.status_code == 200
    body = response.get_json()
    ours = next(s for s in body["slots"] if s["id"] == seeded["future_slot"].id)
    assert ours["player"] == {"id": seeded["player"].id, "name": "Tova Exempel"}
    assert ours["start"] == "18:00" and ours["pending_swap"] is False
    assert "@" not in response.get_data(as_text=True)


def test_the_slots_read_filters_by_date_and_refuses_bad_dates(client, admins, api_keys, seeded):
    day = seeded["future_slot"].date.isoformat()

    only = client.get(f"/admin/api/slots?team=f14-bla&from={day}&to={day}", headers=bearer(api_keys["ansvarig"]))
    bad = client.get("/admin/api/slots?team=f14-bla&from=igår", headers=bearer(api_keys["ansvarig"]))

    assert {s["date"] for s in only.get_json()["slots"]} == {day}
    assert bad.status_code == 400


def test_another_teams_slots_are_not_found(client, admins, api_keys):
    response = client.get("/admin/api/slots?team=p13", headers=bearer(api_keys["ansvarig"]))

    assert response.status_code == 404
