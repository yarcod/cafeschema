import pytest

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Team
from duty_web.teams import TeamError, create_team, team_by_slug, validate_slug


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def test_create_team_stores_name_slug_and_venue():
    session = make_session()

    team = create_team(session, name="  P13 ", slug="p13", venue="Mölnlycke idrottshall")

    assert (team.name, team.slug, team.venue) == ("P13", "p13", "Mölnlycke idrottshall")
    assert session.query(Team).count() == 1


@pytest.mark.parametrize("slug", ["", "F15", "f 15", "-f15", "f15/", "../x", "å13"])
def test_a_slug_that_isnt_folder_and_url_safe_is_refused(slug):
    with pytest.raises(TeamError):
        validate_slug(slug)


def test_a_second_team_with_the_same_slug_is_refused():
    session = make_session()
    create_team(session, name="F15", slug="f15", venue="Wallenstam arena")

    with pytest.raises(TeamError):
        create_team(session, name="F15 B", slug="f15", venue="Wallenstam arena")


def test_a_team_needs_a_name():
    with pytest.raises(TeamError):
        create_team(make_session(), name="  ", slug="f15", venue="Wallenstam arena")


def test_team_by_slug_names_the_known_teams_when_it_misses():
    session = make_session()
    create_team(session, name="F15", slug="f15", venue="Wallenstam arena")

    assert team_by_slug(session, "f15").name == "F15"
    with pytest.raises(TeamError, match="f15"):
        team_by_slug(session, "p13")
