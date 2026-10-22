"""Rehearse the admin migration against a copy of production. Read-only check.

    just db-pull /tmp/duty-copy.db
    cd web && uv run python -m duty_web.migrate /tmp/duty-copy.db
    cd web && uv run python ../scripts/verify_admin_migration.py /tmp/duty-copy.db

Fails (exit 1) if any slot would now link a different instruction than its
duty name did before the migration, the one thing parents could notice.
"""

from __future__ import annotations

import sys

from duty_web.db import make_engine, make_session_factory
from duty_web.duties import profile_for
from duty_web.models import Slot
from duty_web.places import document_for_slot


def main(db_path: str) -> int:
    session = make_session_factory(make_engine(db_path))()
    slots = session.query(Slot).all()
    mismatches = []
    for slot in slots:
        profile = profile_for(slot.duty_name)
        before = profile.document if profile else None
        if before != document_for_slot(slot):
            mismatches.append(slot.id)
    linked = sum(1 for slot in slots if slot.station_id is not None)
    print(f"{len(slots)} pass, {linked} kopplade till en station, {len(slots) - linked} utan plats.")
    if mismatches:
        print(f"{len(mismatches)} pass skulle länka en annan instruktion: id {mismatches}")
        return 1
    print("Alla pass länkar samma instruktion som före migreringen.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/duty-copy.db"))
