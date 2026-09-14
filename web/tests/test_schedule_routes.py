def _login(client, person):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True


def test_mine_lists_the_logged_in_persons_slots(client, seeded):
    _login(client, seeded["person"])

    response = client.get("/")

    assert response.status_code == 200
    assert "Arena värdskap" in response.get_data(as_text=True)
    assert "16" in response.get_data(as_text=True)


def test_team_lists_slots_for_the_requested_month(client, seeded):
    _login(client, seeded["person"])

    response = client.get("/team?year=2026&month=1")

    assert response.status_code == 200
    assert "Arena värdskap" in response.get_data(as_text=True)


def test_team_is_empty_for_a_month_with_no_slots(client, seeded):
    _login(client, seeded["person"])

    response = client.get("/team?year=2026&month=6")

    assert response.status_code == 200
    assert "Arena värdskap" not in response.get_data(as_text=True)


def test_mine_lists_slots_whose_date_is_not_set_yet(client, seeded, session_factory):
    from datetime import time

    from duty_web.models import Slot

    session = session_factory()
    session.add(Slot(
        team_id=seeded["team"].id, date=None, start_time=time(18, 0),
        end_time=time(21, 0), station="", duty_name="Arena värdskap vinter",
        venue="Wallenstam arena", person_id=seeded["person"].id,
    ))
    session.commit()
    _login(client, seeded["person"])

    body = client.get("/").get_data(as_text=True)

    assert "Datum ej satt" in body
    assert "Arena värdskap vinter" in body


def test_team_lists_slots_whose_date_is_not_set_yet(client, seeded, session_factory):
    from datetime import time

    from duty_web.models import Slot

    session = session_factory()
    session.add(Slot(
        team_id=seeded["team"].id, date=None, start_time=time(18, 0),
        end_time=time(21, 0), station="", duty_name="Arena värdskap vinter",
        venue="Wallenstam arena", person_id=seeded["person"].id,
    ))
    session.commit()
    _login(client, seeded["person"])

    body = client.get("/team?year=2026&month=6").get_data(as_text=True)

    assert "Datum ej satt" in body
    assert "Arena värdskap vinter" in body
