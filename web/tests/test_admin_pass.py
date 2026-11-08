"""The Pass tab and GET /admin/api/slots."""

import re
from datetime import time, timedelta

from duty_web.admin.pending import new_pass, team_entries
from duty_web.clock import utcnow
from duty_web.dates import short_date_sv
from duty_web.models import Player, Slot, SwapRequest

from .conftest import bearer, login, run_action


def test_admin_lands_on_pass(client, admins):
    login(client, admins["ansvarig"])

    response = client.get("/admin/")

    assert response.headers["Location"].endswith("/admin/f14-bla/pass")


def _stage(app, session_factory, admins, **fields):
    return run_action(app, session_factory, admins["ansvarig"], "pending.edit", **fields)


def _rows(page):
    return re.findall(r'<tr class="pass-row[^"]*"', page)


def test_every_pass_is_a_row_with_editable_cells(client, admins, seeded):
    login(client, admins["ansvarig"])
    slot = seeded["future_slot"]

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert 'name="day"' in page and 'name="station_id"' in page
    assert f'name="seat-{slot.id}"' in page
    assert f'value="{slot.date.isoformat()}"' in page and 'value="18:00"' in page
    assert "Tova Exempel" in page
    assert "admin_pass.js" in page
    assert "+ Lägg till pass" in page and "Importera Excel" in page


def test_a_vantande_edit_shows_on_the_pass_page_after_coming_back(
    client, app, session_factory, admins, seeded
):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], start="19:00")
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert 'class="pass-row pend"' in page and 'value="19:00"' in page
    assert "1 väntande ändring, inga mejl skickade" in page
    assert "/f14-bla/granska" in page


def test_the_bar_counts_every_vantande_pass(client, app, session_factory, admins, seeded, second_team):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], start="19:00")
    _stage(app, session_factory, admins, slot_ids=[second_team["neighbour_slot"].id], note="Hjälp")
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "2 väntande ändringar, inga mejl skickade" in page


def test_a_changed_cell_is_marked_and_the_others_are_not(client, app, session_factory, admins, seeded):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], start="19:00")
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert re.search(r'class="[^"]*cell-ch[^"]*"[^>]*>\s*<input[^>]*name="start"', page)
    assert not re.search(r'cell-ch[^>]*>\s*<input[^>]*name="end"', page)


def test_rows_show_the_staged_value_under_the_staged_date(
    client, app, session_factory, admins, seeded
):
    slot = seeded["future_slot"]
    moved = slot.date + timedelta(days=3)
    _stage(app, session_factory, admins, slot_ids=[slot.id], day=moved.isoformat())
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    heading = page.index(f"{short_date_sv(moved)}</td>")
    under_it = page[heading:].split('class="day-head"')[0]
    assert f'name="seat-{slot.id}"' in under_it and f'value="{moved.isoformat()}"' in under_it
    # The neighbour's Pass stays under the live day; the staged one has left it.
    old = page.index(f"{short_date_sv(slot.date)}</td>")
    assert f'name="seat-{slot.id}"' not in page[old:].split('class="day-head"')[0]


def test_a_removed_row_is_struck_through(client, app, session_factory, admins, seeded):
    run_action(app, session_factory, admins["ansvarig"], "pending.remove", slot_ids=[seeded["future_slot"].id])
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert 'class="pass-row pend gone"' in page
    row = page[page.index('class="pass-row pend gone"'):]
    row = row[:row.index("</tr>")]
    assert "disabled" in row and "Ångra" in row and "pending.undo" in row


def test_a_new_pass_is_a_row_before_it_exists_in_slots(client, app, session_factory, admins, seeded):
    session = session_factory()
    entry = new_pass(seeded["team"].id, "Entré", seeded["future_slot"].date, time(9), time(11), None, 2)
    session.add(entry)
    session.commit()
    before = session.query(Slot).count()
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert 'name="seat-new-0"' in page and 'name="seat-new-1"' in page
    assert f'name="entry_id" value="{entry.id}"' in page
    assert "Entré" in page
    assert session.query(Slot).count() == before


def test_no_bar_when_nothing_is_vantande(client, admins, seeded):
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "pending-bar" not in page and "pass-row pend" not in page


def test_past_rows_stay_hidden_until_asked_for(client, admins, seeded):
    login(client, admins["ansvarig"])
    past = f'name="seat-{seeded["slot"].id}"'

    hidden = client.get("/admin/f14-bla/pass").get_data(as_text=True)
    shown = client.get("/admin/f14-bla/pass?tidigare=1").get_data(as_text=True)

    assert past not in hidden and past in shown


def test_the_table_has_names_and_never_addresses(client, app, session_factory, admins, seeded):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], start="19:00")
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "Tova Exempel" in page
    assert "exempel.se" not in page
    assert not re.search(r"[\w.+-]+@[A-Za-z][\w-]*\.[A-Za-z]{2,}", page)


def test_the_old_edit_pages_are_gone(client, admins, seeded):
    login(client, admins["ansvarig"])

    assert client.get("/admin/f14-bla/do/slot.update").status_code == 404
    assert client.get("/admin/f14-bla/do/slot.delete").status_code == 404
    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)
    assert "slot.update" not in page and "slot.delete" not in page


def test_another_ansvarig_sees_the_same_batch(client, app, session_factory, admins, seeded):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], start="19:00")
    login(client, admins["club"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert 'class="pass-row pend"' in page and "1 väntande ändring" in page


def test_an_open_seat_names_the_schedules_spelling_until_it_is_staged(
    client, app, session_factory, admins, seeded
):
    session = session_factory()
    seat = Slot(
        team_id=seeded["team"].id, date=seeded["future_slot"].date, start_time=time(9), end_time=time(12),
        station="Café", duty_name="Bästkustcupen", venue="Mölnlycke idrottshall", child_name="Tove E.",
    )
    session.add(seat)
    session.commit()
    login(client, admins["ansvarig"])

    before = client.get("/admin/f14-bla/pass").get_data(as_text=True)
    _stage(app, session_factory, admins, slot_ids=[seat.id], **{f"seat-{seat.id}": str(seeded["player"].id)})
    staged = client.get("/admin/f14-bla/pass").get_data(as_text=True)
    _stage(app, session_factory, admins, slot_ids=[seat.id], **{f"seat-{seat.id}": ""})
    cleared = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "Tove E. i schemat" in before
    assert "Tove E. i schemat" not in staged
    # Back to what the schedule says, so its spelling applies again.
    assert "Tove E. i schemat" in cleared


def test_a_leader_family_is_marked_in_the_player_list(client, admins, seeded, session_factory):
    session = session_factory()
    session.add(Player(name="Ledarbarn Exempel", team_id=seeded["team"].id, exempt=True))
    session.commit()
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "Ledarbarn Exempel (ledarfamilj)" in page
    assert "Nils Främling" not in page


def test_a_waiting_swap_is_tagged_on_the_seat(client, admins, seeded, second_team, session_factory):
    session = session_factory()
    session.add(SwapRequest(
        proposer_slot_id=seeded["future_slot"].id, target_slot_id=second_team["neighbour_slot"].id,
        created_at=utcnow(), status="pending",
    ))
    session.commit()
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "Byte väntar" in page


def test_a_row_that_no_longer_matches_is_flagged(client, app, session_factory, admins, seeded):
    _stage(app, session_factory, admins, slot_ids=[seeded["future_slot"].id], start="19:00")
    session = session_factory()
    session.get(Slot, seeded["future_slot"].id).start_time = time(17)
    session.commit()
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "Behöver ses över" in page


def test_rows_post_without_javascript(client, app, session_factory, admins, seeded, csrf_token):
    slot = seeded["future_slot"]
    login(client, admins["ansvarig"])

    response = client.post("/admin/f14-bla/do/pending.edit", data={
        "csrf_token": csrf_token(), "slot_ids": [str(slot.id)], "day": slot.date.isoformat(),
        "start": "19:00", "end": "21:00", "station_id": "", "note": "",
        f"seat-{slot.id}": str(seeded["player"].id),
    })

    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/f14-bla/pass")
    entries = team_entries(session_factory(), seeded["team"].id)
    assert [list(e.fields) for e in entries] == [["start_time"]]


def test_klart_shows_only_after_genomfor(client, admins, seeded):
    login(client, admins["ansvarig"])

    done = client.get("/admin/f14-bla/pass?klart=pending.publish").get_data(as_text=True)
    other = client.get("/admin/f14-bla/pass?klart=pending.edit").get_data(as_text=True)

    assert "Klart." in done and "Klart." not in other


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


def test_pass_offers_the_excel_template_next_to_the_import(client, admins):
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/pass").get_data(as_text=True)

    assert "/admin/f14-bla/pass/mall.xlsx" in page


def test_the_template_lists_the_teams_children_but_not_the_exempt(
    client, admins, seeded, session_factory
):
    from io import BytesIO

    from openpyxl import load_workbook

    from duty_web.models import Player

    session = session_factory()
    session.add(Player(name="Ledarbarn Exempel", team_id=seeded["team"].id, exempt=True))
    session.commit()
    login(client, admins["ansvarig"])

    response = client.get("/admin/f14-bla/pass/mall.xlsx")

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store"
    lists = load_workbook(BytesIO(response.data))["Listor"]
    children = [c.value for c in lists["A"] if c.value]
    activities = [c.value for c in lists["B"] if c.value]
    assert "Tova Exempel" in children
    assert "Ledarbarn Exempel" not in children
    assert "Nils Främling" not in children  # P13's child
    assert children == sorted(children)
    # Known activities, and the team's own under their current names.
    assert {"Cafépass", "Bästkustcupen"} <= set(activities)
    assert "Arena värdskap" in activities


def test_the_template_is_for_the_teams_admins_only(client, admins):
    login(client, admins["parent"])

    assert client.get("/admin/f14-bla/pass/mall.xlsx").status_code == 404


def test_the_import_page_points_to_the_template(client, admins):
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/do/schedule.import").get_data(as_text=True)

    assert "/admin/f14-bla/pass/mall.xlsx" in page
