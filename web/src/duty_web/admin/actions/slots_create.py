"""Lägg till pass (spec Flows): days × shift rows → empty seats.

Each row is a shift (start, end, station, seats) on every picked day, or on
the weekdays it names. Every seat becomes its own unassigned Slot, so it can
be staffed and swapped on its own, linked to its station with the station's
names copied to the slot's text columns for parents, /api/schedule and the
mailer. Staffing is the next step, under Pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ...duties import canonical_duty_name
from ...models import ORIGIN_ADMIN, Slot, Station, Venue
from ..slot_ops import day_label, parse_clock, shift_label, slot_label
from .base import Action, Plan, ValidationError, register

MAX_DAYS = 31
MAX_ROWS = 10
MAX_SEATS = 10
MAX_TOTAL = 300
MAX_ACTIVITY = 80
FORM_ROWS = 4
WEEKDAY_KEYS = ("mån", "tis", "ons", "tor", "fre", "lör", "sön")


@dataclass
class CreateSlotsInput:
    days: list[str]
    activity: str
    rows: list[dict]


def _date(raw: object, field_name: str) -> date:
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise ValidationError({field_name: "Ange datum som ÅÅÅÅ-MM-DD"}) from None


def _days_from(fields) -> list[str]:
    """The picked days: a list, or every day from date_from to date_to."""
    raw = fields.getlist("days") if hasattr(fields, "getlist") else fields.get("days")
    if raw:
        return [str(day).strip() for day in (raw if isinstance(raw, list) else [raw])]
    if not fields.get("date_from"):
        raise ValidationError({"days": "Välj minst en dag"})
    start = _date(fields.get("date_from"), "date_from")
    end = _date(fields.get("date_to") or fields.get("date_from"), "date_to")
    if end < start:
        raise ValidationError({"date_to": "Slutdagen måste vara samma dag eller senare"})
    if (end - start).days >= MAX_DAYS:
        raise ValidationError({"date_to": f"Högst {MAX_DAYS} dagar åt gången"})
    return [(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)]


def _rows_from_form(fields) -> list[dict]:
    rows = []
    for i in range(MAX_ROWS):
        start = (fields.get(f"row-{i}-start") or "").strip()
        end = (fields.get(f"row-{i}-end") or "").strip()
        station = (fields.get(f"row-{i}-station_id") or "").strip()
        if not (start or end or station):
            continue
        rows.append({
            "_row": i, "start": start, "end": end, "station_id": station,
            "seats": (fields.get(f"row-{i}-seats") or "1").strip(),
            "on_days": fields.getlist(f"row-{i}-on_days"),
        })
    return rows


def _station(session, raw: object, field_name: str) -> Station:
    try:
        station = session.get(Station, int(str(raw).strip()))
    except ValueError:
        raise ValidationError({field_name: "Välj en station"}) from None
    if station is None:
        raise ValidationError({field_name: "Okänd station"})
    return station


def _seats(raw: object, field_name: str) -> int:
    try:
        seats = int(str(raw).strip())
    except ValueError:
        raise ValidationError({field_name: "Ange antal platser"}) from None
    if not 1 <= seats <= MAX_SEATS:
        raise ValidationError({field_name: f"Mellan 1 och {MAX_SEATS} platser"})
    return seats


def _on_days(raw: object, days: list[date], field_name: str) -> list[date]:
    """Which of the picked days a row applies to: all, or the ones it names."""
    if not raw:
        return list(days)
    picked = set()
    for value in raw if isinstance(raw, list) else [raw]:
        text = str(value).strip()
        key = text[:3].casefold()
        if key in WEEKDAY_KEYS:
            picked.update(day for day in days if day.weekday() == WEEKDAY_KEYS.index(key))
            continue
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValidationError({field_name: f"Okänd dag: {text}"}) from None
        if day not in days:
            raise ValidationError({field_name: f"{day_label(day)} är inte en av de valda dagarna"})
        picked.add(day)
    if not picked:
        raise ValidationError({field_name: "Ingen av de valda dagarna är en sådan veckodag"})
    return sorted(picked)


def _overlaps(session, team_id: int, shifts: list[tuple]) -> list[str]:
    days = sorted({shift[0] for shift in shifts})
    existing = (
        session.query(Slot)
        .filter(Slot.team_id == team_id, Slot.date.in_(days), Slot.station_id.isnot(None))
        .order_by(Slot.date, Slot.start_time, Slot.id)
        .all()
    )
    found: list[str] = []
    for slot in existing:
        for day, start, end, station, _ in shifts:
            if (
                slot.date == day and slot.station_id == station.id
                and slot.start_time < end and start < slot.end_time
            ):
                label = slot_label(slot)
                if label not in found:
                    found.append(label)
                break
    return [f"Laget har redan ett pass som överlappar: {label}" for label in found]


@register
class CreateSlots(Action):
    name = "slots.create"
    title = "Lägg till pass"
    scope = "team"
    Input = CreateSlotsInput
    template = "admin/actions/slots_create.html"
    done_endpoint = "admin.pass_tab"

    def parse(self, fields, files):
        if hasattr(fields, "getlist"):
            rows = _rows_from_form(fields)
        else:
            rows = fields.get("rows") or []
            if not isinstance(rows, list):
                raise ValidationError({"rows": "Skicka rows som en lista"})
        return CreateSlotsInput(
            days=_days_from(fields), activity=str(fields.get("activity") or ""), rows=rows
        )

    def form_context(self, session, ctx):
        stations = session.query(Station).join(Venue).order_by(Venue.name, Station.name).all()
        activities = sorted({
            canonical_duty_name(name)
            for (name,) in session.query(Slot.duty_name).filter(Slot.team_id == ctx.team.id).distinct()
        })
        today = date.today()
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        return {
            "stations": [(s.id, f"{s.name} ({s.venue.name})") for s in stations],
            "activities": activities,
            "weekdays": WEEKDAY_KEYS,
            "form_rows": FORM_ROWS,
            "shortcuts": [
                ("Den här helgen", saturday.isoformat(), (saturday + timedelta(days=1)).isoformat()),
                ("Nästa helg", (saturday + timedelta(days=7)).isoformat(),
                 (saturday + timedelta(days=8)).isoformat()),
                ("I dag", today.isoformat(), today.isoformat()),
            ],
        }

    def plan(self, session, ctx, inp):
        days = sorted({_date(day, "days") for day in inp.days})
        if not days:
            raise ValidationError({"days": "Välj minst en dag"})
        if len(days) > MAX_DAYS:
            raise ValidationError({"days": f"Högst {MAX_DAYS} dagar åt gången"})
        activity = canonical_duty_name(inp.activity)
        if not activity:
            raise ValidationError({"activity": "Skriv vad passen gäller, t.ex. Bästkustcupen"})
        if len(activity) > MAX_ACTIVITY:
            raise ValidationError({"activity": f"Högst {MAX_ACTIVITY} tecken"})
        if not inp.rows:
            raise ValidationError({"rows": "Lägg till minst ett pass"})
        if len(inp.rows) > MAX_ROWS:
            raise ValidationError({"rows": f"Högst {MAX_ROWS} rader åt gången"})

        shifts = []
        for position, row in enumerate(inp.rows):
            if not isinstance(row, dict):
                raise ValidationError({f"rows[{position}]": "Ogiltig rad"})
            # The HTML form numbers its own rows; an agent's row is its position.
            i = row["_row"] if isinstance(row.get("_row"), int) else position
            start, end = parse_clock(row.get("start")), parse_clock(row.get("end"))
            if start is None:
                raise ValidationError({f"rows[{i}].start": "Ange tid som TT:MM"})
            if end is None:
                raise ValidationError({f"rows[{i}].end": "Ange tid som TT:MM"})
            if end <= start:
                raise ValidationError({f"rows[{i}].end": "Sluttiden måste vara efter starttiden"})
            station = _station(session, row.get("station_id"), f"rows[{i}].station_id")
            seats = _seats(row.get("seats", 1), f"rows[{i}].seats")
            for day in _on_days(row.get("on_days"), days, f"rows[{i}].on_days"):
                shifts.append((day, start, end, station, seats))
        total = sum(shift[4] for shift in shifts)
        if total > MAX_TOTAL:
            raise ValidationError({"rows": f"Högst {MAX_TOTAL} platser åt gången"})
        shifts.sort(key=lambda shift: (shift[0], shift[1], shift[3].name))

        plan = Plan()
        for day, start, end, station, seats in shifts:
            plan.add(
                "add", "shift", shift_label(day, start, end, activity, station.name),
                f"{seats} {'plats' if seats == 1 else 'platser'} · {station.venue.name}",
            )
        plan.warnings.extend(_overlaps(session, ctx.team.id, shifts))
        plan.notes.append(
            f"{total} {'ledig plats' if total == 1 else 'lediga platser'} att bemanna: "
            "välj dem under Pass och tryck Bemanna."
        )
        plan.preview_dates.update(shift[0] for shift in shifts)

        team_id = ctx.team.id
        seats_to_add = [
            (day, start, end, station.id, station.venue.name, station.name, seats)
            for day, start, end, station, seats in shifts
        ]

        def write(s):
            for day, start, end, station_id, venue, station_name, seats in seats_to_add:
                for _ in range(seats):
                    s.add(Slot(
                        team_id=team_id, date=day, start_time=start, end_time=end,
                        station=station_name, venue=venue, duty_name=activity,
                        station_id=station_id, origin=ORIGIN_ADMIN,
                    ))

        plan.writes.append(write)
        return plan
