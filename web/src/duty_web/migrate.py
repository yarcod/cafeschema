"""Bring a database up to the admin schema. Idempotent.

Runs at every start (entrypoint.sh), before either process opens the
database, so a deploy never serves code against a file that lacks its
columns. New tables come from create_all; new columns on existing tables
need ALTER TABLE, which create_all never does.

The data half runs its seeding once, while the stations table is empty:
it creates duties.py's venues and cafés in the venues/stations tables and
stations from the text on slots, all without an instruction (those are
linked under Platser). After that the tables belong to the ansvariga: later
runs never create or rename a station and never touch a document, so an
unlink or a delete done in the admin is not undone at the next start. Every run still links slots that
have no station yet (slots imported before stations existed carry text only), by their
duty's profile or their own venue and station text, but only to stations
that already exist; a slot with no match is left for an ansvarig to place.

    python -m duty_web.migrate [db_path]
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from sqlalchemy import Engine

from .db import init_db, make_engine, make_session_factory
from .duties import DUTY_PROFILES, profile_for
from .models import Slot, Station
from .names import normalize_key
from .places import find_or_create_station, link_unlinked_slots

NEW_COLUMNS = [
    ("slots", "station_id", "INTEGER REFERENCES stations (id)"),
    ("slots", "origin", "VARCHAR NOT NULL DEFAULT 'import'"),
    ("players", "exempt", "BOOLEAN NOT NULL DEFAULT 0"),
]


@dataclass
class MigrationReport:
    columns_added: list[str] = field(default_factory=list)
    stations_created: int = 0
    slots_linked: int = 0
    slots_unlinked: int = 0


def _add_missing_columns(engine: Engine) -> list[str]:
    added = []
    with engine.begin() as connection:
        for table, column, ddl in NEW_COLUMNS:
            existing = {
                row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                added.append(f"{table}.{column}")
    return added


def migrate(db_path: str) -> MigrationReport:
    engine = make_engine(db_path)
    init_db(engine)
    report = MigrationReport(columns_added=_add_missing_columns(engine))

    session = make_session_factory(engine)()
    try:
        before = session.query(Station).count()
        first_run = before == 0
        if first_run:
            for profile in DUTY_PROFILES.values():
                find_or_create_station(session, profile.venue, profile.station)

        if first_run:
            # Only now may a slot's own text create its place (linked just
            # below); afterwards the tables belong to the ansvariga.
            for slot in session.query(Slot).filter(Slot.station_id.is_(None)).all():
                profile = profile_for(slot.duty_name)
                venue_name, station_name = (
                    (profile.venue, profile.station) if profile else (slot.venue, slot.station)
                )
                if normalize_key(venue_name) and normalize_key(station_name):
                    find_or_create_station(session, venue_name, station_name)
            session.flush()

        report.slots_linked, report.slots_unlinked = link_unlinked_slots(session)

        report.stations_created = session.query(Station).count() - before
        session.commit()
    finally:
        session.close()
    return report


def main(argv: list[str]) -> int:
    db_path = argv[1] if len(argv) > 1 else os.environ.get("DB_PATH", "/data/duty.db")
    report = migrate(db_path)
    print(
        f"Migrering klar: kolumner {report.columns_added or 'inga nya'}, "
        f"{report.stations_created} nya stationer, {report.slots_linked} pass kopplade, "
        f"{report.slots_unlinked} pass utan plats."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
