"""The admin migration: new columns, places from duties.py, slots linked."""

from __future__ import annotations

import sqlite3

from duty_web.db import make_engine, make_session_factory
from duty_web.migrate import migrate
from duty_web.models import Player, Slot, Station, Venue

# The schema as deployed before the admin: teams have slugs, players are
# unique per team, and there are no places, roles or keys.
PRE_ADMIN_SCHEMA = """
CREATE TABLE people ( id INTEGER NOT NULL, name VARCHAR NOT NULL, email VARCHAR NOT NULL, email_notifications BOOLEAN NOT NULL, PRIMARY KEY (id), UNIQUE (email) );
CREATE TABLE teams ( id INTEGER NOT NULL, name VARCHAR NOT NULL, slug VARCHAR NOT NULL, venue VARCHAR NOT NULL, PRIMARY KEY (id), UNIQUE (slug) );
CREATE TABLE players ( id INTEGER NOT NULL, name VARCHAR NOT NULL, team_id INTEGER NOT NULL, PRIMARY KEY (id), UNIQUE (team_id, name), FOREIGN KEY(team_id) REFERENCES teams (id) );
CREATE TABLE parent_players ( person_id INTEGER NOT NULL, player_id INTEGER NOT NULL, PRIMARY KEY (person_id, player_id), FOREIGN KEY(person_id) REFERENCES people (id), FOREIGN KEY(player_id) REFERENCES players (id) );
CREATE TABLE slots ( id INTEGER NOT NULL, team_id INTEGER NOT NULL, date DATE, start_time TIME NOT NULL, end_time TIME NOT NULL, station VARCHAR NOT NULL, duty_name VARCHAR NOT NULL, venue VARCHAR NOT NULL, child_name VARCHAR, note VARCHAR, player_id INTEGER, PRIMARY KEY (id), FOREIGN KEY(team_id) REFERENCES teams (id), FOREIGN KEY(player_id) REFERENCES players (id) );
CREATE TABLE swap_requests ( id INTEGER NOT NULL, proposer_slot_id INTEGER NOT NULL, target_slot_id INTEGER NOT NULL, status VARCHAR NOT NULL, created_at DATETIME NOT NULL, resolved_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(proposer_slot_id) REFERENCES slots (id), FOREIGN KEY(target_slot_id) REFERENCES slots (id) );
CREATE TABLE login_tokens ( id INTEGER NOT NULL, token_hash VARCHAR NOT NULL, email VARCHAR NOT NULL, expires_at DATETIME NOT NULL, consumed_at DATETIME, PRIMARY KEY (id), UNIQUE (token_hash) );
"""

ROWS = """
INSERT INTO teams (id, name, slug, venue) VALUES (1, 'F15', 'f15', 'Wallenstam arena');
INSERT INTO players (id, name, team_id) VALUES (7, 'Klara', 1);
INSERT INTO slots (id, team_id, date, start_time, end_time, station, duty_name, venue, child_name, player_id) VALUES
  (1, 1, '2026-11-01', '18:00:00.000000', '21:00:00.000000', 'Café Arena B, nedre plan', 'Cafépass', 'Wallenstam arena', 'Klara', 7),
  (2, 1, '2026-09-25', '16:30:00.000000', '22:45:00.000000', '', 'Bästkustcupen', '', 'Klara', 7),
  (3, 1, '2026-12-05', '10:00:00.000000', '12:00:00.000000', '', 'Åby Julmarknad', '', 'Klara', 7),
  (4, 1, '2026-12-12', '09:00:00.000000', '12:00:00.000000', 'Kiosk', 'Kioskpass', 'Mölnlycke  idrottshall', 'Klara', 7);
"""


def _pre_admin_db(tmp_path) -> str:
    path = tmp_path / "duty.db"
    connection = sqlite3.connect(path)
    connection.executescript(PRE_ADMIN_SCHEMA + ROWS)
    connection.commit()
    connection.close()
    return str(path)


def _session(path):
    return make_session_factory(make_engine(path))()


def test_the_new_columns_are_added(tmp_path):
    path = _pre_admin_db(tmp_path)

    report = migrate(path)

    assert set(report.columns_added) == {"slots.station_id", "players.exempt"}
    assert _session(path).get(Player, 7).exempt is False


def test_places_come_from_the_duty_profiles(tmp_path):
    path = _pre_admin_db(tmp_path)

    migrate(path)

    session = _session(path)
    assert {v.name for v in session.query(Venue)} == {"Wallenstam arena", "Mölnlycke idrottshall"}
    documents = {(s.venue.name, s.name): s.document for s in session.query(Station)}
    assert documents[("Wallenstam arena", "Café Arena B, nedre plan")] == (
        "Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf"
    )
    assert documents[("Wallenstam arena", "Café Arena A, övre plan")] == (
        "Cafeteria_Instruktion_W_Arena_A_övre_plan.pdf"
    )
    assert documents[("Mölnlycke idrottshall", "Café")] == "Cafeteria_Instruktion_MIH.pdf"


def test_slots_are_linked_by_their_duty_or_their_own_text(tmp_path):
    path = _pre_admin_db(tmp_path)

    report = migrate(path)

    session = _session(path)
    # By the duty's profile, even where the slot's own text is empty.
    assert session.get(Slot, 1).station_row.name == "Café Arena B, nedre plan"
    assert session.get(Slot, 2).station_row.document == "Cafeteria_Instruktion_MIH.pdf"
    # By its own text when the duty has no profile; the extra space in the
    # venue doesn't create a second Mölnlycke idrottshall.
    kiosk = session.get(Slot, 4).station_row
    assert (kiosk.venue.name, kiosk.name, kiosk.document) == ("Mölnlycke idrottshall", "Kiosk", None)
    # Neither profile nor text: left for an ansvarig to place.
    assert session.get(Slot, 3).station_id is None
    assert (report.slots_linked, report.slots_unlinked) == (3, 1)


def test_running_it_again_changes_nothing(tmp_path):
    path = _pre_admin_db(tmp_path)
    migrate(path)

    report = migrate(path)

    assert report.columns_added == []
    assert report.stations_created == 0
    assert (report.slots_linked, report.slots_unlinked) == (0, 1)
    assert _session(path).query(Station).count() == 4


def test_a_linked_station_keeps_an_instruction_set_by_hand(tmp_path):
    path = _pre_admin_db(tmp_path)
    migrate(path)
    session = _session(path)
    station = session.get(Slot, 1).station_row
    station.document = "Annan_instruktion.pdf"
    session.commit()

    migrate(path)

    assert _session(path).get(Slot, 1).station_row.document == "Annan_instruktion.pdf"
