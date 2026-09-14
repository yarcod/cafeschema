"""Add or remove a throwaway parent + pass for testing the swap flow end to end.

Runs ON the Fly machine (uploaded by `just test-fixture-add` / `-remove`).

The test parent is a real, loggable account — that is the point: swap mails
and the magic-link login can only be exercised against an address that is
actually in the schedule. Everything it creates is labelled TEST so nobody
mistakes it for a real duty, and `remove` deletes all of it.

    python test_swap_fixture.py add <e-postadress>
    python test_swap_fixture.py remove <e-postadress>

Adressen måste vara en du själv kan läsa mail på — poängen är att kunna
klicka i inloggningslänken och bytesmailen på riktigt.
"""

from __future__ import annotations

import sys
from datetime import date, time

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, SwapRequest, Team

DB_PATH = "/data/duty.db"

TEST_NAME = "TEST Testförälder"
TEST_CHILD = "TEST Testspelare"
TEST_DUTY = "TEST – bytesprov"
TEST_DATE = date(2026, 10, 4)


def add(session, team: Team, email: str) -> None:
    person = session.query(Person).filter_by(email=email).one_or_none()
    if person is None:
        person = Person(name=TEST_NAME, email=email)
        session.add(person)
        session.flush()
        print(f"Skapade testförälder {email} (id {person.id})")
    else:
        print(f"Testförälder {email} fanns redan (id {person.id})")

    existing = session.query(Slot).filter_by(duty_name=TEST_DUTY).count()
    if existing:
        print(f"{existing} testpass fanns redan — hoppar över")
        return

    session.add(Slot(
        team_id=team.id, date=TEST_DATE, start_time=time(10, 0), end_time=time(12, 0),
        station="", duty_name=TEST_DUTY, venue="Wallenstam arena",
        note="Testpass — går att ta bort när bytesflödet är verifierat",
        child_name=TEST_CHILD, person_id=person.id,
    ))
    print(f"Skapade testpass {TEST_DATE} 10:00-12:00 för {email}")


def remove(session, team: Team, email: str) -> None:
    slots = session.query(Slot).filter_by(duty_name=TEST_DUTY).all()
    slot_ids = [slot.id for slot in slots]
    if slot_ids:
        # Swap requests reference slots, so they have to go first or the
        # Byten page would try to render a request pointing at nothing.
        swaps = (
            session.query(SwapRequest)
            .filter(
                SwapRequest.proposer_slot_id.in_(slot_ids)
                | SwapRequest.target_slot_id.in_(slot_ids)
            )
            .all()
        )
        for swap in swaps:
            session.delete(swap)
        print(f"Tog bort {len(swaps)} bytesförfrågningar")

    for slot in slots:
        session.delete(slot)
    print(f"Tog bort {len(slots)} testpass")

    person = session.query(Person).filter_by(email=email).one_or_none()
    if person is not None:
        remaining = session.query(Slot).filter_by(person_id=person.id).count()
        if remaining:
            print(f"Behåller {email} — har {remaining} riktiga pass kvar")
        else:
            session.delete(person)
            print(f"Tog bort testföräldern {email}")


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("add", "remove"):
        print(__doc__)
        return 1
    email = sys.argv[2]

    engine = make_engine(DB_PATH)
    init_db(engine)
    session = make_session_factory(engine)()
    team = session.query(Team).first()
    if team is None:
        print("Ingen grupp finns i databasen.", file=sys.stderr)
        return 1

    (add if sys.argv[1] == "add" else remove)(session, team, email)
    session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
