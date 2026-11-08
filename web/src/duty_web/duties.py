"""What parents need to know about a duty beyond what the schedule file says.

The trainer's spreadsheet names an activity and, for the arena duties, an
arena. It doesn't say which café inside that building. That is the team's
own knowledge, it changes about once a season.

The migration creates venues and stations from DUTY_PROFILES the first time
it runs (while the stations table is empty); after that the places live in
the database (✎ → Platser). The table stays as the way a schedule import and
the migration's linking find the station for a known activity.

Which instruction a station links is never decided here: it is set under
Platser, where it can change without a deploy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DutyProfile:
    venue: str
    station: str


DUTY_PROFILES: dict[str, DutyProfile] = {
    "Bästkustcupen": DutyProfile(
        venue="Mölnlycke idrottshall",
        station="Café",
    ),
    "Cafépass": DutyProfile(
        venue="Wallenstam arena",
        station="Café Arena B, nedre plan",
    ),
    # Same building, other café — a name of its own rather than a "Cafépass"
    # that happens to sit elsewhere, so an import can place it. The plain
    # name keeps meaning Arena B: renaming it would change every slot of the
    # season and the colour parents already recognise it by.
    "Cafépass Arena A": DutyProfile(
        venue="Wallenstam arena",
        station="Café Arena A, övre plan",
    ),
}

# The spreadsheet's own names for duties that are now called something else.
# Both arena-hosting terms are the same duty to a parent, so they share a
# name; the undated block already tells VT27 apart from the autumn shifts.
RENAMED_DUTIES: dict[str, str] = {
    "Arena värdskap höst": "Cafépass",
    "Arena värdskap vinter": "Cafépass",
}

_BY_KEY = {name.casefold(): name for name in DUTY_PROFILES}
_RENAMES_BY_KEY = {name.casefold(): new for name, new in RENAMED_DUTIES.items()}


def canonical_duty_name(name: str | None) -> str:
    """The name a duty goes by now. Anything unrenamed passes through."""
    stripped = " ".join((name or "").split())
    return _RENAMES_BY_KEY.get(stripped.casefold(), stripped)


def profile_for(name: str | None) -> DutyProfile | None:
    """The profile for a duty, under its current name or its old one.

    Accepting the old spelling keeps a database that hasn't been migrated
    yet from losing its place.
    """
    canonical = canonical_duty_name(name)
    key = _BY_KEY.get(canonical.casefold())
    return DUTY_PROFILES[key] if key is not None else None


@dataclass(frozen=True)
class WeekendPattern:
    """A repeating set of sessions: what a weekend of one duty looks like."""

    name: str
    title: str
    activity: str
    sessions: dict[int, tuple[tuple[str, str], ...]]  # weekday (Mon=0) -> (start, end)
    seats: int
    seat_choices: tuple[int, ...]


# The Saturday and Sunday sessions of the café weekend are the same five.
CAFE_SATURDAY = (("08:30", "10:30"), ("10:30", "12:30"), ("12:30", "14:30"),
                 ("14:30", "16:30"), ("16:30", "18:30"))

PATTERNS: dict[str, WeekendPattern] = {
    "cafe_weekend": WeekendPattern(
        name="cafe_weekend", title="Cafépass, helg", activity="Cafépass",
        sessions={4: (("17:30", "19:30"),), 5: CAFE_SATURDAY, 6: CAFE_SATURDAY},
        seats=2, seat_choices=(2, 3),
    ),
}


def pattern_sessions(pattern: WeekendPattern, days: list[date]) -> list[tuple[date, str, str]]:
    """The pattern's sessions on the given days; days it has none for add nothing."""
    return [
        (day, start, end)
        for day in sorted(days)
        for start, end in pattern.sessions.get(day.weekday(), ())
    ]
