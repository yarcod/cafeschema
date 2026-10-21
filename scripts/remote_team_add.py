"""Create a team in the deployed app's database. Runs ON the Fly machine.

Uploaded and executed by `just team-add`; not meant to run locally.

    python /tmp/remote_team_add.py <slug> <namn> <arena>
"""

from __future__ import annotations

import sys

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.teams import TeamError, create_team

DB_PATH = "/data/duty.db"


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 1
    engine = make_engine(DB_PATH)
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        team = create_team(session, slug=argv[1], name=argv[2], venue=argv[3])
    except TeamError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Skapade {team.name} ({team.slug}), id {team.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
