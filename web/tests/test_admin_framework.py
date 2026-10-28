"""Plan, then apply exactly what was planned — or refuse."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pytest
from werkzeug.datastructures import MultiDict

from duty_web.admin.actions.base import Plan, ValidationError, fields_schema, parse_input
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.permissions import admin_context
from duty_web.admin.plan_tokens import PlanTokenError, read_plan_token
from duty_web.models import Person, Venue

from .admin_echo import EchoInput

SECRET = "test-secret"


@dataclass
class Sample:
    name: str
    count: int
    flag: bool = False
    ids: list[int] = field(default_factory=list)
    note: str | None = None


def _ctx(session, person, slug):
    return admin_context(session, session.get(Person, person.id), slug)


def test_parse_input_converts_form_strings():
    form = MultiDict([("name", " Kiosk "), ("count", "3"), ("flag", "on"), ("ids", "1"), ("ids", "2")])

    assert parse_input(Sample, form) == Sample("Kiosk", 3, True, [1, 2], None)


def test_parse_input_reads_json_values():
    assert parse_input(Sample, {"name": "a", "count": 2, "ids": [4]}) == Sample("a", 2, False, [4], None)


def test_parse_input_reports_every_bad_field_at_once():
    with pytest.raises(ValidationError) as caught:
        parse_input(Sample, {"count": "tre"})

    assert caught.value.errors == {"name": "Saknas", "count": "Måste vara ett heltal"}


def test_a_bool_is_not_an_integer():
    with pytest.raises(ValidationError):
        parse_input(Sample, {"name": "a", "count": True})


def test_fields_schema_describes_inputs_for_agents():
    assert fields_schema(Sample) == {
        "name": "string", "count": "integer", "flag": "boolean?",
        "ids": "integer[]?", "note": "string?",
    }


def test_the_digest_covers_changes_and_warnings_but_not_writes():
    first, second = Plan(), Plan()
    for plan in (first, second):
        plan.add("add", "venue", "Hindås")
    second.writes.append(lambda s: None)

    assert first.digest() == second.digest()
    second.warnings.append("obs")
    assert first.digest() != second.digest()
    assert first.public()["counts"] == {"add": 1, "update": 0, "delete": 0}


def test_dry_run_writes_nothing(session_factory, admins, echo):
    session = session_factory()
    ctx = _ctx(session, admins["ansvarig"], "f14-bla")

    plan, token = dry_run(session, ctx, echo, EchoInput("Hindås", 2), secret_key=SECRET)

    assert [c.summary for c in plan.changes] == ["Hindås 0", "Hindås 1"]
    assert token and session.query(Venue).count() == 0


def test_apply_writes_exactly_the_plan(session_factory, admins, echo):
    session = session_factory()
    ctx = _ctx(session, admins["ansvarig"], "f14-bla")
    _, token = dry_run(session, ctx, echo, EchoInput("Hindås"), secret_key=SECRET)

    action, plan = apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert action is echo and len(plan.changes) == 1
    assert [v.name for v in session_factory().query(Venue)] == ["Hindås"]


def test_a_replayed_token_is_stale(session_factory, admins, echo):
    session = session_factory()
    ctx = _ctx(session, admins["ansvarig"], "f14-bla")
    _, token = dry_run(session, ctx, echo, EchoInput("Hindås"), secret_key=SECRET)
    apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    with pytest.raises(PlanTokenError) as caught:
        apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert (caught.value.status, caught.value.code) == (409, "stale_plan")
    assert session.query(Venue).count() == 1


def test_data_changed_since_review_is_stale(session_factory, admins, echo):
    session = session_factory()
    ctx = _ctx(session, admins["ansvarig"], "f14-bla")
    _, token = dry_run(session, ctx, echo, EchoInput("Hindås"), secret_key=SECRET)
    session.add(Venue(name="hindås", name_key="hindas"))
    session.commit()

    with pytest.raises(PlanTokenError) as caught:
        apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert caught.value.status == 409
    assert session.query(Venue).count() == 1


def test_a_tampered_token_is_refused(session_factory, admins, echo):
    session = session_factory()
    ctx = _ctx(session, admins["ansvarig"], "f14-bla")
    _, token = dry_run(session, ctx, echo, EchoInput("Hindås"), secret_key=SECRET)

    with pytest.raises(PlanTokenError) as caught:
        apply_plan(session, ctx, token[:-4] + "AAAA", secret_key=SECRET, via="webb")

    assert (caught.value.status, caught.value.code) == (400, "tampered_plan")


def test_an_expired_token_is_stale(session_factory, admins, echo):
    session = session_factory()
    ctx = _ctx(session, admins["ansvarig"], "f14-bla")
    _, token = dry_run(session, ctx, echo, EchoInput("Hindås"), secret_key=SECRET)

    with pytest.raises(PlanTokenError) as caught:
        read_plan_token(SECRET, token, max_age=-1)

    assert caught.value.status == 409


def test_a_token_belongs_to_its_person_team_and_action(session_factory, admins, echo):
    session = session_factory()
    mine = _ctx(session, admins["ansvarig"], "f14-bla")
    _, token = dry_run(session, mine, echo, EchoInput("Hindås"), secret_key=SECRET)

    for ctx, expect in (
        (_ctx(session, admins["club"], "f14-bla"), None),   # someone else
        (_ctx(session, admins["club"], "p13"), None),       # another team
        (mine, "venue.create"),                              # another action's URL
    ):
        with pytest.raises(PlanTokenError) as caught:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb", expect=expect)
        assert caught.value.status == 404
    assert session.query(Venue).count() == 0


def test_a_failing_mail_after_commit_keeps_the_change(session_factory, admins, echo, caplog):
    session = session_factory()
    ctx = _ctx(session, admins["ansvarig"], "f14-bla")
    _, token = dry_run(session, ctx, echo, EchoInput("mailtest"), secret_key=SECRET)

    with caplog.at_level(logging.ERROR, logger="duty_web.admin"):
        apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert session_factory().query(Venue).count() == 1
    assert "efter genomförande" in caplog.text


def test_a_write_that_loses_a_race_is_a_409_and_leaks_no_values(
    session_factory, admins, echo, caplog
):
    session = session_factory()
    ctx = _ctx(session, admins["ansvarig"], "f14-bla")
    _, token = dry_run(session, ctx, echo, EchoInput("race@exempel.se"), secret_key=SECRET)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(PlanTokenError) as caught:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert (caught.value.status, caught.value.code) == (409, "conflict")
    assert session.query(Venue).count() == 0
    assert "@" not in caplog.text and "exempel" not in caplog.text


def test_database_errors_carry_no_bound_parameters(session_factory):
    from sqlalchemy.exc import IntegrityError

    session = session_factory()
    session.add_all([
        Venue(name="Hemlig@exempel.se", name_key="k"),
        Venue(name="Hemlig@exempel.se", name_key="k"),
    ])
    with pytest.raises(IntegrityError) as caught:
        session.flush()
    assert "exempel" not in str(caught.value) and "@" not in str(caught.value)


def test_a_lost_race_is_logged_without_values(session_factory, admins, echo, caplog):
    session = session_factory()
    ctx = _ctx(session, admins["ansvarig"], "f14-bla")
    _, token = dry_run(session, ctx, echo, EchoInput("race@exempel.se"), secret_key=SECRET)

    with caplog.at_level(logging.WARNING, logger="duty_web.admin"):
        with pytest.raises(PlanTokenError):
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert "Krock vid genomförande: tests.echo" in caplog.text
    assert "UNIQUE" in caplog.text
    assert "@" not in caplog.text and "exempel" not in caplog.text
