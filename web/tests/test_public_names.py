"""A parent's address must never be shown to another parent, not even when
the address is what ended up in the name column."""

from datetime import date, time

from duty_web.admin.actions import REGISTRY
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.permissions import admin_context
from duty_web.models import Person, Slot
from duty_web.roster import (
    NAMELESS,
    parent_names_of_slot,
    public_name,
)

from .conftest import make_family
from .test_members_csv import export


def _login(client, person):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True


def test_public_name_hides_empty_names_and_addresses():
    assert public_name(Person(name="Anna", email="a@x.se")) == "Anna"
    assert public_name(Person(name="", email="a@x.se")) == NAMELESS == "(namn saknas)"
    assert public_name(Person(name="a@x.se", email="a@x.se")) == NAMELESS


def test_an_import_row_without_a_name_stores_no_name(session_factory, admins):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    action = REGISTRY["roster.import"]
    rows = export("Nils,Ny,,,Okänd Förälder,", ",,ny@exempel.se,,,Nils Ny").decode("utf-8")
    _, token = dry_run(session, ctx, action, action.parse({"csv": rows}, {}), secret_key="test-secret")
    apply_plan(session, ctx, token, secret_key="test-secret", via="webb")

    person = session_factory().query(Person).filter_by(email="ny@exempel.se").one()
    assert person.name == ""


def test_team_page_shows_a_nameless_parent_as_placeholder(
    client, seeded, session_factory
):
    session = session_factory()
    session.get(Person, seeded["person"].id).name = "tova@exempel.se"
    session.get(Person, seeded["other_parent"].id).name = ""
    session.commit()
    _login(client, seeded["other_parent"])

    body = client.get("/team?year=2026&month=1").get_data(as_text=True)

    assert "(namn saknas)" in body
    assert "tova@exempel.se" not in body


def test_swap_proposal_mail_names_a_nameless_proposer_by_placeholder(
    client, seeded, session_factory, monkeypatch, csrf_token
):
    session = session_factory()
    session.get(Person, seeded["person"].id).name = "tova@exempel.se"
    bea = make_family(
        session, seeded["team"], "Bea Bengtsson", [("Bo Bengtsson", "bo@exempel.se")]
    )
    theirs = Slot(
        team_id=seeded["team"].id, date=date(2026, 12, 5), start_time=time(9, 0),
        end_time=time(12, 0), station="", duty_name="Bästkustcupen", venue="",
        player_id=bea.id,
    )
    session.add(theirs)
    session.commit()
    sent = {}
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed",
        lambda emails, **kwargs: sent.update(kwargs),
    )
    _login(client, seeded["person"])

    client.post("/swaps", data={
        "proposer_slot_id": str(seeded["future_slot"].id),
        "target_slot_ids": [str(theirs.id)],
        "csrf_token": csrf_token(),
    })

    assert sent["proposer_name"] == "(namn saknas)"
    assert "@" not in "".join(str(v) for v in sent.values() if not isinstance(v, list))


def test_prose_names_of_a_slot_never_show_addresses(seeded, session_factory):
    session = session_factory()
    session.get(Person, seeded["person"].id).name = "tova@exempel.se"
    session.get(Person, seeded["other_parent"].id).name = "bjorn@exempel.se"
    session.commit()

    assert parent_names_of_slot(session, seeded["slot"]) == "(namn saknas) & (namn saknas)"
