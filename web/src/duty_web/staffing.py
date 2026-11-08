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

A candidate isn't eligible at all if they already have a shift that day or
that weekend (Friday to Sunday), are already on this very shift, or if their
family has a shift on another team at the same time. Families can still
swap into such a shift themselves.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, time, timedelta

NO_ONE = "inga spelare att välja bland (alla är undantagna eller saknar föräldrar)"
BUSY = "alla kvarvarande har redan pass den dagen"
ON_SHIFT = "alla kvarvarande står redan på passet"
BUSY_WEEKEND = "alla kvarvarande har redan pass den helgen"
ELSEWHERE = "alla kvarvarande har redan pass samtidigt i ett annat lag"
CLASHES = "alla kvarvarande har redan pass den dagen, den helgen eller samtidigt i ett annat lag"


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
    # The family's shifts on other teams, as (day, start, end).
    elsewhere: tuple[tuple[date, time, time], ...] = ()


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


def _weekend(day: date) -> date | None:
    """The Monday of a Friday-to-Sunday day's week; None on other days."""
    return _monday(day) if day.weekday() >= 4 else None


def same_weekend(day: date, days: Iterable[date]) -> bool:
    weekend = _weekend(day)
    return weekend is not None and any(_weekend(other) == weekend for other in days)


def clashes(
    day: date | None, start: time, end: time, shifts: Iterable[tuple[date, time, time]]
) -> bool:
    """Whether any shift overlaps this one. Back to back is not an overlap."""
    return day is not None and any(
        other == day and other_start < end and start < other_end
        for other, other_start, other_end in shifts
    )


def _stop(seat: Seat, candidate: Candidate, mates: set[int], days: set[date]) -> str | None:
    """Why a candidate can't take the seat, or None if they can."""
    if seat.day is None:
        return ON_SHIFT if candidate.player_id in mates else None
    if candidate.player_id in mates or seat.day in days:
        return BUSY
    if same_weekend(seat.day, days):
        return BUSY_WEEKEND
    if clashes(seat.day, seat.start, seat.end, candidate.elsewhere):
        return ELSEWHERE
    return None


def _reason(stops: set[str]) -> str:
    """One reason for an unfilled seat. Booked that day is also booked that weekend."""
    if BUSY_WEEKEND in stops:
        stops = stops - {BUSY}
    return stops.pop() if len(stops) == 1 else CLASHES


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
        stops = {c.player_id: _stop(seat, c, mates, held[c.player_id]) for c in pool}
        eligible = [c for c in pool if stops[c.player_id] is None]
        if not eligible:
            reason = NO_ONE if not pool else _reason({s for s in stops.values() if s})
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
