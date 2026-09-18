"""One-off: helgen v38 blev omgjord — ett pass i stället för sju. Idempotent.

Före: sju Cafépass i Arena B, nedre plan (fre 18–21, lör 09–12, 12–15, 15–18
och söndag likadant), fjorton pass fördelade på fjorton spelare. Efter: ett
enda pass, lördag 12:30–16:30, i Café Arena A övre plan — Vera Fredriksson
Esmyr och Tilda Haarakangas (dvs. Sofie Esmyrs och Mikaela Haarakangas
familjer). Fredagens pass ställdes in och ersätts inte.

Deliberately an UPDATE rather than a re-import: `just schedule-push` deletes
every slot (seed.py), which would revert accepted swaps elsewhere in the
season. Only helgens pass rörs här.

Pending byten som pekar på ett borttaget pass tas bort med det — annars blir
de hängande rader som byteslistan inte kan rendera.

Runs against a database file, either on the Fly machine (via
`just weekend-38-push`) or on a downloaded copy for a rehearsal:

    python scripts/reschedule_weekend_38.py [db_path]

Safe to re-run: a second run finds helgen redan omgjord och gör ingenting.
Samma ändring ligger i data/seed_sasong_26_27.xlsx, så en ny schedule-push
skriver samma sak i stället för att ångra den.
"""

from __future__ import annotations

import sys
from datetime import date, time

from duty_web.db import make_engine, make_session_factory
from duty_web.duties import profile_for
from duty_web.models import Player, Slot, SwapRequest, Team

DEFAULT_DB_PATH = "/data/duty.db"

# Fredagen hör med: passet den kvällen ställdes in, inte bara helgens.
WEEKEND = (date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 20))
NEW_DATE = date(2026, 9, 19)
NEW_DUTY = "Cafépass Arena A"
NEW_START = time(12, 30)
NEW_END = time(16, 30)
NEW_PLAYERS = ("Vera Fredriksson Esmyr", "Tilda Haarakangas")


def _weekend_slots(session) -> list[Slot]:
    return (
        session.query(Slot)
        .filter(Slot.date.in_(WEEKEND))
        .order_by(Slot.date, Slot.start_time)
        .all()
    )


def reschedule(session, team: Team) -> tuple[int, int, int]:
    """Returns (removed slots, removed swap requests, created slots)."""
    profile = profile_for(NEW_DUTY)
    if profile is None:
        raise SystemExit(
            f"{NEW_DUTY} saknar profil i duty_web.duties — driftsätt appen först."
        )

    # Look the players up before deleting anything: a missing player would
    # leave the weekend empty with nothing to put back.
    players = []
    for name in NEW_PLAYERS:
        player = session.query(Player).filter_by(name=name).one_or_none()
        if player is None:
            raise SystemExit(
                f"Spelaren {name!r} finns inte i laget — kör `just roster-push` först."
            )
        players.append(player)

    existing = _weekend_slots(session)
    keep = [
        slot
        for slot in existing
        if slot.duty_name == NEW_DUTY
        and slot.date == NEW_DATE
        and (slot.start_time, slot.end_time) == (NEW_START, NEW_END)
        and slot.player_id in {player.id for player in players}
    ]
    if len(keep) == len(players) and len(existing) == len(keep):
        return 0, 0, 0

    slot_ids = [slot.id for slot in existing]
    removed_swaps = (
        session.query(SwapRequest)
        .filter(
            SwapRequest.proposer_slot_id.in_(slot_ids)
            | SwapRequest.target_slot_id.in_(slot_ids)
        )
        .delete(synchronize_session=False)
    )
    for slot in existing:
        session.delete(slot)
    session.flush()

    for player in players:
        session.add(
            Slot(
                team_id=team.id,
                date=NEW_DATE,
                start_time=NEW_START,
                end_time=NEW_END,
                station=profile.station,
                duty_name=NEW_DUTY,
                venue=profile.venue,
                child_name=player.name,
                player_id=player.id,
            )
        )

    session.commit()
    return len(existing), removed_swaps, len(players)


def main(argv: list[str]) -> int:
    db_path = argv[1] if len(argv) > 1 else DEFAULT_DB_PATH

    session = make_session_factory(make_engine(db_path))()
    team = session.query(Team).first()
    if team is None:
        print(f"Ingen grupp finns i {db_path}.", file=sys.stderr)
        return 1

    print("Helgen före ändringen:")
    for slot in _weekend_slots(session):
        owner = slot.player.name if slot.player else f"(ledigt: {slot.child_name})"
        print(f"  {slot.date} {slot.start_time:%H:%M}-{slot.end_time:%H:%M} "
              f"{slot.duty_name}, {slot.station} :: {owner}")

    removed, removed_swaps, created = reschedule(session, team)
    if (removed, created) == (0, 0):
        print("Helgen är redan omgjord — inget att göra.")
        return 0

    print(f"Tog bort {removed} pass och {removed_swaps} bytesförslag")
    print(f"La till {created} pass:")
    for slot in _weekend_slots(session):
        print(f"  {slot.date} {slot.start_time:%H:%M}-{slot.end_time:%H:%M} "
              f"{slot.duty_name}, {slot.venue} / {slot.station} :: {slot.player.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
