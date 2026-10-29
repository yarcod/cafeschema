"""The station decides which instruction a slot links, and nothing else does."""

from duty_web.models import Slot
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
    slot.duty_name = "Bästkustcupen"  # its usual café links an instruction…
    find_or_create_station(session, "Mölnlycke idrottshall", "Café").document = (
        "Cafeteria_Instruktion_MIH.pdf"
    )
    station = find_or_create_station(session, "Mölnlycke idrottshall", "Kiosk")
    slot.station_id = station.id  # …but the station it is linked to has none
    session.commit()

    assert _future_row(client, slot)["document_url"] is None


def test_a_slot_without_a_station_links_nothing(client, seeded, session_factory):
    """Not even when its duty is known: until an ansvarig places it, the
    button goes to the document list rather than to a guess."""
    session = session_factory()
    slot = session.get(Slot, seeded["future_slot"].id)
    slot.duty_name = "Cafépass"
    session.commit()

    assert _future_row(client, slot)["document_url"] is None
