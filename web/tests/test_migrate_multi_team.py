"""The one-off migration to multi-team: team slugs, per-team player names.

It lives in scripts/ (it is pushed to the Fly machine), but it is duty_web
code, so it is tested from here where duty_web is importable.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from duty_web.db import make_engine, make_session_factory
from duty_web.models import Player, Slot, SwapRequest, Team

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_multi_team.py"

OLD_SCHEMA = """
CREATE TABLE people ( id INTEGER NOT NULL, name VARCHAR NOT NULL, email VARCHAR NOT NULL, email_notifications BOOLEAN NOT NULL, PRIMARY KEY (id), UNIQUE (email) );
CREATE TABLE teams ( id INTEGER NOT NULL, name VARCHAR NOT NULL, venue VARCHAR NOT NULL, PRIMARY KEY (id) );
CREATE TABLE players ( id INTEGER NOT NULL, name VARCHAR NOT NULL, team_id INTEGER NOT NULL, PRIMARY KEY (id), UNIQUE (name), FOREIGN KEY(team_id) REFERENCES teams (id) );
CREATE TABLE parent_players ( person_id INTEGER NOT NULL, player_id INTEGER NOT NULL, PRIMARY KEY (person_id, player_id), FOREIGN KEY(person_id) REFERENCES people (id), FOREIGN KEY(player_id) REFERENCES players (id) );
CREATE TABLE slots ( id INTEGER NOT NULL, team_id INTEGER NOT NULL, date DATE, start_time TIME NOT NULL, end_time TIME NOT NULL, station VARCHAR NOT NULL, duty_name VARCHAR NOT NULL, venue VARCHAR NOT NULL, child_name VARCHAR, note VARCHAR, player_id INTEGER, PRIMARY KEY (id), FOREIGN KEY(team_id) REFERENCES teams (id), FOREIGN KEY(player_id) REFERENCES players (id) );
CREATE TABLE swap_requests ( id INTEGER NOT NULL, proposer_slot_id INTEGER NOT NULL, target_slot_id INTEGER NOT NULL, status VARCHAR NOT NULL, created_at DATETIME NOT NULL, resolved_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(proposer_slot_id) REFERENCES slots (id), FOREIGN KEY(target_slot_id) REFERENCES slots (id) );
CREATE TABLE login_tokens ( id INTEGER NOT NULL, token_hash VARCHAR NOT NULL, email VARCHAR NOT NULL, expires_at DATETIME NOT NULL, consumed_at DATETIME, PRIMARY KEY (id), UNIQUE (token_hash) );
"""

OLD_ROWS = """
INSERT INTO teams (id, name, venue) VALUES (1, 'F15', 'Wallenstam arena');
INSERT INTO people (id, name, email, email_notifications) VALUES (1, 'Dan', 'dan@exempel.se', 1);
INSERT INTO people (id, name, email, email_notifications) VALUES (2, 'Bo', 'bo@exempel.se', 1);
INSERT INTO players (id, name, team_id) VALUES (7, 'Klara', 1);
INSERT INTO players (id, name, team_id) VALUES (8, 'Bea', 1);
INSERT INTO parent_players (person_id, player_id) VALUES (1, 7);
INSERT INTO parent_players (person_id, player_id) VALUES (2, 8);
INSERT INTO slots (id, team_id, date, start_time, end_time, station, duty_name, venue, child_name, player_id)
  VALUES (3, 1, '2026-11-01', '18:00:00.000000', '21:00:00.000000', 'Café', 'Cafépass', 'Wallenstam arena', 'Klara', 7);
INSERT INTO slots (id, team_id, date, start_time, end_time, station, duty_name, venue, child_name, player_id)
  VALUES (4, 1, '2026-11-08', '18:00:00.000000', '21:00:00.000000', 'Café', 'Cafépass', 'Wallenstam arena', 'Bea', 8);
INSERT INTO swap_requests (id, proposer_slot_id, target_slot_id, status, created_at)
  VALUES (1, 3, 4, 'pending', '2026-10-01 10:00:00.000000');
"""


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("migrate_multi_team", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def old_db(tmp_path):
    path = tmp_path / "duty.db"
    connection = sqlite3.connect(path)
    connection.executescript(OLD_SCHEMA + OLD_ROWS)
    connection.commit()
    connection.close()
    return path


def _session(path):
    return make_session_factory(make_engine(str(path)))()


def test_the_existing_team_gets_the_given_slug(migration, old_db):
    assert migration.main(["x", str(old_db), "f15"]) == 0

    assert _session(old_db).get(Team, 1).slug == "f15"


def test_ids_parents_slots_and_pending_swaps_survive(migration, old_db):
    migration.main(["x", str(old_db), "f15"])
    session = _session(old_db)

    assert [p.email for p in session.get(Player, 7).parents] == ["dan@exempel.se"]
    assert session.get(Slot, 3).player_id == 7
    assert session.get(SwapRequest, 1).target_slot_id == 4


def test_afterwards_another_team_may_have_a_child_of_the_same_name(migration, old_db):
    migration.main(["x", str(old_db), "f15"])
    session = _session(old_db)
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    session.add(p13)
    session.flush()
    session.add(Player(name="Klara", team_id=p13.id))
    session.commit()

    assert session.query(Player).filter_by(name="Klara").count() == 2


def test_running_it_again_changes_nothing(migration, old_db, capsys):
    migration.main(["x", str(old_db), "f15"])
    capsys.readouterr()

    assert migration.main(["x", str(old_db), "f15"]) == 0

    out = capsys.readouterr().out
    assert "fanns redan" in out
    assert "ombyggd" not in out
    assert _session(old_db).get(Player, 8).parents[0].email == "bo@exempel.se"


def test_a_bad_slug_is_refused_before_anything_changes(migration, old_db):
    assert migration.main(["x", str(old_db), "F 15"]) == 1

    connection = sqlite3.connect(old_db)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(teams)")}
    connection.close()
    assert "slug" not in columns
