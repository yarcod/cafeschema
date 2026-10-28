"""The parent app's agenda: one card per shift, grouped by day.

Shared by Hela laget, the admin Pass tab and Granska's phone preview, so all
three read a schedule exactly the same way.
"""

from __future__ import annotations

from datetime import date

from .models import Slot


def shift_key(slot: Slot) -> tuple:
    """Which shift a seat belongs to.

    The station is part of it, by id once linked and by its text until then,
    so two stations at one venue at the same time are two shifts. An undated
    seat's note is too: the import keeps the weekday there ("lör"), and it's
    the only thing telling two undated shifts at the same time apart.
    """
    station = (
        ("id", slot.station_id) if slot.station_id is not None
        else ("text", slot.venue or "", slot.station or "")
    )
    extra = (slot.note or "") if slot.date is None else None
    return (slot.date, slot.start_time, slot.end_time, slot.duty_name, station, extra)


def group_shifts(slots: list[Slot]) -> list[dict]:
    """Collapse per-person slot rows into one entry per staffed shift.

    The schedule staffs a single shift with several people, stored as one
    Slot each so they can be swapped individually; the agenda reads far
    better as one card per shift listing everyone on it.
    """
    shifts: dict[tuple, dict] = {}
    for slot in sorted(
        slots,
        key=lambda s: (s.date or date.max, s.start_time, s.duty_name, s.station or ""),
    ):
        shift = shifts.setdefault(
            shift_key(slot),
            {
                "date": slot.date,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
                "duty_name": slot.duty_name,
                "venue": slot.venue,
                "station": slot.station,
                "slots": [],
            },
        )
        shift["slots"].append(slot)
    return list(shifts.values())


def group_days(shifts: list[dict]) -> list[dict]:
    """Group shifts by date, and tell each one where it sits in its day.

    Being first or last on a day carries real duties (opening up, locking
    after), so position within the day is information the page has to show,
    not decoration.
    """
    days: dict[date, list[dict]] = {}
    for shift in shifts:
        days.setdefault(shift["date"], []).append(shift)
    out = []
    for day, day_shifts in days.items():
        for index, shift in enumerate(day_shifts, start=1):
            shift["position"] = index
            shift["of"] = len(day_shifts)
            shift["is_first"] = index == 1
            shift["is_last"] = index == len(day_shifts)
        out.append({"date": day, "shifts": day_shifts})
    return out
