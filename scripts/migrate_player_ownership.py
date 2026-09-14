"""Move slot ownership from a single parent to the player. Idempotent.

Before: slots.person_id pointed at whichever parent the seed file happened
to list, so the other parent of that child could neither log in nor see the
duty. After: slots.player_id points at the player, and parent_players says
which parents may act for them.

Runs against a database file, either on the Fly machine (via
`just migrate-push`) or on a downloaded copy for a rehearsal:

    python scripts/migrate_player_ownership.py [db_path] [roster_csv]

Safe to re-run: every step checks whether it has already been applied, and
the roster import is additive by construction.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from sqlalchemy.schema import CreateTable

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Player, Slot, Team
from duty_web.roster import import_roster, normalize_name

DEFAULT_DB_PATH = "/data/duty.db"
DEFAULT_ROSTER_PATH = "/tmp/roster.csv"


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _add_player_id_column(db_path: str) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        columns = _columns(connection, "slots")
        if not columns:
            raise SystemExit(f"Ingen slots-tabell i {db_path}")
        if "player_id" in columns:
            return False
        connection.execute(
            "ALTER TABLE slots ADD COLUMN player_id INTEGER REFERENCES players(id)"
        )
        connection.commit()
        return True
    finally:
        connection.close()


def _legacy_owners(db_path: str) -> dict[int, int | None]:
    """slot id -> the person_id the old schema recorded for it."""
    connection = sqlite3.connect(db_path)
    try:
        if "person_id" not in _columns(connection, "slots"):
            return {}
        return {
            row[0]: row[1] for row in connection.execute("SELECT id, person_id FROM slots")
        }
    finally:
        connection.close()


def _drop_person_id_column(db_path: str, engine) -> bool:
    """Shed slots.person_id by rebuilding the table.

    SQLite's own ALTER TABLE ... DROP COLUMN refuses a column named in a
    foreign key definition, which person_id is, so this uses the documented
    create-new/copy/swap recipe instead. The new table's DDL is generated
    from the Slot model rather than written out here, so it cannot drift
    from what create_all would produce on a fresh database.
    """
    connection = sqlite3.connect(db_path, isolation_level=None)
    try:
        if "person_id" not in _columns(connection, "slots"):
            return False

        ddl = str(CreateTable(Slot.__table__).compile(engine)).strip()
        if not ddl.startswith("CREATE TABLE slots ("):
            raise SystemExit(f"Oväntad DDL för slots: {ddl[:60]}")
        ddl = ddl.replace("CREATE TABLE slots (", "CREATE TABLE slots_new (", 1)
        columns = ", ".join(column.name for column in Slot.__table__.columns)

        # Foreign keys are switched off for the swap itself: swap_requests
        # references slots.id, and dropping the old table with enforcement
        # on would either fail or null those references out. The rows keep
        # their ids, so the references stay valid — checked below.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN")
        try:
            connection.execute(ddl)
            connection.execute(
                f"INSERT INTO slots_new ({columns}) SELECT {columns} FROM slots"
            )
            connection.execute("DROP TABLE slots")
            connection.execute("ALTER TABLE slots_new RENAME TO slots")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise SystemExit(f"Trasiga referenser efter ombyggnad: {violations}")
        connection.execute("PRAGMA foreign_keys=ON")
        return True
    finally:
        connection.close()


def main(argv: list[str]) -> int:
    db_path = argv[1] if len(argv) > 1 else DEFAULT_DB_PATH
    roster_csv = Path(argv[2] if len(argv) > 2 else DEFAULT_ROSTER_PATH)

    if not Path(db_path).is_file():
        print(f"Databasen hittades inte: {db_path}", file=sys.stderr)
        return 1
    if not roster_csv.is_file():
        print(f"Laglistan hittades inte: {roster_csv}", file=sys.stderr)
        return 1

    engine = make_engine(db_path)
    # Creates players and parent_players; leaves the existing tables alone.
    init_db(engine)

    added = _add_player_id_column(db_path)
    print(f"slots.player_id: {'tillagd' if added else 'fanns redan'}")

    session = make_session_factory(engine)()
    team = session.query(Team).first()
    if team is None:
        print("Ingen grupp finns i databasen — skapa en först.", file=sys.stderr)
        return 1

    result = import_roster(session, roster_csv, team_id=team.id)
    print(
        f"Laglista: +{result.players_added} spelare, +{result.parents_added} "
        f"föräldrar, +{result.links_added} kopplingar, "
        f"{result.parents_renamed} namn uppdaterade"
    )

    players_by_name = {
        normalize_name(player.name): player for player in session.query(Player).all()
    }
    legacy_owners = _legacy_owners(db_path)
    backfilled = 0
    carried_over: set[str] = set()
    orphaned: set[str] = set()
    for slot in session.query(Slot).all():
        if slot.player_id is not None:
            continue
        player = players_by_name.get(normalize_name(slot.child_name or ""))
        if player is None:
            # Not on the roster (a hand-added test fixture, or a player the
            # 360Player export hasn't caught up with). person_id is the only
            # record of who held this slot, and it is about to be dropped —
            # so rebuild the same ownership as a player of their own rather
            # than losing it.
            owner = session.get(Person, legacy_owners.get(slot.id) or 0)
            if slot.child_name and owner is not None:
                player = Player(name=slot.child_name, team_id=slot.team_id)
                player.parents = [owner]
                session.add(player)
                session.flush()
                players_by_name[normalize_name(player.name)] = player
                carried_over.add(player.name)
            else:
                orphaned.add(slot.child_name or "(tomt)")
                continue
        slot.player_id = player.id
        backfilled += 1
    session.commit()
    print(f"Pass kopplade till spelare: {backfilled}")
    if carried_over:
        print("  utanför laglistan, ägare bevarad: " + ", ".join(sorted(carried_over)))
    if orphaned:
        # Nothing to preserve: these had no owner before either.
        print("  utan ägare även före: " + ", ".join(sorted(orphaned)))

    dropped = _drop_person_id_column(db_path, engine)
    print(f"slots.person_id: {'borttagen' if dropped else 'fanns inte'}")

    print(
        f"Klart. {session.query(Player).count()} spelare, "
        f"{session.query(Person).count()} föräldrar, "
        f"{session.query(Slot).count()} pass."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
