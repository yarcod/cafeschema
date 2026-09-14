"""Import the 360Player contact list into the deployed app. Runs ON the Fly machine.

Uploaded and executed by `just roster-push`; not meant to run locally.
The deployed image has no curl and no sqlite3 binary, so this calls the
importer directly instead of going through the localhost admin endpoint.

Additive: adds players, parents and the links between them, and updates a
parent's name if the export spells it differently. It never removes
anything, so re-running it after a new parent registers in 360Player costs
nothing and disturbs no live session or pending swap.
"""

from __future__ import annotations

import sys

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Player, Team
from duty_web.roster import RosterError, import_roster

DB_PATH = "/data/duty.db"
ROSTER_PATH = "/tmp/roster.csv"


def main() -> int:
    engine = make_engine(DB_PATH)
    init_db(engine)
    session = make_session_factory(engine)()

    team = session.query(Team).first()
    if team is None:
        print("Ingen grupp finns i databasen — skapa en först.", file=sys.stderr)
        return 1

    try:
        result = import_roster(session, ROSTER_PATH, team_id=team.id)
    except RosterError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Laglistan synkad mot {team.name}")
    print(f"  nya spelare: {result.players_added}")
    print(f"  nya föräldrar: {result.parents_added}")
    print(f"  nya kopplingar: {result.links_added}")
    print(f"  namn uppdaterade: {result.parents_renamed}")
    if result.rows_without_email:
        print(f"  rader utan e-post: {result.rows_without_email}")
    print(f"  totalt: {session.query(Player).count()} spelare, "
          f"{session.query(Person).count()} föräldrar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
