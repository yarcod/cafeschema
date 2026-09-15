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
        venue="Wallenstam arena", player_id=seeded["player"].id,
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
        venue="Wallenstam arena", player_id=seeded["player"].id,
    ))
    session.commit()
    _login(client, seeded["person"])

    body = client.get("/team?year=2026&month=6").get_data(as_text=True)

    assert "Datum ej satt" in body
    assert "Arena värdskap vinter" in body


def _cafepass_slot(session_factory, seeded, **overrides):
    """A slot as the schedule now imports it: named, placed and stationed."""
    from datetime import date, time, timedelta

    from duty_web.models import Slot

    session = session_factory()
    fields = dict(
        team_id=seeded["team"].id,
        date=date.today() + timedelta(days=3),
        start_time=time(18, 0),
        end_time=time(21, 0),
        station="Café Arena B, nedre plan",
        duty_name="Cafépass",
        venue="Wallenstam arena",
        player_id=seeded["player"].id,
    )
    fields.update(overrides)
    session.add(Slot(**fields))
    session.commit()


def test_att_gora_says_where_and_which_cafe(client, seeded, session_factory):
    _cafepass_slot(session_factory, seeded)
    _login(client, seeded["person"])

    body = client.get("/").get_data(as_text=True)

    assert "Wallenstam arena" in body
    assert "Café Arena B, nedre plan" in body


def test_att_gora_links_to_the_instruction_for_that_duty(
    client, seeded, session_factory
):
    _cafepass_slot(session_factory, seeded)
    _login(client, seeded["person"])

    body = client.get("/").get_data(as_text=True)

    assert "/dokument/Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf" in body


def test_att_gora_falls_back_to_the_duty_name_when_there_is_no_station(
    client, seeded, session_factory
):
    _cafepass_slot(session_factory, seeded, station="", duty_name="Åby Julmarknad")
    _login(client, seeded["person"])

    body = client.get("/").get_data(as_text=True)

    assert "Åby Julmarknad" in body
