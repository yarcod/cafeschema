"""Import a seed xlsx into the deployed app's database. Runs ON the Fly machine.

Uploaded and executed by `just schedule-push`; not meant to run locally.

    python /tmp/remote_import.py <slug>
The deployed image has no curl and no sqlite3 binary, so this calls the
importer directly instead of going through the localhost admin endpoint.

Replaces that team's slots with the contents of the file. Players, parents and
their logged-in sessions are untouched — they come from the roster import
(`just roster-push <slug>`), which must have run at least once first.
"""

from __future__ import annotations

import sys

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Slot
from duty_web.seed import SeedError, import_schedule
from duty_web.teams import TeamError, team_by_slug

DB_PATH = "/data/duty.db"
SEED_PATH = "/tmp/seed.xlsx"


def main() -> int:
    engine = make_engine(DB_PATH)
    init_db(engine)
    session = make_session_factory(engine)()

    if len(sys.argv) != 2:
        print("Ange lagets slug, t.ex. f15.", file=sys.stderr)
        return 1
    try:
        team = team_by_slug(session, sys.argv[1])
    except TeamError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        count = import_schedule(session, SEED_PATH, team_id=team.id)
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    team_slots = session.query(Slot).filter(Slot.team_id == team.id)
    dated = team_slots.filter(Slot.date.isnot(None)).count()
    unassigned = team_slots.filter(Slot.player_id.is_(None)).count()
    print(f"Importerade {count} pass till {team.name}")
    print(f"  med datum: {dated}")
    print(f"  datum ej satt: {count - dated}")
    print(f"  utan matchad spelare: {unassigned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
