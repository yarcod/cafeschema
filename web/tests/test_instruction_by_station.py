"""Once a slot has a station, the station decides which instruction it links."""

from duty_web.duties import profile_for
from duty_web.models import Slot, Team
from duty_web.places import find_or_create_station

API = {"X-Api-Key": "test-api-key"}


def _future_row(client, slot):
    payload = client.get("/api/schedule", headers=API).get_json()
    return next(row for row in payload if row["date"] == slot.date.isoformat())


def test_a_linked_station_decides_the_instruction(client, seeded, session_factory):
    session = session_factory()
    slot = session.get(Slot, seeded["future_slot"].id)
    slot.duty_name = "Julcupen"  # no profile in duties.py
    station = find_or_create_station(session, "Mölnlycke idrottshall", "Café")
    station.document = "Cafeteria_Instruktion_MIH.pdf"
    slot.station_id = station.id
    session.commit()

    row = _future_row(client, slot)

    assert row["document_url"].endswith("/dokument/Cafeteria_Instruktion_MIH.pdf")


def test_a_linked_station_without_instruction_links_nothing(client, seeded, session_factory):
    session = session_factory()
    slot = session.get(Slot, seeded["future_slot"].id)
    slot.duty_name = "Bästkustcupen"  # its profile has an instruction…
    station = find_or_create_station(session, "Mölnlycke idrottshall", "Kiosk")
    slot.station_id = station.id  # …but the station it is linked to has none
    session.commit()

    assert _future_row(client, slot)["document_url"] is None


def test_migrated_slots_link_what_the_duty_name_linked(app, seeded, session_factory):
    from duty_web.routes.document_routes import (
        instruction_url_for_duty,
        instruction_url_for_slot,
    )

    session = session_factory()
    team = session.get(Team, seeded["team"].id)
    for duty in ("Cafépass", "Bästkustcupen", "Arena värdskap höst", "Åby Julmarknad"):
        slot = Slot(
            team_id=team.id, date=seeded["future_slot"].date,
            start_time=seeded["future_slot"].start_time, end_time=seeded["future_slot"].end_time,
            station="", duty_name=duty, venue="",
        )
        profile = profile_for(duty)
        if profile:
            # What migrate.py does: the profile's station, with its instruction.
            station = find_or_create_station(session, profile.venue, profile.station)
            station.document = profile.document
            slot.station_id = station.id
        session.add(slot)
    session.commit()
    session = session_factory()

    with app.test_request_context():
        for slot in session.query(Slot).all():
            assert instruction_url_for_slot(slot) == instruction_url_for_duty(
                slot.duty_name, team.slug
            ), slot.duty_name
