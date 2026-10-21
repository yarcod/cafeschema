"""Pure scheduling logic.

The system keeps no record of what it has already sent (see spec D2), so
"should this go out?" is answered purely by arithmetic on dates. Everything
here is deterministic given an explicit `today`, which is what makes the
whole season replayable via `--date`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time
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
    # By time within the day too, so a day's several shifts are sent — and
    # printed by --dry-run — in the order they actually happen.
    return sorted(due, key=lambda pair: (pair[0].due, pair[0].start_time or time.min))


def previous_occurrence(
    occurrences: Sequence[Occurrence],
    occ: Occurrence,
    *,
    max_gap_days: int,
) -> Occurrence | None:
    """The last shift of the previous duty day, if it is close enough.

    Only shifts of `occ`'s own team count. Used to tell the incoming group who to collect keys from. Across a
    season break the previous group is not a meaningful handover, so
    anything further back than `max_gap_days` is treated as no predecessor
    at all.

    "Previous duty *day*", not previous shift: a day holding three shifts
    hands its keys on from whoever locked up, so the day's later shifts
    deliberately find no predecessor rather than pointing at colleagues who
    were there a few hours earlier. Among that earlier day's shifts the
    latest one wins, for the same reason — they are the ones who locked up.
    """
    # Only the same team: keys and cafés are shared, but the Kansli handles
    # each team on its own, so a handover never points at another team's
    # parents. An untagged roster has team "" throughout and is unaffected.
    earlier = [o for o in occurrences if o.due < occ.due and o.team == occ.team]
    if not earlier:
        return None
    last_day = max(o.due for o in earlier)
    if (occ.due - last_day).days > max_gap_days:
        return None
    # time.min for a roster with no times at all, which is every tabular
    # source: one occurrence per date, so there is nothing to order anyway.
    return max(
        (o for o in earlier if o.due == last_day),
        key=lambda o: o.start_time or time.min,
    )
