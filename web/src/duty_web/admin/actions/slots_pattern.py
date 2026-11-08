"""Lägg till cafépass: a whole weekend of sessions as väntande Pass, in one step.

The pattern (duties.PATTERNS) says which sessions a weekend has; the person
or agent may adjust any of them by sending rows instead. Staged exactly like
slots.create, so nothing reaches parents before Genomför.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from flask import request

from ...duties import PATTERNS, pattern_sessions, profile_for
from ...models import Station
from ...places import find_station, find_venue
from .. import pending
from ..slot_ops import parse_clock, shift_label
from .base import Action, Plan, ValidationError, register
from .slots_create import (
    MAX_DAYS, MAX_TOTAL, _date, _days_from, _overlaps, _station,
)

MAX_FORM_ROWS = 100
DEFAULT_PATTERN = "cafe_weekend"
WEEKEND_CHIPS = 4


@dataclass
class PatternInput:
    days: list[str]
    pattern: str = DEFAULT_PATTERN
    station_id: int | None = None
    seats: int = 2
    rows: list[dict] = field(default_factory=list)


def _rows_from_form(fields) -> list[dict]:
    rows = []
    seen = False
    for i in range(MAX_FORM_ROWS):
        day = (fields.get(f"row-{i}-day") or "").strip()
        if not day:
            continue
        seen = True
        if fields.get(f"row-{i}-skip"):
            continue
        rows.append({
            "_row": i, "day": day,
            "start": (fields.get(f"row-{i}-start") or "").strip(),
            "end": (fields.get(f"row-{i}-end") or "").strip(),
        })
    if seen and not rows:
        raise ValidationError({"rows": "Lägg till minst ett pass"})
    return rows


def default_station(session, activity: str) -> Station | None:
    """The station the activity's profile names, if it exists yet."""
    profile = profile_for(activity)
    venue = find_venue(session, profile.venue) if profile else None
    return find_station(session, venue, profile.station) if venue else None


def _weekends(today: date) -> list[tuple[str, str, str]]:
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    chips = []
    for n in range(WEEKEND_CHIPS):
        start = friday + timedelta(days=7 * n)
        chips.append((f"v {start.isocalendar().week}", start.isoformat(),
                      (start + timedelta(days=2)).isoformat()))
    return chips


@register
class CreatePatternSlots(Action):
    name = "slots.from_pattern"
    title = "Lägg till cafépass"
    scope = "team"
    Input = PatternInput
    template = "admin/actions/slots_pattern.html"
    done_endpoint = "admin.pass_tab"
    immediate = True
    agent_note = (
        "Skapar en hel cafépasshelg som väntande pass. Skicka date_from/date_to (eller days); "
        "fredag, lördag och söndag får sina pass ur mönstret i GET /admin/api (patterns). "
        "Skicka rows [{day, start, end}] för att ändra tiderna."
    )

    def parse(self, fields, files):
        if hasattr(fields, "getlist"):
            rows = _rows_from_form(fields)
        else:
            rows = fields.get("rows") or []
            if not isinstance(rows, list):
                raise ValidationError({"rows": "Skicka rows som en lista"})
        errors = {}
        station_raw = str(fields.get("station_id") or "").strip()
        seats_raw = fields.get("seats")
        seats = 2
        if seats_raw not in (None, ""):
            try:
                seats = int(str(seats_raw).strip())
            except ValueError:
                errors["seats"] = "Ange antal föräldrar per pass"
        if station_raw and not station_raw.isdigit():
            errors["station_id"] = "Välj en plats"
        if errors:
            raise ValidationError(errors)
        return PatternInput(
            days=_days_from(fields),
            pattern=str(fields.get("pattern") or DEFAULT_PATTERN),
            station_id=int(station_raw) if station_raw else None,
            seats=seats, rows=rows,
        )

    def form_context(self, session, ctx):
        pattern = PATTERNS[DEFAULT_PATTERN]
        values = request.form if request.method == "POST" else request.args
        try:
            days = sorted({_date(day, "days") for day in _days_from(values)})
        except ValidationError:
            days = []
        posted = {
            i: values for i in range(MAX_FORM_ROWS) if values.get(f"row-{i}-day")
        }
        if posted:
            sessions = [
                (i, _date(values[f"row-{i}-day"], "days") if _valid(values[f"row-{i}-day"]) else None,
                 values.get(f"row-{i}-start", ""), values.get(f"row-{i}-end", ""),
                 bool(values.get(f"row-{i}-skip")))
                for i in posted
            ]
        else:
            sessions = [
                (i, day, start, end, False)
                for i, (day, start, end) in enumerate(pattern_sessions(pattern, days))
            ]
        columns: dict = {}
        for i, day, start, end, skip in sessions:
            columns.setdefault(day, []).append(
                {"i": i, "day": day, "start": start, "end": end, "skip": skip}
            )
        station = default_station(session, pattern.activity)
        stations = session.query(Station).join(Station.venue).order_by(
            Station.venue_id, Station.name
        ).all()
        chosen = values.get("station_id") or (station.id if station else "")
        return {
            "pattern": pattern,
            "columns": list(columns.items()),
            "count": sum(1 for _, _, _, _, skip in sessions if not skip),
            "stations": [(s.id, f"{s.name} ({s.venue.name})") for s in stations],
            "chosen_station": str(chosen),
            "chosen_seats": str(values.get("seats") or pattern.seats),
            "chips": _weekends(date.today()),
            "next_weekend": _weekends(date.today())[0],
        }

    def plan(self, session, ctx, inp):
        pattern = PATTERNS.get(inp.pattern)
        if pattern is None:
            raise ValidationError({"pattern": "Okänt mönster"})
        if inp.seats not in pattern.seat_choices:
            choices = " eller ".join(str(n) for n in pattern.seat_choices)
            raise ValidationError({"seats": f"Välj {choices} föräldrar per pass"})
        days = sorted({_date(day, "days") for day in inp.days})
        if not days:
            raise ValidationError({"days": "Välj minst en dag"})
        if len(days) > MAX_DAYS:
            raise ValidationError({"days": f"Högst {MAX_DAYS} dagar åt gången"})
        if not any(day.weekday() in pattern.sessions for day in days):
            raise ValidationError(
                {"days": "Inga fredagar, lördagar eller söndagar i de valda dagarna"}
            )

        if inp.station_id is None:
            station = default_station(session, pattern.activity)
            if station is None:
                raise ValidationError({"station_id": "Välj en plats"})
        else:
            station = _station(session, inp.station_id, "station_id")

        if inp.rows:
            given = []
            for position, row in enumerate(inp.rows):
                if not isinstance(row, dict):
                    raise ValidationError({f"rows[{position}]": "Ogiltig rad"})
                i = row["_row"] if isinstance(row.get("_row"), int) else position
                day = _date(row.get("day"), f"rows[{i}].day")
                if day not in days:
                    raise ValidationError({f"rows[{i}].day": "Dagen är inte en av de valda dagarna"})
                given.append((i, day, row.get("start"), row.get("end")))
        else:
            given = [
                (i, day, start, end)
                for i, (day, start, end) in enumerate(pattern_sessions(pattern, days))
            ]

        shifts = []
        for i, day, raw_start, raw_end in given:
            start, end = parse_clock(raw_start), parse_clock(raw_end)
            if start is None:
                raise ValidationError({f"rows[{i}].start": "Ange tid som TT:MM"})
            if end is None:
                raise ValidationError({f"rows[{i}].end": "Ange tid som TT:MM"})
            if end <= start:
                raise ValidationError({f"rows[{i}].end": "Sluttiden måste vara efter starttiden"})
            shifts.append((day, start, end, station, inp.seats))
        total = inp.seats * len(shifts)
        if total > MAX_TOTAL:
            raise ValidationError({"rows": f"Högst {MAX_TOTAL} platser åt gången"})
        shifts.sort(key=lambda shift: (shift[0], shift[1]))

        plan = Plan()
        for day, start, end, _, seats in shifts:
            plan.add(
                "add", "shift", shift_label(day, start, end, pattern.activity, station.name),
                f"{seats} platser · {station.venue.name}",
            )
        plan.warnings.extend(_overlaps(session, ctx.team.id, shifts))
        plan.notes.append(f"{total} lediga platser väntar på att bemannas.")
        plan.preview_dates.update(shift[0] for shift in shifts)

        team_id, activity, station_id = ctx.team.id, pattern.activity, station.id
        staged = [(day, start, end) for day, start, end, _, _ in shifts]
        seats = inp.seats

        def write(s):
            for day, start, end in staged:
                s.add(pending.new_pass(team_id, activity, day, start, end, station_id, seats))

        plan.writes.append(write)
        return plan


def _valid(raw: object) -> bool:
    try:
        date.fromisoformat(str(raw).strip())
    except ValueError:
        return False
    return True
