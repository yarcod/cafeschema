"""Fair staffing (spec D7): fewest shifts first, chance only among equals.

Pure: the action loads the team, this decides, and the same seed always gives
the same answer, so a plan can be reviewed and then applied unchanged.

Seats are filled in date order (undated last). For each one, the eligible
candidate with the lowest rank goes first:

1. fewest shifts this season. Fairness comes before everything else, so a
   sibling goes first only among equally loaded players;
2. a sibling of someone already on the shift, since families like to come
   together;
3. no shift the week before or after, so a family isn't asked two weekends
   running;
4. chance, from random.Random(seed).

A candidate who already has a shift that day, or is already on this very
shift, isn't eligible at all.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, time, timedelta

NO_ONE = "inga spelare att välja bland (alla är undantagna eller saknar föräldrar)"
BUSY = "alla kvarvarande har redan pass den dagen"
ON_SHIFT = "alla kvarvarande står redan på passet"


@dataclass(frozen=True)
class Seat:
    slot_id: int
    day: date | None
    start: time
    end: time
    station_id: int | None
    # Which shift the seat is on (agenda.shift_key). Without one, the seat's
    # own day, times and station stand in, which is enough for pure tests.
    key: tuple | None = None

    @property
    def shift(self) -> tuple:
        if self.key is not None:
            return self.key
        return (self.day, self.start, self.end, self.station_id)


@dataclass
class Candidate:
    player_id: int
    family: frozenset[int]
    load: int = 0
    days: set[date] = field(default_factory=set)


@dataclass(frozen=True)
class Filled:
    slot_id: int
    player_id: int | None
    reason: str = ""


def season_of(day: date) -> tuple[date, date]:
    """1 Aug – 31 Jul around a day: the club's season."""
    first = day.year if day.month >= 8 else day.year - 1
    return date(first, 8, 1), date(first + 1, 7, 31)


def spread(loads: Iterable[int]) -> tuple[float, int]:
    """(mean, max − min) of the shifts per player."""
    values = list(loads)
    if not values:
        return 0.0, 0
    return sum(values) / len(values), max(values) - min(values)


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _week_next_door(day: date, days: Iterable[date]) -> bool:
    week = _monday(day)
    return any(abs((_monday(other) - week).days) == 7 for other in days)


def assign(
    seats: Iterable[Seat],
    candidates: Iterable[Candidate],
    *,
    seed: int,
    occupied: Mapping[tuple, Iterable[int]] | None = None,
    families: Mapping[int, frozenset[int]] | None = None,
) -> list[Filled]:
    """Fill the seats fairly, in seat order.

    occupied: the players already on each shift. families: every team
    player's parent ids, so a sibling is recognised even when the one already
    on the shift isn't a candidate (a leader's child, say).
    """
    rng = random.Random(seed)
    pool = sorted(candidates, key=lambda c: c.player_id)
    load = {c.player_id: c.load for c in pool}
    held = {c.player_id: set(c.days) for c in pool}
    family = {**(families or {}), **{c.player_id: c.family for c in pool}}
    on_shift = {key: set(ids) for key, ids in (occupied or {}).items()}

    results = []
    for seat in sorted(seats, key=lambda s: (s.day is None, s.day or date.min, s.start, s.slot_id)):
        mates = on_shift.setdefault(seat.shift, set())
        eligible = [
            c for c in pool
            if c.player_id not in mates and (seat.day is None or seat.day not in held[c.player_id])
        ]
        if not eligible:
            reason = NO_ONE if not pool else (BUSY if seat.day is not None else ON_SHIFT)
            results.append(Filled(seat.slot_id, None, reason))
            continue
        mate_parents = frozenset().union(*(family.get(m, frozenset()) for m in mates))

        def rank(c: Candidate) -> tuple:
            sibling = bool(c.family & mate_parents)
            next_door = seat.day is not None and _week_next_door(seat.day, held[c.player_id])
            return (load[c.player_id], not sibling, next_door, rng.random())

        best = min(eligible, key=rank)
        load[best.player_id] += 1
        if seat.day is not None:
            held[best.player_id].add(seat.day)
        mates.add(best.player_id)
        results.append(Filled(seat.slot_id, best.player_id))
    return results
