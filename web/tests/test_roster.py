"""The roster import: additive, re-runnable, and the source of who owns what."""

import pytest

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Player, Slot, Team, parent_players
from duty_web.roster import (
    RosterError,
    import_roster,
    notifiable_emails,
    parent_names_of_slot,
    parent_owns_slot,
    parents_of_player,
    player_ids_for_parent,
    teams_for_parent,
)

ROSTER = [
    ("Hans Ahlqvist", "hans@exempel.se", "Klara Ahlqvist"),
    ("Lena Ahlqvist", "lena@exempel.se", "Klara Ahlqvist"),
    ("Bo Bengtsson", "bo@exempel.se", "Bea Bengtsson"),
]


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def write_csv(tmp_path, rows, name="roster.csv"):
    path = tmp_path / name
    lines = ["parent_name,parent_email,children_on_team"]
    lines += [",".join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def with_team(session):
    team = Team(name="F14 Blå", slug="f14-bla", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    return team


def test_import_creates_players_parents_and_the_links_between_them(tmp_path):
    session = make_session()
    team = with_team(session)

    result = import_roster(session, write_csv(tmp_path, ROSTER), team_id=team.id)

    assert (result.players_added, result.parents_added, result.links_added) == (2, 3, 3)
    klara = session.query(Player).filter_by(name="Klara Ahlqvist").one()
    assert [p.email for p in klara.parents] == ["hans@exempel.se", "lena@exempel.se"]


def test_running_it_twice_changes_nothing(tmp_path):
    """The property the 360Player sync depends on: re-running is a no-op."""
    session = make_session()
    team = with_team(session)
    path = write_csv(tmp_path, ROSTER)
    import_roster(session, path, team_id=team.id)
    before = (
        session.query(Player).count(),
        session.query(Person).count(),
        session.query(parent_players).count(),
    )

    result = import_roster(session, path, team_id=team.id)

    assert (result.players_added, result.parents_added, result.links_added) == (0, 0, 0)
    assert before == (
        session.query(Player).count(),
        session.query(Person).count(),
        session.query(parent_players).count(),
    )


def test_a_newly_registered_parent_is_added_without_touching_anyone_else(tmp_path):
    session = make_session()
    team = with_team(session)
    import_roster(session, write_csv(tmp_path, ROSTER), team_id=team.id)
    existing_ids = {p.email: p.id for p in session.query(Person).all()}

    result = import_roster(
        session,
        write_csv(
            tmp_path,
            ROSTER + [("Bodil Bengtsson", "bodil@exempel.se", "Bea Bengtsson")],
            name="senare.csv",
        ),
        team_id=team.id,
    )

    assert (result.players_added, result.parents_added, result.links_added) == (0, 1, 1)
    # Person ids are what a logged-in session stores — none of them may move.
    after = {p.email: p.id for p in session.query(Person).all()}
    assert after.items() >= existing_ids.items()
    assert after.keys() - existing_ids.keys() == {"bodil@exempel.se"}
    bea = session.query(Player).filter_by(name="Bea Bengtsson").one()
    assert len(bea.parents) == 2


def test_a_parent_dropped_from_the_export_is_kept(tmp_path):
    """Never-remove: a 360Player export that lost a row must not log someone
    out or orphan the slots their child holds."""
    session = make_session()
    team = with_team(session)
    import_roster(session, write_csv(tmp_path, ROSTER), team_id=team.id)

    import_roster(
        session, write_csv(tmp_path, ROSTER[:1], name="kortare.csv"), team_id=team.id
    )

    assert session.query(Person).count() == 3
    assert session.query(parent_players).count() == 3


def test_a_renamed_parent_keeps_their_id(tmp_path):
    session = make_session()
    team = with_team(session)
    import_roster(session, write_csv(tmp_path, ROSTER), team_id=team.id)
    hans_id = session.query(Person).filter_by(email="hans@exempel.se").one().id

    result = import_roster(
        session,
        write_csv(
            tmp_path,
            [("Hans Lindgren Ahlqvist", "hans@exempel.se", "Klara Ahlqvist")],
            name="nytt-namn.csv",
        ),
        team_id=team.id,
    )

    hans = session.query(Person).filter_by(email="hans@exempel.se").one()
    assert result.parents_renamed == 1
    assert hans.id == hans_id
    assert hans.name == "Hans Lindgren Ahlqvist"


def test_a_row_without_an_email_still_creates_the_player(tmp_path):
    session = make_session()
    team = with_team(session)

    result = import_roster(
        session,
        write_csv(tmp_path, [("Ingen Kontakt", "", "Nora Nilsson")]),
        team_id=team.id,
    )

    assert result.rows_without_email == 1
    assert session.query(Player).one().name == "Nora Nilsson"
    assert session.query(Person).count() == 0


def test_import_rejects_a_file_without_the_expected_columns(tmp_path):
    session = make_session()
    team = with_team(session)
    path = tmp_path / "fel.csv"
    path.write_text("namn,epost\nA,a@exempel.se\n", encoding="utf-8")

    with pytest.raises(RosterError, match="kolumn"):
        import_roster(session, path, team_id=team.id)


def test_import_rejects_a_nonexistent_team_id(tmp_path):
    session = make_session()
    team = with_team(session)

    with pytest.raises(RosterError, match="team_id"):
        import_roster(session, write_csv(tmp_path, ROSTER), team_id=team.id + 999)

    assert session.query(Player).count() == 0


def test_ownership_helpers_resolve_a_slot_to_the_whole_household(tmp_path):
    session = make_session()
    team = with_team(session)
    import_roster(session, write_csv(tmp_path, ROSTER), team_id=team.id)
    klara = session.query(Player).filter_by(name="Klara Ahlqvist").one()
    hans, lena = klara.parents
    bo = session.query(Person).filter_by(email="bo@exempel.se").one()
    from datetime import date, time

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


def test_nobody_owns_an_unassigned_slot(tmp_path):
    session = make_session()
    team = with_team(session)
    import_roster(session, write_csv(tmp_path, ROSTER), team_id=team.id)
    hans = session.query(Person).filter_by(email="hans@exempel.se").one()
    from datetime import date, time

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


def test_teams_for_parent_lists_each_of_their_teams_once(tmp_path):
    session = make_session()
    f15 = Team(name="F15", slug="f15", venue="Wallenstam arena")
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    f11 = Team(name="F11", slug="f11", venue="Wallenstam arena")
    session.add_all([f15, p13, f11])
    session.commit()
    # Two siblings on F15, one child on P13, nobody on F11.
    import_roster(session, write_csv(tmp_path, [
        ("Dan Edh", "dan@exempel.se", "Klara Edh"),
        ("Dan Edh", "dan@exempel.se", "Moa Edh"),
    ]), team_id=f15.id)
    import_roster(session, write_csv(tmp_path, [
        ("Dan Edh", "dan@exempel.se", "Elsa Edh"),
    ], name="p13.csv"), team_id=p13.id)
    dan = session.query(Person).filter_by(email="dan@exempel.se").one()

    assert [team.slug for team in teams_for_parent(session, dan.id)] == ["f15", "p13"]


def test_teams_for_parent_is_empty_for_someone_with_no_child_on_file():
    session = make_session()
    with_team(session)
    outsider = Person(name="Ola", email="ola@exempel.se")
    session.add(outsider)
    session.commit()

    assert teams_for_parent(session, outsider.id) == []
