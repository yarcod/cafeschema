def test_api_schedule_requires_the_api_key(client):
    response = client.get("/api/schedule")

    assert response.status_code == 401


def test_api_schedule_rejects_the_wrong_key(client):
    response = client.get("/api/schedule", headers={"X-Api-Key": "wrong"})

    assert response.status_code == 401


def test_api_schedule_returns_filled_slots_only(client, seeded, session_factory):
    from datetime import date, time
    from duty_web.models import Slot

    session = session_factory()
    session.add(Slot(
        team_id=seeded["team"].id, date=date(2026, 2, 6), start_time=time(18, 0),
        end_time=time(21, 0), station="Kiosk", duty_name="Arena värdskap",
        venue="Wallenstam arena", player_id=None,
    ))
    session.commit()

    response = client.get("/api/schedule", headers={"X-Api-Key": "test-api-key"})

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload) == 2
    # Verify the unassigned slot is filtered out, filled slots are returned
    assert all(slot["player"]["name"] == "Tova Exempel" for slot in payload)
    assert all(slot["station"] == "Cafe" for slot in payload)


def test_api_schedule_lists_every_parent_of_the_player(client, seeded):
    """Both parents may turn up, so both are reminder recipients."""
    response = client.get("/api/schedule", headers={"X-Api-Key": "test-api-key"})

    entry = response.get_json()[0]
    assert [parent["email"] for parent in entry["parents"]] == [
        "tova@exempel.se", "bjorn@exempel.se",
    ]
    assert all(parent["email_notifications"] for parent in entry["parents"])


def test_api_schedule_links_each_shift_to_its_instruction(
    client, seeded, session_factory
):
    """The mailer doesn't know which document belongs to which duty; the app
    that owns the documents says so."""
    from datetime import date, time, timedelta

    from duty_web.models import Slot

    session = session_factory()
    session.add(Slot(
        team_id=seeded["team"].id, date=date.today() + timedelta(days=3),
        start_time=time(18, 0), end_time=time(21, 0),
        station="Café", duty_name="Bästkustcupen",
        venue="Mölnlycke idrottshall", player_id=seeded["player"].id,
    ))
    session.commit()

    payload = client.get(
        "/api/schedule", headers={"X-Api-Key": "test-api-key"}
    ).get_json()

    cup = next(entry for entry in payload if entry["duty_name"] == "Bästkustcupen")
    assert cup["document_url"].endswith("/dokument/Cafeteria_Instruktion_MIH.pdf")
    assert cup["document_url"].startswith("http")


def test_a_duty_with_no_instruction_has_no_document_url(client, seeded):
    """'Arena värdskap' predates the rename table and maps to nothing."""
    payload = client.get(
        "/api/schedule", headers={"X-Api-Key": "test-api-key"}
    ).get_json()

    assert payload[0]["document_url"] is None
