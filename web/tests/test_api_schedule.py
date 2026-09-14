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
