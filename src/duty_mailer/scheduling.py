"""Pure scheduling logic.

The system keeps no record of what it has already sent (see spec D2), so
"should this go out?" is answered purely by arithmetic on dates. Everything
here is deterministic given an explicit `today`, which is what makes the
whole season replayable via `--date`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .models import Occurrence, Role


def date_in_zone(instant: datetime, timezone: str) -> date:
    """The calendar date at `instant`, as seen in `timezone`."""
    return instant.astimezone(ZoneInfo(timezone)).date()


def today_in(timezone: str) -> date:
    """Today's date in `timezone`.

    The only clock read in the library. The roster holds local calendar
    dates, so a UTC-derived date would be wrong either side of midnight.
    """
    return date_in_zone(datetime.now(tz=ZoneInfo("UTC")), timezone)


def role_for(offset: int, lead_days: tuple[int, ...]) -> Role:
    """Which message a given lead time carries.

    Decided by role rather than by literal value, so (7, 1), (14, 2) and
    (3,) all behave sensibly. A lone reminder is always the nudge; when
    there are two, the earlier one is the heads-up.
    """
    if len(lead_days) < 2:
        return Role.PAMINNELSE
    return Role.FORHANDSBESKED if offset == max(lead_days) else Role.PAMINNELSE


def due_on(
    occurrences: Sequence[Occurrence], today: date
) -> list[tuple[Occurrence, Role]]:
    """Every (occurrence, role) whose reminder falls exactly on `today`."""
    due: list[tuple[Occurrence, Role]] = []
    for occ in occurrences:
        days_left = (occ.due - today).days
        for offset in occ.lead_days:
            if days_left == offset:
                due.append((occ, role_for(offset, occ.lead_days)))
    return sorted(due, key=lambda pair: pair[0].due)


def previous_occurrence(
    occurrences: Sequence[Occurrence],
    occ: Occurrence,
    *,
    max_gap_days: int,
) -> Occurrence | None:
    """The occurrence immediately before `occ`, if it is close enough.

    Used to tell the incoming group who to collect keys from. Across a
    season break the previous group is not a meaningful handover, so
    anything further back than `max_gap_days` is treated as no predecessor
    at all.
    """
    earlier = [o for o in occurrences if o.due < occ.due]
    if not earlier:
        return None
    candidate = max(earlier, key=lambda o: o.due)
    if (occ.due - candidate.due).days > max_gap_days:
        return None
    return candidate
