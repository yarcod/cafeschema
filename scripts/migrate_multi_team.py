"""Give teams a slug and make player names unique per team. Idempotent.

Before: teams had no short name, and players.name was unique across the
whole database, so a child on a second team couldn't share a name with one
on the first. After: teams.slug is NOT NULL and UNIQUE, and players are
unique per (team_id, name).

Runs against a database file, either on the Fly machine (via
`just multi-team-migrate-push`) or on a downloaded copy for a rehearsal:

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


def _add_team_slug(connection: sqlite3.Connection, slug: str) -> bool:
    if "slug" in _columns(connection, "teams"):
        return False
    (count,) = connection.execute("SELECT COUNT(*) FROM teams").fetchone()
    if count > 1:
        raise SystemExit("Fler än en grupp saknar slug — sätt dem för hand.")
    connection.execute("ALTER TABLE teams ADD COLUMN slug VARCHAR")
    connection.execute("UPDATE teams SET slug = ?", (slug,))
    return True


def _rebuild(connection: sqlite3.Connection, engine, table: str) -> None:
    """Recreate a table from its model's DDL, keeping every row and id.

    SQLite can't add or drop a constraint in place, so this is the
    documented create-new/copy/swap recipe (as in
    migrate_player_ownership.py). The DDL comes from the model, so the
    result can't drift from what create_all makes on a fresh database.
    Foreign keys are off for the swap: other tables reference this one by
    id, and the ids are kept, which foreign_key_check confirms afterwards.
    """
    model_table = Base.metadata.tables[table]
    ddl = str(CreateTable(model_table).compile(engine)).strip()
    prefix = f"CREATE TABLE {table} ("
    if not ddl.startswith(prefix):
        raise SystemExit(f"Oväntad DDL för {table}: {ddl[:60]}")
    ddl = ddl.replace(prefix, f"CREATE TABLE {table}_new (", 1)
    columns = ", ".join(column.name for column in model_table.columns)

    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN")
    try:
        connection.execute(ddl)
        connection.execute(
            f"INSERT INTO {table}_new ({columns}) SELECT {columns} FROM {table}"
        )
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise SystemExit(f"Trasiga referenser efter ombyggnad av {table}: {violations}")
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
