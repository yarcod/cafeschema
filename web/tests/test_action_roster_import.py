"""roster.import: the team's people from 360Player, leaders' families exempt."""

import io
import json
import re

from duty_web.admin.actions import REGISTRY
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.permissions import admin_context
from duty_web.models import ROLE_LEADER, Person, Player, TeamRole

from .conftest import bearer, login
from .test_members_csv import export

SECRET = "test-secret"

# The seeded team as 360Player lists it: Tova (Alva, Björn), Moa (Maja), and
# Maja's second membership as a leader.
TEAM = [
    "Tova,Exempel,,,Alva Exempel;Björn Exempel,",
    "Alva,Exempel,tova@exempel.se,,,Tova Exempel",
    "Björn,Exempel,bjorn@exempel.se,,,Tova Exempel",
    "Moa,Granne,,,Maja Granne,",
    "Maja,Granne,maja@exempel.se,,,Moa Granne",
    "Maja,Granne,maja@exempel.se,,,",
]


def _plan(session_factory, admins, *rows):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    action = REGISTRY["roster.import"]
    inp = action.parse({"csv": export(*rows).decode("utf-8")}, {})
    plan, token = dry_run(session, ctx, action, inp, secret_key=SECRET)
    return session, ctx, plan, token


def _apply(session_factory, admins, *rows):
    session, ctx, plan, token = _plan(session_factory, admins, *rows)
    apply_plan(session, ctx, token, secret_key=SECRET, via="webb")
    return plan


def _summaries(plan):
    return [c.summary for c in plan.changes]


def _player(session_factory, name):
    return session_factory().query(Player).filter_by(name=name).one()


def test_the_leaders_child_stops_staffing_shifts(session_factory, admins):
    plan = _apply(session_factory, admins, *TEAM)

    # Maja is already marked leader by the fixture; what changes is Moa.
    assert _summaries(plan) == ["Står inte på pass: Moa Granne"]
    assert _player(session_factory, "Moa Granne").exempt is True
    assert _player(session_factory, "Tova Exempel").exempt is False


def test_importing_the_same_export_twice_changes_nothing(session_factory, admins):
    _apply(session_factory, admins, *TEAM)

    _, _, plan, _ = _plan(session_factory, admins, *TEAM)

    assert plan.changes == []


def test_a_new_family_comes_with_its_link(session_factory, admins):
    plan = _apply(session_factory, admins, *TEAM, "Ida,Ny,,,Kim Ny,", "Kim,Ny,kim@exempel.se,,,Ida Ny")

    assert "Ny spelare: Ida Ny" in _summaries(plan)
    assert any(c.summary == "Ny vårdnadshavare: Kim Ny" and c.detail == "till Ida Ny" for c in plan.changes)
    kim = session_factory().query(Person).filter_by(email="kim@exempel.se").one()
    assert [p.name for p in kim.players] == ["Ida Ny"]


def test_an_address_in_other_case_or_with_spaces_is_the_same_parent(session_factory, admins):
    rows = [r.replace("tova@exempel.se", " TOVA@Exempel.se ") for r in TEAM]

    plan = _apply(session_factory, admins, *rows)

    assert not any(c.kind == "parent" for c in plan.changes)
    assert session_factory().query(Person).filter(Person.email.like("%tova%")).count() == 1


def test_a_guardian_with_two_children_is_one_person_with_two_links(session_factory, admins):
    _apply(session_factory, admins, *TEAM,
           "Ida,Ny,,,Kim Ny,", "Ola,Ny,,,Kim Ny,",
           "Kim,Ny,kim@exempel.se,,,Ida Ny", "Kim,Ny,kim@exempel.se,,,Ola Ny")

    kims = session_factory().query(Person).filter_by(email="kim@exempel.se").all()
    assert len(kims) == 1
    assert sorted(p.name for p in kims[0].players) == ["Ida Ny", "Ola Ny"]


def test_a_leader_who_leaves_frees_the_family(session_factory, admins):
    _apply(session_factory, admins, *TEAM)

    plan = _apply(session_factory, admins, *TEAM[:-1])

    assert _summaries(plan) == ["Inte längre ledare: Maja Granne", "Står på pass igen: Moa Granne"]
    assert session_factory().query(TeamRole).filter_by(role=ROLE_LEADER).count() == 0
    assert _player(session_factory, "Moa Granne").exempt is False


def test_people_missing_from_the_export_are_kept_and_flagged(session_factory, admins):
    plan = _apply(session_factory, admins, *TEAM[:3])

    assert any("Moa Granne finns inte längre i 360Player (1 kommande pass)" in w for w in plan.warnings)
    assert _player(session_factory, "Moa Granne")  # still there


def test_an_exempt_player_with_upcoming_shifts_is_flagged(session_factory, admins):
    _, _, plan, _ = _plan(session_factory, admins, *TEAM)

    assert any("Moa Granne står inte på pass men har 1 kommande pass" in w for w in plan.warnings)


def test_guardians_without_email_are_counted(session_factory, admins):
    _, _, plan, _ = _plan(session_factory, admins, *TEAM, "Utan,Post,,,,Tova Exempel")

    assert any("1 vårdnadshavare saknar e-post" in w for w in plan.warnings)


def test_the_plan_never_contains_an_address(session_factory, admins):
    _, _, plan, _ = _plan(session_factory, admins, *TEAM, "Ida,Ny,,,Kim Ny,", "Kim,Ny,kim@exempel.se,,,Ida Ny")

    assert "@" not in json.dumps(plan.public(), ensure_ascii=False)


def test_both_doors_review_the_same_plan(client, admins, api_keys, csrf_token):
    data = export(*TEAM)
    api = client.post(
        "/admin/api/roster.import",
        data={"team": "f14-bla", "file": (io.BytesIO(data), "F14-members.csv")},
        headers=bearer(api_keys["ansvarig"]), content_type="multipart/form-data",
    ).get_json()
    login(client, admins["ansvarig"])
    page = client.post(
        "/admin/f14-bla/do/roster.import",
        data={"csrf_token": csrf_token(), "file": (io.BytesIO(data), "F14-members.csv")},
        content_type="multipart/form-data",
    ).get_data(as_text=True)

    assert [c["summary"] for c in api["changes"]] == ["Står inte på pass: Moa Granne"]
    assert "Står inte på pass: Moa Granne" in page
    assert re.search(r'name="plan_token" value="[^"]+"', page)


def test_a_wrong_file_stays_on_the_form(client, admins, csrf_token):
    login(client, admins["ansvarig"])

    response = client.post(
        "/admin/f14-bla/do/roster.import",
        data={"csrf_token": csrf_token(), "file": (io.BytesIO(b"x"), "lista.xlsx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400 and "CSV-filen" in response.get_data(as_text=True)


def test_the_team_page_offers_the_import_and_shows_exempt_children(client, admins, session_factory):
    _apply(session_factory, admins, *TEAM)
    login(client, admins["ansvarig"])

    body = client.get("/admin/f14-bla/lag").get_data(as_text=True)

    assert "Uppdatera från 360Player" in body and "(står inte på pass)" in body


def test_the_roster_read_shows_addresses_to_the_teams_admins_only(client, admins, api_keys):
    mine = client.get("/admin/api/roster?team=f14-bla", headers=bearer(api_keys["ansvarig"]))
    other = client.get("/admin/api/roster?team=p13", headers=bearer(api_keys["ansvarig"]))

    moa = next(p for p in mine.get_json()["players"] if p["name"] == "Moa Granne")
    assert moa["parents"] == [{"id": admins["leader"].id, "name": "Maja Granne",
                               "email": "maja@exempel.se", "roles": ["leader"]}]
    assert other.status_code == 404
