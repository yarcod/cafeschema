"""Klubbadmin sees every team; an ansvarig only her own; a leader none."""

from duty_web.admin.permissions import (
    admin_context,
    admin_teams,
    is_ansvarig,
    seed_club_admins,
    team_roster,
)
from duty_web.config import load_config
from duty_web.models import ClubAdmin, Person


def test_an_ansvarig_administers_her_team_only(session_factory, admins):
    session = session_factory()
    person = admins["ansvarig"]

    assert [t.slug for t in admin_teams(session, person.id)] == ["f14-bla"]
    ctx = admin_context(session, person, "f14-bla")
    assert ctx.team.slug == "f14-bla" and ctx.is_club_admin is False
    # She has a child on P13, but that makes her a parent there, not an admin.
    assert admin_context(session, person, "p13") is None
    assert is_ansvarig(session, person.id, admins["team"].id)


def test_a_club_admin_administers_every_team(session_factory, admins):
    session = session_factory()
    club = admins["club"]

    assert [t.name for t in admin_teams(session, club.id)] == ["F14 Blå", "P13"]
    assert admin_context(session, club, "p13").is_club_admin is True
    assert admin_context(session, club, None).team is None


def test_a_leader_or_a_parent_administers_nothing(session_factory, admins):
    session = session_factory()

    assert admin_context(session, admins["leader"], None) is None
    assert admin_context(session, admins["parent"], "f14-bla") is None
    assert admin_context(session, None, "f14-bla") is None


def test_an_unknown_team_is_simply_not_there(session_factory, admins):
    assert admin_context(session_factory(), admins["club"], "finns-inte") is None


def test_the_roster_is_the_teams_parents(session_factory, admins):
    names = [p.name for p in team_roster(session_factory(), admins["team"])]

    assert names == ["Alva Exempel", "Björn Exempel", "Maja Granne"]


def test_club_admins_are_seeded_only_into_an_empty_table(session_factory, seeded):
    session = session_factory()

    skipped = seed_club_admins(session, ["tova@exempel.se", "okand@exempel.se"])

    assert skipped == ["okand@exempel.se"]
    assert [a.person.email for a in session.query(ClubAdmin)] == ["tova@exempel.se"]
    # Once someone is club admin, the setting can no longer add anyone.
    assert seed_club_admins(session, ["bjorn@exempel.se"]) == []
    assert session.query(ClubAdmin).count() == 1


def test_club_admins_are_read_from_the_environment(monkeypatch):
    for name, value in {
        "SECRET_KEY": "s", "SCHEDULE_API_KEY": "k", "SMTP_HOST": "h", "SMTP_USER": "u",
        "SMTP_PASSWORD": "p", "FROM_ADDRESS": "f@exempel.se",
        "CLUB_ADMINS": " Dan@Exempel.se ,, bo@exempel.se",
    }.items():
        monkeypatch.setenv(name, value)

    assert load_config().club_admins == ("dan@exempel.se", "bo@exempel.se")
