"""Give teams a slug and make player names unique per team. Idempotent.

Before: teams had no short name, and players.name was unique across the
whole database, so a child on a second team couldn't share a name with one
on the first. After: teams.slug is NOT NULL and UNIQUE, and players are
unique per (team_id, name).

Runs against a database file, either on the Fly machine (upload it and run
it over `fly ssh console`) or on a copy from `just db-pull` for a rehearsal:

    python scripts/migrate_multi_team.py [db_path] [slug]

slug goes to the one team that exists before this migration (F15 → f15).
Safe to re-run: each step checks whether it has already been applied.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from sqlalchemy.schema import CreateTable

from duty_web.db import make_engine
from duty_web.models import Base
from duty_web.teams import TeamError, validate_slug

DEFAULT_DB_PATH = "/data/duty.db"
DEFAULT_SLUG = "f15"


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _unique_column_sets(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    """The column lists of every UNIQUE constraint/index on a table."""
    sets = set()
    for _, index_name, unique, *_ in connection.execute(f"PRAGMA index_list({table})"):
        if unique:
            sets.add(tuple(
                row[2] for row in connection.execute(f"PRAGMA index_info('{index_name}')")
            ))
    return sets


def _add_slot_station_id(connection: sqlite3.Connection) -> bool:
    """Add the nullable slots.station_id link if missing. True if added.

    Nullable, so no backfill: migrate.py links the slots to stations later.
    """
    if "station_id" in _columns(connection, "slots"):
        return False
    connection.execute(
        "ALTER TABLE slots ADD COLUMN station_id INTEGER REFERENCES stations(id)"
    )
    return True


def _add_team_slug(connection: sqlite3.Connection, slug: str) -> bool:
    """Make sure every team has a slug. True if anything was written.

    The column and its backfill go in one transaction (SQLite DDL is
    transactional): a crash between them used to leave the column with NULL
    slugs, and every re-run then failed on NOT NULL in the teams rebuild.
    If that state is already there, it is repaired here.
    """
    has_column = "slug" in _columns(connection, "teams")
    (count,) = connection.execute("SELECT COUNT(*) FROM teams").fetchone()
    if has_column:
        (missing,) = connection.execute(
            "SELECT COUNT(*) FROM teams WHERE slug IS NULL"
        ).fetchone()
        if not missing:
            return False
    if count > 1:
        raise SystemExit("Fler än en grupp saknar slug — sätt dem för hand.")

    connection.execute("BEGIN")
    try:
        if not has_column:
            connection.execute("ALTER TABLE teams ADD COLUMN slug VARCHAR")
        connection.execute("UPDATE teams SET slug = ? WHERE slug IS NULL", (slug,))
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return True


def _violations(connection: sqlite3.Connection) -> set[tuple]:
    """(child table, rowid, parent table) for every dangling reference.

    The constraint index is left out: a rebuilt table may number its foreign
    keys differently without a single reference having changed.
    """
    return {
        (table, rowid, parent)
        for table, rowid, parent, _ in connection.execute("PRAGMA foreign_key_check")
    }


def _rebuild(connection: sqlite3.Connection, engine, table: str) -> None:
    """Recreate a table from its model's DDL, keeping every row and id.

    SQLite can't add or drop a constraint in place, so this is the
    documented create-new/copy/swap recipe (as in
    migrate_player_ownership.py). The DDL comes from the model, so the
    result can't drift from what create_all makes on a fresh database.
    Foreign keys are off for the swap: other tables reference this one by
    id, and the ids are kept, which foreign_key_check confirms before the
    swap is committed. Only violations that are new compared with before the
    swap count: a dangling reference that was already there is warned about,
    never blamed on (or blocking) the migration.
    """
    model_table = Base.metadata.tables[table]
    ddl = str(CreateTable(model_table).compile(engine)).strip()
    prefix = f"CREATE TABLE {table} ("
    if not ddl.startswith(prefix):
        raise SystemExit(f"Oväntad DDL för {table}: {ddl[:60]}")
    ddl = ddl.replace(prefix, f"CREATE TABLE {table}_new (", 1)
    # Copy only the columns the old table has (columns added to the model
    # after this migration's era take their defaults).
    existing = _columns(connection, table)
    columns = ", ".join(c.name for c in model_table.columns if c.name in existing)

    baseline = _violations(connection)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN")
    try:
        connection.execute(ddl)
        connection.execute(
            f"INSERT INTO {table}_new ({columns}) SELECT {columns} FROM {table}"
        )
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        new = _violations(connection) - baseline
        if new:
            connection.execute("ROLLBACK")
            connection.execute("PRAGMA foreign_keys=ON")
            raise SystemExit(
                f"Trasiga referenser efter ombyggnad av {table}, ändringen ångrades: "
                f"{sorted(new)}"
            )
        connection.execute("COMMIT")
    except SystemExit:
        raise
    except Exception:
        connection.execute("ROLLBACK")
        raise

    if baseline:
        print(
            f"Varning: {len(baseline)} trasiga referenser fanns redan före "
            f"migreringen och lämnades som de var: {sorted(baseline)}"
        )
    connection.execute("PRAGMA foreign_keys=ON")


def main(argv: list[str]) -> int:
    db_path = argv[1] if len(argv) > 1 else DEFAULT_DB_PATH
    slug = argv[2] if len(argv) > 2 else DEFAULT_SLUG

    try:
        validate_slug(slug)
    except TeamError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not Path(db_path).is_file():
        print(f"Databasen hittades inte: {db_path}", file=sys.stderr)
        return 1

    engine = make_engine(db_path)
    connection = sqlite3.connect(db_path, isolation_level=None)
    try:
        added = _add_team_slug(connection, slug)
        print(f"teams.slug: {'tillagd' if added else 'fanns redan'}")

        if ("slug",) in _unique_column_sets(connection, "teams"):
            print("teams: unik slug fanns redan")
        else:
            _rebuild(connection, engine, "teams")
            print("teams: ombyggd med unik slug")

        added = _add_slot_station_id(connection)
        print(f"slots.station_id: {'tillagd' if added else 'fanns redan'}")

        if ("team_id", "name") in _unique_column_sets(connection, "players"):
            print("players: namn per lag fanns redan")
        else:
            _rebuild(connection, engine, "players")
            print("players: ombyggd, namn unika per lag")
    finally:
        connection.close()

    print("Klart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
