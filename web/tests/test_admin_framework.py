"""Plan, then apply exactly what was planned — or refuse."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

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


def test_notes_are_public_and_part_of_the_digest():
    noted, plain = Plan(), Plan()
    noted.notes.append("Tova Exempel: 1 → 2 pass")

    assert noted.public()["notes"] == ["Tova Exempel: 1 → 2 pass"]
    assert noted.digest() != plain.digest()


def test_the_basis_is_in_the_digest_and_never_public():
    before, after = Plan(), Plan()
    before.basis.append([7, None, date(2026, 12, 12), "gammal anteckning"])
    after.basis.append([7, None, date(2026, 12, 12), "ny anteckning"])

    assert before.digest() != after.digest()
    assert "basis" not in before.public()
    assert "gammal anteckning" not in str(before.public())


def test_questions_and_preview_days_stay_out_of_the_digest():
    asking, plain = Plan(), Plan()
    asking.preview_dates.add(date(2026, 12, 12))
    asking.questions.append({"field": "name::Elin", "label": "Vem är 'Elin'?", "choices": [[1, "Elin E"]]})

    assert asking.digest() == plain.digest()
    assert asking.public()["questions"] == [
        {"field": "name::Elin", "label": "Vem är 'Elin'?", "choices": [[1, "Elin E"]]}
    ]
    assert "preview_dates" not in asking.public()


def test_a_preview_sees_the_planned_state_and_writes_nothing(session_factory, seeded):
    from duty_web.admin.executor import preview

    session = session_factory()
    plan = Plan()
    plan.preview_dates.add(date(2026, 12, 12))
    plan.writes.append(lambda s: s.add(Venue(name="Hindås", name_key="hindas")))

    seen = preview(session, plan, lambda s: s.query(Venue).count())

    assert seen == 1
    assert session.query(Venue).count() == 0
    assert session_factory().query(Venue).count() == 0


def test_a_preview_that_fails_is_left_out(session_factory, seeded, caplog):
    from duty_web.admin.executor import preview

    plan = Plan()
    plan.preview_dates.add(date(2026, 12, 12))
    plan.writes.append(lambda s: 1 / 0)

    assert preview(session_factory(), plan, lambda s: "html") is None
    assert "ZeroDivisionError" in caplog.text


@pytest.mark.parametrize("write", [
    lambda s: s.add(Venue(name="Hindås", name_key="hindas")),
    lambda s: 1 / 0,
])
def test_a_preview_never_runs_the_after_commit_steps(session_factory, seeded, write):
    from duty_web.admin.executor import preview

    sent = []
    plan = Plan()
    plan.preview_dates.add(date(2026, 12, 12))
    plan.writes.append(write)
    plan.after_commit.append(lambda: sent.append("mail"))

    preview(session_factory(), plan, lambda s: "html")

    assert sent == []


def test_apply_holds_the_write_lock_while_it_replans(tmp_path, monkeypatch):
    import sqlite3

    from duty_web.admin.actions import REGISTRY
    from duty_web.db import init_db, make_engine, make_session_factory
    from duty_web.models import ClubAdmin, Team

    from .admin_echo import EchoAction, EchoInput

    db = tmp_path / "duty.db"
    engine = make_engine(str(db))
    init_db(engine)
    session = make_session_factory(engine)()
    team, person = Team(name="F14 Blå", slug="f14-bla", venue="X"), Person(name="K", email="k@exempel.se")
    session.add_all([team, person])
    session.flush()
    session.add(ClubAdmin(person_id=person.id))
    session.commit()
    action = EchoAction()
    monkeypatch.setitem(REGISTRY, action.name, action)
    ctx = admin_context(session, person, "f14-bla")
    _, token = dry_run(session, ctx, action, EchoInput(text="Hindås"), secret_key="s")
    seen = []
    real = EchoAction.plan

    def planning(self, s, c, i):
        # Another writer, mid-apply: it must find the database locked.
        other = sqlite3.connect(db, timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            seen.append("free")
        except sqlite3.OperationalError:
            seen.append("locked")
        finally:
            other.close()
        return real(self, s, c, i)

    monkeypatch.setattr(EchoAction, "plan", planning)
    apply_plan(session, ctx, token, secret_key="s", via="test")

    assert seen == ["locked"]
    assert make_session_factory(engine)().query(Venue).count() == 1


def test_a_busy_database_is_a_conflict_with_nothing_written(
    session_factory, admins, echo, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    from duty_web.admin import executor

    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    plan, token = dry_run(session, ctx, echo, echo.parse({"text": "Hindås", "count": "1"}, {}),
                          secret_key="test-secret")
    sent = []
    monkeypatch.setattr(type(echo), "plan", lambda self, s, c, i: _with_mail(plan, sent))

    def busy(s):
        raise OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked"))

    monkeypatch.setattr(executor, "_write_lock", busy)

    with pytest.raises(PlanTokenError) as caught:
        apply_plan(session, ctx, token, secret_key="test-secret", via="test")

    assert (caught.value.status, caught.value.code) == (409, "busy")
    assert session_factory().query(Venue).count() == 0 and sent == []


def _with_mail(plan, sent):
    plan.after_commit.append(lambda: sent.append("mail"))
    return plan


def test_no_preview_without_days_to_show(session_factory):
    from duty_web.admin.executor import preview

    plan = Plan()
    plan.writes.append(lambda s: s.add(Venue(name="Hindås", name_key="hindas")))

    assert preview(session_factory(), plan, lambda s: "html") is None
