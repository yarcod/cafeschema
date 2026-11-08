"""The draw: who fills the empty seats, fairly, read from the effective schedule.

The load, held days and occupied shifts are counted here from the seats as
they would be once everything väntande is published, and the choice is made
by staffing.assign, which is pure. Candidates are the team's players who
aren't exempt (a leader's family) and have at least one parent who can be
reached.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from sqlalchemy.orm import Session

from ..agenda import shift_key
from ..models import Player, Slot
from ..staffing import Candidate, Filled, Seat, assign
from .pending import SeatView


def shifts_elsewhere(
    session: Session, team_id: int, days: set[date], families: dict[int, frozenset[int]]
) -> dict[int, tuple]:
    """Each player's family's shifts on other teams on these days, by shared parent.

    Reads other teams' live seats only: what they have staged is theirs to
    publish, and not ours to see.
    """
    by_parent: defaultdict = defaultdict(set)
    if days:
        others = session.query(Slot).filter(
            Slot.team_id != team_id, Slot.player_id.isnot(None), Slot.date.in_(days)
        )
        for slot in others:
            for parent in slot.player.parents:
                by_parent[parent.id].add((slot.date, slot.start_time, slot.end_time))
    return {
        player_id: tuple(shift for parent_id in family for shift in by_parent[parent_id])
        for player_id, family in families.items()
    }


def draw_seats(
    empty: list[SeatView],
    everything: list[SeatView],
    players: dict[int, Player],
    *,
    seed: int,
    season: tuple[date, date],
    elsewhere: dict[int, tuple],
) -> list[Filled]:
    """Fill the empty seats; Filled.slot_id holds SeatView.ref.

    everything is the team's effective schedule, which is what a player's
    load, days and shifts are counted from. The seats being drawn must be
    empty in it.
    """
    first, last = season
    load: Counter = Counter()
    held: defaultdict = defaultdict(set)
    occupied: defaultdict = defaultdict(set)
    for seat in everything:
        if seat.player_id is None:
            continue
        if seat.date is None or first <= seat.date <= last:
            load[seat.player_id] += 1
        if seat.date is not None:
            held[seat.player_id].add(seat.date)
        occupied[shift_key(seat)].add(seat.player_id)

    families = {p.id: frozenset(parent.id for parent in p.parents) for p in players.values()}
    pool = [
        Candidate(p.id, families[p.id], load[p.id], set(held[p.id]), elsewhere.get(p.id, ()))
        for p in players.values() if not p.exempt and p.parents
    ]
    seats = [
        Seat(s.ref, s.date, s.start_time, s.end_time, s.station_id, key=shift_key(s))
        for s in empty
    ]
    return assign(seats, pool, seed=seed, occupied=occupied, families=families)
