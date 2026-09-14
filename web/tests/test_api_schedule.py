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
        venue="Wallenstam arena", person_id=None,
    ))
    session.commit()

    response = client.get("/api/schedule", headers={"X-Api-Key": "test-api-key"})

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload) == 2
    # Verify empty slot (person_id=None) is filtered out, filled slots are returned
    assert all(slot["person"]["email"] == "tova@exempel.se" for slot in payload)
    assert all(slot["station"] == "Cafe" for slot in payload)
