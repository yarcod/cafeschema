"""Import a seed xlsx into the deployed app's database. Runs ON the Fly machine.

Uploaded and executed by `just schedule-push`; not meant to run locally.
The deployed image has no curl and no sqlite3 binary, so this calls the
importer directly instead of going through the localhost admin endpoint.

Replaces every slot with the contents of the file. Parents (and their
logged-in sessions) are matched by e-post and kept — see seed.py.
"""

from __future__ import annotations

import sys

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, Team
from duty_web.seed import import_schedule

DB_PATH = "/data/duty.db"
SEED_PATH = "/tmp/seed.xlsx"


def main() -> int:
    engine = make_engine(DB_PATH)
    init_db(engine)
    session = make_session_factory(engine)()

    team = session.query(Team).first()
    if team is None:
        print("Ingen grupp finns i databasen — skapa en först.", file=sys.stderr)
        return 1

    count = import_schedule(session, SEED_PATH, team_id=team.id)
    dated = session.query(Slot).filter(Slot.date.isnot(None)).count()
    print(f"Importerade {count} pass till {team.name}")
    print(f"  med datum: {dated}")
    print(f"  datum ej satt: {count - dated}")
    print(f"  föräldrar: {session.query(Person).count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
