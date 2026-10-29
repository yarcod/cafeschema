"""Who owns what: a slot resolves to the whole household of its player."""

from datetime import date, time

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Player, Slot, Team, parent_players
from duty_web.roster import (
    notifiable_emails,
    parent_names_of_slot,
    parent_owns_slot,
    parents_of_player,
    player_ids_for_parent,
    teams_for_parent,
)


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def with_team(session):
    team = Team(name="F14 Blå", slug="f14-bla", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    return team


def add_family(session, team, child, parents):
    """A player and their parents, linked: what roster.import would leave behind."""
    player = Player(name=child, team_id=team.id)
    session.add(player)
    session.flush()
    for name, email in parents:
        person = session.query(Person).filter_by(email=email).one_or_none()
        if person is None:
            person = Person(name=name, email=email)
            session.add(person)
            session.flush()
        session.execute(parent_players.insert().values(person_id=person.id, player_id=player.id))
    session.commit()
    return player


def test_ownership_helpers_resolve_a_slot_to_the_whole_household():
    session = make_session()
    team = with_team(session)
    klara = add_family(session, team, "Klara Ahlqvist", [
        ("Hans Ahlqvist", "hans@exempel.se"),
        ("Lena Ahlqvist", "lena@exempel.se"),
    ])
    add_family(session, team, "Bea Bengtsson", [("Bo Bengtsson", "bo@exempel.se")])
    hans, lena = klara.parents
    bo = session.query(Person).filter_by(email="bo@exempel.se").one()

    slot = Slot(
        team_id=team.id, date=date(2026, 1, 16), start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", player_id=klara.id,
    )
    session.add(slot)
    session.commit()

    assert player_ids_for_parent(session, hans.id) == {klara.id}
    assert player_ids_for_parent(session, lena.id) == {klara.id}
    assert [p.id for p in parents_of_player(session, klara.id)] == [hans.id, lena.id]
    assert parent_owns_slot(session, hans.id, slot)
    assert parent_owns_slot(session, lena.id, slot)
    assert not parent_owns_slot(session, bo.id, slot)
    assert parent_names_of_slot(session, slot) == "Hans Ahlqvist & Lena Ahlqvist"
    assert notifiable_emails(session, slot) == ["hans@exempel.se", "lena@exempel.se"]


def test_nobody_owns_an_unassigned_slot():
    session = make_session()
    team = with_team(session)
    add_family(session, team, "Klara Ahlqvist", [("Hans Ahlqvist", "hans@exempel.se")])
    hans = session.query(Person).filter_by(email="hans@exempel.se").one()

    unassigned = Slot(
        team_id=team.id, date=date(2026, 1, 16), start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", player_id=None,
    )
    session.add(unassigned)
    session.commit()

    assert not parent_owns_slot(session, hans.id, unassigned)
    assert not parent_owns_slot(session, hans.id, None)
    assert notifiable_emails(session, unassigned) == []
    assert parent_names_of_slot(session, unassigned) == "Ledigt"


def test_teams_for_parent_lists_each_of_their_teams_once():
    session = make_session()
    f15 = Team(name="F15", slug="f15", venue="Wallenstam arena")
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    f11 = Team(name="F11", slug="f11", venue="Wallenstam arena")
    session.add_all([f15, p13, f11])
    session.commit()
    # Two siblings on F15, one child on P13, nobody on F11.
    add_family(session, f15, "Klara Edh", [("Dan Edh", "dan@exempel.se")])
    add_family(session, f15, "Moa Edh", [("Dan Edh", "dan@exempel.se")])
    add_family(session, p13, "Elsa Edh", [("Dan Edh", "dan@exempel.se")])
    dan = session.query(Person).filter_by(email="dan@exempel.se").one()

    assert [team.slug for team in teams_for_parent(session, dan.id)] == ["f15", "p13"]


def test_teams_for_parent_is_empty_for_someone_with_no_child_on_file():
    session = make_session()
    with_team(session)
    outsider = Person(name="Ola", email="ola@exempel.se")
    session.add(outsider)
    session.commit()

    assert teams_for_parent(session, outsider.id) == []
