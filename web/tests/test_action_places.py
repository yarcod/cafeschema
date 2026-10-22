"""venue.create, station.create, station.link_document — and the Platser page."""

import pytest

from duty_web.admin.actions import REGISTRY
from duty_web.admin.actions.base import NearDuplicate, ValidationError
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.permissions import admin_context
from duty_web.models import Person, Slot, Station, Venue
from duty_web.places import find_or_create_station, list_documents, rank_documents

from .conftest import bearer, login

SECRET = "test-secret"
API = {"X-Api-Key": "test-api-key"}


def _run(app, session_factory, person, action_name, **fields):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, person.id), "f14-bla")
    action = REGISTRY[action_name]
    with app.test_request_context():
        plan, token = dry_run(session, ctx, action, action.parse(fields, {}), secret_key=SECRET)
        apply_plan(session, ctx, token, secret_key=SECRET, via="webb")
    return plan


def test_a_new_venue(app, session_factory, admins):
    plan = _run(app, session_factory, admins["ansvarig"], "venue.create", name="  Hindås ")

    assert [c.summary for c in plan.changes] == ["Ny plats: Hindås"]
    assert session_factory().query(Venue).one().name_key == "hindas"


def test_a_venue_spelled_differently_is_the_same_venue(client, admins, api_keys, session_factory):
    session = session_factory()
    find_or_create_station(session, "Wallenstam arena", "Café Arena B")
    session.commit()

    response = client.post("/admin/api/venue.create", json={"name": "wallenstam  ARENA"},
                           headers=bearer(api_keys["ansvarig"]))

    assert response.status_code == 409
    assert response.get_json()["existing"]["name"] == "Wallenstam arena"


def test_a_decomposed_accent_is_a_near_duplicate_station(app, session_factory, admins):
    session = session_factory()
    venue = find_or_create_station(session, "Mölnlycke idrottshall", "Café").venue
    session.commit()

    with pytest.raises(NearDuplicate) as caught:
        _run(app, session_factory, admins["ansvarig"], "station.create",
             venue_id=str(venue.id), name="Café ")

    assert caught.value.existing_name == "Café"


def test_a_new_station_with_its_instruction(app, session_factory, admins):
    session = session_factory()
    venue = find_or_create_station(session, "Mölnlycke idrottshall", "Café").venue
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], "station.create",
                venue_id=str(venue.id), name="Kiosk entré", document="Cafeteria_Instruktion_MIH.pdf")

    assert plan.changes[0].summary == "Ny station: Kiosk entré"
    kiosk = session_factory().query(Station).filter_by(name="Kiosk entré").one()
    assert kiosk.document == "Cafeteria_Instruktion_MIH.pdf"


def test_only_uploaded_instructions_can_be_linked(app, session_factory, admins):
    session = session_factory()
    station = find_or_create_station(session, "Mölnlycke idrottshall", "Café")
    session.commit()

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "station.link_document",
             station_id=str(station.id), document="../../etc/passwd")

    assert "document" in caught.value.errors


def test_linking_changes_what_every_slot_there_links(app, client, seeded, session_factory, admins):
    session = session_factory()
    station = find_or_create_station(session, "Mölnlycke idrottshall", "Café")
    slot = session.get(Slot, seeded["future_slot"].id)
    slot.station_id = station.id
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], "station.link_document",
                station_id=str(station.id), document="Cafeteria_Instruktion_MIH.pdf")

    assert plan.changes[0].detail == "ingen → Cafeteria Instruktion MIH"
    row = next(r for r in client.get("/api/schedule", headers=API).get_json()
               if r["date"] == slot.date.isoformat())
    assert row["document_url"].endswith("/dokument/Cafeteria_Instruktion_MIH.pdf")


def test_linking_what_is_already_linked_is_nothing_to_do(app, session_factory, admins):
    session = session_factory()
    station = find_or_create_station(session, "Mölnlycke idrottshall", "Café")
    station.document = "Cafeteria_Instruktion_MIH.pdf"
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], "station.link_document",
                station_id=str(station.id), document="Cafeteria_Instruktion_MIH.pdf")

    assert plan.changes == []


def test_a_station_needs_a_known_venue(app, session_factory, admins):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "station.create", venue_id="999", name="Kiosk")

    assert caught.value.errors == {"venue_id": "Okänd plats"}


def test_documents_are_listed_shared_and_per_team_and_ranked(documents_dir):
    (documents_dir / "p13").mkdir()
    (documents_dir / "p13" / "Cafeteria_Instruktion_Hindås.pdf").write_bytes(b"%PDF")

    docs = list_documents(documents_dir, ["p13"])

    assert [(d.filename, d.team_slug) for d in docs] == [
        ("Cafeteria_Instruktion_MIH.pdf", None),
        ("Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf", None),
        ("Cafeteria_Instruktion_Hindås.pdf", "p13"),
    ]
    assert rank_documents("Hindås", "Café", docs)[0].filename == "Cafeteria_Instruktion_Hindås.pdf"


def test_the_platser_page_and_the_venue_read(client, admins, api_keys, session_factory):
    session = session_factory()
    station = find_or_create_station(session, "Mölnlycke idrottshall", "Café")
    station.document = "Cafeteria_Instruktion_MIH.pdf"
    session.commit()
    login(client, admins["ansvarig"])

    page = client.get("/admin/f14-bla/platser").get_data(as_text=True)
    venues = client.get("/admin/api/venues", headers=bearer(api_keys["ansvarig"])).get_json()

    assert "Mölnlycke idrottshall" in page
    unlinked = page.split("Uppladdade men inte kopplade")[1]
    assert "Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf" in unlinked
    assert "Cafeteria_Instruktion_MIH.pdf" not in unlinked  # linked to Café
    assert venues == [{"id": station.venue_id, "name": "Mölnlycke idrottshall", "stations": [
        {"id": station.id, "name": "Café", "document": "Cafeteria_Instruktion_MIH.pdf", "slots": 0}]}]


def test_both_doors_review_the_same_new_venue(client, admins, api_keys, csrf_token):
    api = client.post("/admin/api/venue.create", json={"name": "Hindås"},
                      headers=bearer(api_keys["ansvarig"])).get_json()
    login(client, admins["ansvarig"])
    page = client.post("/admin/f14-bla/do/venue.create",
                       data={"csrf_token": csrf_token(), "name": "Hindås"}).get_data(as_text=True)

    assert [c["summary"] for c in api["changes"]] == ["Ny plats: Hindås"]
    assert "Ny plats: Hindås" in page


def test_a_parent_without_rights_cannot_create_places(client, api_keys):
    response = client.post("/admin/api/venue.create", json={"name": "Hindås"},
                           headers=bearer(api_keys["parent"]))

    assert response.status_code == 404


@pytest.mark.parametrize("blank", ["", "   ", "\u00a0 \t"])
def test_a_blank_name_is_never_a_new_venue_or_station(app, session_factory, admins, blank):
    session = session_factory()
    venue = find_or_create_station(session, "Mölnlycke idrottshall", "Café").venue
    session.commit()

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "venue.create", name=blank)
    assert "name" in caught.value.errors
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "station.create",
             venue_id=str(venue.id), name=blank)
    assert "name" in caught.value.errors
    assert session_factory().query(Venue).count() == 1
    assert session_factory().query(Station).count() == 1


def test_a_decomposed_accent_with_extra_spaces_matches_cafe(app, session_factory, admins):
    session = session_factory()
    venue = find_or_create_station(session, "Mölnlycke idrottshall", "Café").venue
    session.commit()

    with pytest.raises(NearDuplicate) as caught:
        _run(app, session_factory, admins["ansvarig"], "station.create",
             venue_id=str(venue.id), name="  Cafe\u0301   ")

    assert caught.value.existing_name == "Café"
    assert session_factory().query(Station).count() == 1


@pytest.mark.parametrize("bad", ["sub/Cafeteria_Instruktion_MIH.pdf", "..", "notes.exe",
                                 "Cafeteria_Instruktion_MIH.pdf/", "saknas.pdf"])
def test_station_create_only_takes_listed_documents(app, session_factory, admins, bad):
    session = session_factory()
    venue = find_or_create_station(session, "Mölnlycke idrottshall", "Café").venue
    session.commit()

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "station.create",
             venue_id=str(venue.id), name="Kiosk", document=bad)

    assert "document" in caught.value.errors


def test_an_empty_document_unlinks(app, session_factory, admins):
    session = session_factory()
    station = find_or_create_station(session, "Mölnlycke idrottshall", "Café")
    station.document = "Cafeteria_Instruktion_MIH.pdf"
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], "station.link_document",
                station_id=str(station.id), document="")

    assert plan.changes[0].detail == "Cafeteria Instruktion MIH → ingen"
    assert session_factory().get(Station, station.id).document is None
