"""Rename duties and fill in where they are held. Idempotent.

Before: the slots carried the spreadsheet's own words — "Arena värdskap
höst"/"vinter", no station, and no venue at all for the cup. After: both
arena terms are "Cafépass", and every slot says which building and which
café, per DUTY_PROFILES in duty_web/duties.py.

Deliberately an UPDATE rather than a re-import: `just schedule-push` deletes
every slot (seed.py), which would revert accepted swaps and orphan the
pending ones. Nothing here touches ownership.

Runs against a database file, either on the Fly machine (via
`just duties-push`) or on a downloaded copy for a rehearsal:

    python scripts/rename_duty_profiles.py [db_path]

Safe to re-run: a second run finds nothing left to change. The same table
goes into build_seed_xlsx.py, so the next schedule import writes these
values itself instead of undoing them.
"""

from __future__ import annotations

import sys

from duty_web.db import make_engine, make_session_factory
from duty_web.duties import DUTY_PROFILES, RENAMED_DUTIES, profile_for
from duty_web.models import Slot

DEFAULT_DB_PATH = "/data/duty.db"


def apply_profiles(session) -> tuple[int, int]:
    """Returns (renamed slots, slots given a venue/station)."""
    renamed = 0
    placed = 0

    for slot in session.query(Slot).all():
        canonical = RENAMED_DUTIES.get(slot.duty_name)
        if canonical is not None:
            slot.duty_name = canonical
            renamed += 1

        profile = profile_for(slot.duty_name)
        if profile is None:
            continue
        if (slot.venue, slot.station) != (profile.venue, profile.station):
            slot.venue = profile.venue
            slot.station = profile.station
            placed += 1

    session.commit()
    return renamed, placed


def main(argv: list[str]) -> int:
    db_path = argv[1] if len(argv) > 1 else DEFAULT_DB_PATH

    session = make_session_factory(make_engine(db_path))()
    if session.query(Slot).count() == 0:
        print(f"Inga pass i {db_path} — inget att göra.", file=sys.stderr)
        return 1

    renamed, placed = apply_profiles(session)

    print(f"Döpte om {renamed} pass")
    print(f"Satte plats och café på {placed} pass")
    for name, profile in DUTY_PROFILES.items():
        count = session.query(Slot).filter(Slot.duty_name == name).count()
        print(f"  {name}: {count} pass — {profile.venue}, {profile.station}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
