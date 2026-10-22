"""team_admins.add / team_admins.remove: trust follows the permission (spec D8)."""

import logging
import smtplib

import pytest

from duty_web.admin.actions import REGISTRY
from duty_web.admin.actions.base import ValidationError
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.plan_tokens import PlanTokenError
from duty_web.admin.permissions import admin_context, is_ansvarig
from duty_web.models import Person

from .conftest import bearer, login

SECRET = "test-secret"


@pytest.fixture
def sent(monkeypatch):
    mails = []
    monkeypatch.setattr("duty_web.notifications._send", lambda to, subject, body: mails.append((to, subject, body)))
    return mails


def _run(app, session_factory, actor, name, person_id, slug="f14-bla"):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, actor.id), slug)
    action = REGISTRY[name]
    with app.test_request_context():
        plan, token = dry_run(session, ctx, action, action.parse({"person_id": str(person_id)}, {}), secret_key=SECRET)
        apply_plan(session, ctx, token, secret_key=SECRET, via="webb")
    return plan


def test_an_ansvarig_makes_another_parent_ansvarig(app, session_factory, admins, sent):
    plan = _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["parent"].id)

    assert [c.summary for c in plan.changes] == ["Ny ansvarig: Björn Exempel"]
    assert "Björn Exempel får ett mail om det." in plan.warnings
    assert is_ansvarig(session_factory(), admins["parent"].id, admins["team"].id)
    (to, subject, body), = sent
    assert to == "bjorn@exempel.se" and "F14 Blå" in subject and "/admin/f14-bla/lag" in body


def test_a_leader_can_be_made_ansvarig_too(app, session_factory, admins, sent):
    _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["leader"].id)

    assert is_ansvarig(session_factory(), admins["leader"].id, admins["team"].id)


def test_only_people_on_the_teams_roster(app, session_factory, admins, sent):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["club"].id)

    assert caught.value.errors == {"person_id": "Personen finns inte i lagets lista"}
    assert sent == []


def test_someone_already_ansvarig_is_said_so(app, session_factory, admins, sent):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["ansvarig"].id)

    assert caught.value.errors == {"person_id": "Alva Exempel är redan ansvarig"}


def test_a_failed_mail_keeps_the_new_ansvarig(app, session_factory, admins, monkeypatch, caplog):
    def broken(to, subject, body):
        raise OSError("smtp nere")

    monkeypatch.setattr("duty_web.notifications._send", broken)
    with caplog.at_level(logging.ERROR, logger="duty_web.admin"):
        _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["parent"].id)

    assert is_ansvarig(session_factory(), admins["parent"].id, admins["team"].id)
    assert "bjorn@exempel.se" not in caplog.text


def test_the_last_ansvarig_stays_unless_a_club_admin_removes_her(app, session_factory, admins):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "team_admins.remove", admins["ansvarig"].id)
    assert "minst en ansvarig" in caught.value.errors["person_id"]

    plan = _run(app, session_factory, admins["club"], "team_admins.remove", admins["ansvarig"].id)

    assert [c.summary for c in plan.changes] == ["Inte längre ansvarig: Alva Exempel"]
    assert not is_ansvarig(session_factory(), admins["ansvarig"].id, admins["team"].id)


def test_an_ansvarig_removes_another_when_one_remains(app, session_factory, admins, sent):
    _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["parent"].id)

    _run(app, session_factory, admins["ansvarig"], "team_admins.remove", admins["parent"].id)

    assert not is_ansvarig(session_factory(), admins["parent"].id, admins["team"].id)


def test_a_new_ansvarig_has_the_workbench_at_once(app, client, session_factory, admins, sent):
    _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["parent"].id)
    login(client, admins["parent"])

    assert client.get("/admin/f14-bla/lag").status_code == 200


def test_another_teams_ansvariga_are_out_of_reach(client, admins, api_keys):
    response = client.post("/admin/api/team_admins.add", json={"team": "p13", "person_id": admins["parent"].id},
                           headers=bearer(api_keys["ansvarig"]))

    assert response.status_code == 404


def test_the_team_page_offers_both_buttons(client, admins):
    login(client, admins["ansvarig"])

    body = client.get("/admin/f14-bla/lag").get_data(as_text=True)

    assert "Gör till ansvarig" in body and "Ta bort som ansvarig" in body


def test_a_refused_recipient_leaves_no_address_in_the_log(app, session_factory, admins, monkeypatch, caplog):
    def refused(to, subject, body):
        raise smtplib.SMTPRecipientsRefused({"bjorn@exempel.se": (550, b"x")})

    monkeypatch.setattr("duty_web.notifications._send", refused)
    with caplog.at_level(logging.DEBUG):
        _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["parent"].id)

    assert is_ansvarig(session_factory(), admins["parent"].id, admins["team"].id)
    assert "efter genomförande" in caplog.text
    assert "@" not in caplog.text


def test_a_replayed_add_is_stale_and_sends_no_second_mail(app, session_factory, admins, sent):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    action = REGISTRY["team_admins.add"]
    with app.test_request_context():
        _, token = dry_run(session, ctx, action, action.parse({"person_id": str(admins["parent"].id)}, {}), secret_key=SECRET)
        apply_plan(session, ctx, token, secret_key=SECRET, via="webb")
        with pytest.raises(PlanTokenError) as caught:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert caught.value.status == 409
    assert len(sent) == 1


def test_removing_someone_who_is_not_ansvarig(app, session_factory, admins):
    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], "team_admins.remove", admins["parent"].id)

    assert caught.value.errors == {"person_id": "Personen är inte ansvarig i laget"}


def test_removing_yourself_warns_and_sends_you_home(app, client, session_factory, admins, sent, csrf_token):
    _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["parent"].id)
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["parent"].id), "f14-bla")
    action = REGISTRY["team_admins.remove"]
    with app.test_request_context():
        plan, token = dry_run(session, ctx, action, action.parse({"person_id": str(admins["parent"].id)}, {}),
                              secret_key=app.config["SECRET_KEY"])
    assert "Du förlorar åtkomst till laget." in plan.warnings

    login(client, admins["parent"])
    response = client.post("/admin/f14-bla/genomfor", data={"csrf_token": csrf_token(), "plan_token": token})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/")
    assert not is_ansvarig(session_factory(), admins["parent"].id, admins["team"].id)


def test_removing_another_does_not_warn(app, session_factory, admins, sent):
    _run(app, session_factory, admins["ansvarig"], "team_admins.add", admins["parent"].id)

    plan = _run(app, session_factory, admins["ansvarig"], "team_admins.remove", admins["parent"].id)

    assert plan.warnings == []
