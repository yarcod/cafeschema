from duty_web.models import Person


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


def _month(seeded):
    day = seeded["future_slot"].date
    return f"year={day.year}&month={day.month}"


def test_team_page_shows_the_parents_first_team_by_default(client, seeded, second_team):
    _login(client, seeded["person"])

    body = client.get(f"/team?{_month(seeded)}").get_data(as_text=True)

    assert "Arena värdskap" in body
    assert "Kioskpass" not in body


def test_a_parent_on_two_teams_can_switch_to_the_other(client, seeded, second_team):
    _login(client, seeded["person"])

    body = client.get(f"/team?lag=p13&{_month(seeded)}").get_data(as_text=True)

    assert "Kioskpass" in body
    assert "Arena värdskap" not in body
    assert "lag=f14-bla" in body  # the switcher leads back
    assert body.count("lag=p13") >= 2  # month navigation stays on P13


def test_another_teams_calendar_is_not_found(client, seeded, second_team):
    _login(client, seeded["other_parent"])

    assert client.get("/team?lag=p13").status_code == 404


def test_a_parent_on_one_team_gets_no_team_switcher(client, seeded, second_team):
    _login(client, seeded["other_parent"])

    body = client.get(f"/team?{_month(seeded)}").get_data(as_text=True)

    assert "team-switch" not in body


def test_someone_with_no_child_on_any_team_sees_no_calendar(
    client, seeded, session_factory
):
    session = session_factory()
    outsider = Person(name="Ola", email="ola@exempel.se")
    session.add(outsider)
    session.commit()
    _login(client, outsider)

    response = client.get(f"/team?{_month(seeded)}")

    assert response.status_code == 200
    assert "Arena värdskap" not in response.get_data(as_text=True)


def test_mine_names_the_team_on_each_pass_for_a_parent_on_two(client, seeded, second_team):
    _login(client, seeded["person"])

    body = client.get("/").get_data(as_text=True)

    assert '<span class="slot-team">P13</span>' in body
    assert '<span class="slot-team">F14 Blå</span>' in body


def test_mine_leaves_the_team_out_for_a_parent_on_one(client, seeded, second_team):
    _login(client, seeded["other_parent"])

    assert "slot-team" not in client.get("/").get_data(as_text=True)
