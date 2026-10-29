import hashlib
import os

import pytest
from itsdangerous import URLSafeTimedSerializer

from duty_web.app import create_app
from duty_web.config import AppConfig
from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import (
    ROLE_ANSVARIG,
    ROLE_LEADER,
    ClubAdmin,
    Person,
    Player,
    Slot,
    Team,
    TeamRole,
)


def make_family(session, team, player_name, parents):
    """A player and the parents who may act for them.

    Duties belong to the player, so a test that needs an owner needs a
    family: (name, email) pairs, in the order the roster lists them.
    """
    player = Player(name=player_name, team_id=team.id)
    people = [Person(name=name, email=email) for name, email in parents]
    player.parents = people
    session.add(player)
    session.add_all(people)
    session.flush()
    return player


def link_instruction(session, slot, venue, station, document):
    """Place a slot at a station that links an instruction, the way an
    ansvarig does under Platser. The station alone decides the link."""
    from duty_web.places import find_or_create_station

    row = find_or_create_station(session, venue, station)
    row.document = document
    slot.station_id = row.id
    return row


@pytest.fixture
def documents_dir(tmp_path):
    """The instructions as they sit on the Fly volume.

    Named like the club's real ones, so a test can tell a real link to an
    instruction apart from the fallback to the document index.
    """
    directory = tmp_path / "dokument"
    directory.mkdir()
    for name in (
        "Cafeteria_Instruktion_MIH.pdf",
        "Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf",
    ):
        (directory / name).write_bytes(b"%PDF-1.4 fake")
    return directory


@pytest.fixture
def app(documents_dir):
    config = AppConfig(
        secret_key="test-secret",
        db_path=":memory:",
        api_key="test-api-key",
        smtp_host="localhost", smtp_port=587, smtp_user="u",
        smtp_password="p", from_address="noreply@exempel.se",
        documents_dir=str(documents_dir),
    )
    application = create_app(config)
    application.config.update(TESTING=True)
    yield application


@pytest.fixture
def session_factory(app):
    return app.extensions["duty_web_session_factory"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded(session_factory):
    """A team, a player with two parents, and two of the player's slots.

    The 'slot' is a historical slot (Jan 16, 2026) for team-calendar tests.
    The 'future_slot' is always 30 days in the future for personal view tests.
    'person' is the first parent; 'other_parent' is the second, and must see
    exactly the same slots.
    """
    session = session_factory()
    team = Team(name="F14 Blå", slug="f14-bla", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    player = make_family(
        session,
        team,
        "Tova Exempel",
        [("Alva Exempel", "tova@exempel.se"), ("Björn Exempel", "bjorn@exempel.se")],
    )
    person, other_parent = player.parents
    from datetime import date, time

    slot = Slot(
        team_id=team.id, date=date(2026, 1, 16), start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", child_name=player.name, player_id=player.id,
    )
    # Compute the next 16th of any month (ensures test assertion "16" in response works forever)
    today = date.today()
    if today.day < 16:
        future_slot_date = date(today.year, today.month, 16)
    else:
        # Next month's 16th
        if today.month == 12:
            future_slot_date = date(today.year + 1, 1, 16)
        else:
            future_slot_date = date(today.year, today.month + 1, 16)

    future_slot = Slot(
        team_id=team.id, date=future_slot_date, start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", child_name=player.name, player_id=player.id,
    )
    session.add_all([slot, future_slot])
    session.commit()
    return {
        "team": team,
        "player": player,
        "person": person,
        "other_parent": other_parent,
        "slot": slot,
        "future_slot": future_slot,
    }


@pytest.fixture
def second_team(session_factory, seeded):
    """A second team in the same club, P13, on the same future day.

    The seeded first parent (only them, not their partner) has a child here
    too, as a parent with children on two teams would. Nora's family is on
    P13 only, and Maja's is a neighbour on the seeded team, so both teams
    have someone to trade with.
    """
    from datetime import time

    session = session_factory()
    team = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    session.add(team)
    session.flush()
    sibling = Player(name="Elsa Exempel", team_id=team.id)
    sibling.parents = [session.get(Person, seeded["person"].id)]
    session.add(sibling)
    stranger = make_family(
        session, team, "Nils Främling", [("Nora Främling", "nora@exempel.se")]
    )
    neighbour = make_family(
        session, session.get(Team, seeded["team"].id), "Moa Granne",
        [("Maja Granne", "maja@exempel.se")],
    )
    day = seeded["future_slot"].date

    def slot(team_id, player, start, duty_name, venue):
        return Slot(
            team_id=team_id, date=day, start_time=time(start, 0),
            end_time=time(start + 2, 0), station="Kiosk", duty_name=duty_name,
            venue=venue, child_name=player.name, player_id=player.id,
        )

    sibling_slot = slot(team.id, sibling, 9, "Kioskpass", "Mölnlycke idrottshall")
    stranger_slot = slot(team.id, stranger, 13, "Kioskpass", "Mölnlycke idrottshall")
    neighbour_slot = slot(
        seeded["team"].id, neighbour, 13, "Arena värdskap", "Wallenstam arena"
    )
    session.add_all([sibling_slot, stranger_slot, neighbour_slot])
    session.commit()
    return {
        "team": team,
        "sibling": sibling,
        "sibling_slot": sibling_slot,
        "stranger": stranger,
        "stranger_slot": stranger_slot,
        "neighbour_slot": neighbour_slot,
    }


@pytest.fixture
def csrf_token(client, app):
    """Generate a CSRF token bound to the client's session cookie.

    Flask-WTF's own ``generate_csrf()`` reads/writes ``flask.session``,
    which is only bound to a live request context — but
    ``client.session_transaction()``'s ``sess`` object is a separate,
    directly-addressable view of the same session store used *outside*
    any request context. So this replicates generate_csrf()'s algorithm
    (see flask_wtf.csrf.generate_csrf) against that `sess` object instead:
    same session field ("csrf_token"), same signing scheme
    (URLSafeTimedSerializer with the app's secret key and the
    "wtf-csrf-token" salt) — producing a token the real CSRFProtect
    middleware validates identically to one a rendered form would carry.
    """
    def make_token():
        with client.session_transaction() as sess:
            if "csrf_token" not in sess:
                sess["csrf_token"] = hashlib.sha1(os.urandom(64)).hexdigest()
            serializer = URLSafeTimedSerializer(app.secret_key, salt="wtf-csrf-token")
            return serializer.dumps(sess["csrf_token"])

    return make_token


def login(client, person):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True


@pytest.fixture
def admins(session_factory, seeded, second_team):
    """Who may administer what, on top of the seeded club.

    'ansvarig' is the seeded first parent: ansvarig in the seeded team only,
    though she also has a child on P13 (second_team). 'club' is a club admin
    with no children at all. 'leader' is Maja Granne, a parent on the seeded
    team and one of its leaders, which gives no admin rights. 'parent' is
    the seeded second parent, with no role.
    """
    session = session_factory()
    team = session.get(Team, seeded["team"].id)
    ansvarig = session.get(Person, seeded["person"].id)
    leader = session.query(Person).filter_by(email="maja@exempel.se").one()
    club = Person(name="Klubb Admin", email="klubb@exempel.se")
    session.add(club)
    session.flush()
    session.add_all([
        ClubAdmin(person_id=club.id),
        TeamRole(person_id=ansvarig.id, team_id=team.id, role=ROLE_ANSVARIG),
        TeamRole(person_id=leader.id, team_id=team.id, role=ROLE_LEADER),
    ])
    session.commit()
    return {
        "ansvarig": ansvarig,
        "club": club,
        "leader": leader,
        "parent": session.get(Person, seeded["other_parent"].id),
        "team": team,
        "other_team": session.get(Team, second_team["team"].id),
    }


@pytest.fixture
def echo(app):
    """Registers the test-only tests.echo action and its form template."""
    from jinja2 import ChoiceLoader, DictLoader

    from duty_web.admin.actions import REGISTRY

    from .admin_echo import ECHO_TEMPLATE, EchoAction

    action = EchoAction()
    REGISTRY[action.name] = action
    original = app.jinja_env.loader
    app.jinja_env.loader = ChoiceLoader(
        [DictLoader({action.template: ECHO_TEMPLATE}), original]
    )
    yield action
    REGISTRY.pop(action.name, None)
    app.jinja_env.loader = original


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """The limiters are process-wide; ids restart in every test database."""
    from duty_web.admin import ratelimit

    ratelimit.api_limiter.reset()
    ratelimit.admin_limiter.reset()


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def api_keys(session_factory, admins):
    from duty_web.admin.api_keys import create_api_key

    session = session_factory()
    return {
        who: create_api_key(session, admins[who].id)[1]
        for who in ("ansvarig", "club", "parent")
    }
