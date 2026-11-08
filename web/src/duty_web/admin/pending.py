"""Staged ("väntande") Pass edits and the schedule as it would look with them.

Nothing here writes to slots. The table, Granska and the draw all read the
effective schedule, so a staged change is visible to the ansvarig long before
any family is told.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, time, timedelta

from sqlalchemy.orm import Session

from ..models import (
    PENDING_EDIT,
    PENDING_NEW,
    PENDING_REMOVE,
    PendingPass,
    Player,
    Slot,
    Station,
)

FIELD_NAMES = ("date", "start_time", "end_time", "station_id", "note")


@dataclass
class SeatView:
    """One seat, live or staged. Same attribute names as Slot, so
    agenda.shift_key and group_shifts take it unchanged."""

    ref: int
    slot_id: int | None
    entry_id: int | None
    date: date | None
    start_time: time
    end_time: time
    duty_name: str
    venue: str
    station: str
    station_id: int | None
    note: str | None
    player_id: int | None
    child_name: str | None
    drawn: bool = False


@dataclass(frozen=True)
class Conflict:
    missing: bool
    fields: tuple[str, ...]
    seat_ids: tuple[int, ...]


def team_entries(session: Session, team_id: int) -> list[PendingPass]:
    return (
        session.query(PendingPass)
        .filter(PendingPass.team_id == team_id)
        .order_by(PendingPass.id)
        .all()
    )


def pending_count(session: Session, team_id: int) -> int:
    return session.query(PendingPass).filter(PendingPass.team_id == team_id).count()


def encode_slot(slot: Slot) -> dict:
    """The five field values of one live seat, in the form entries store."""
    return {
        "date": slot.date.isoformat() if slot.date else None,
        "start_time": slot.start_time.strftime("%H:%M"),
        "end_time": slot.end_time.strftime("%H:%M"),
        "station_id": slot.station_id,
        "note": slot.note,
    }


def conflict_of(entry, live: dict[int, Slot], player_ids: set[int]) -> Conflict | None:
    """What no longer matches what the entry replaced, or None.

    Each part is judged on its own: a swap on seat B must not flag a staged
    time, so fields are compared on the first live seat only and seats only
    where the entry actually places someone.
    """
    seats = entry.seats
    missing = any(seat["slot_id"] is not None and seat["slot_id"] not in live for seat in seats)
    # A seat whose owner is unchanged places nobody, so only a real
    # placement can point at a player who has since left.
    missing = missing or any(
        seat["to"] is not None and seat["to"] != seat["from"] and seat["to"] not in player_ids
        for seat in seats
    )

    first = next((live[s["slot_id"]] for s in seats if s["slot_id"] in live), None)
    fields: tuple[str, ...] = ()
    if first is not None:
        now = encode_slot(first)
        fields = tuple(name for name, change in entry.fields.items() if now[name] != change["from"])

    seat_ids = tuple(
        seat["slot_id"]
        for seat in seats
        if seat["slot_id"] in live
        and live[seat["slot_id"]].player_id != seat["from"]
        and (seat["to"] != seat["from"] or entry.kind == PENDING_REMOVE)
    )
    if not (missing or fields or seat_ids):
        return None
    return Conflict(missing=missing, fields=fields, seat_ids=seat_ids)


def _view(slot: Slot) -> SeatView:
    return SeatView(
        ref=slot.id, slot_id=slot.id, entry_id=None, date=slot.date,
        start_time=slot.start_time, end_time=slot.end_time, duty_name=slot.duty_name,
        venue=slot.venue, station=slot.station, station_id=slot.station_id,
        note=slot.note, player_id=slot.player_id, child_name=slot.child_name,
    )


def _apply_fields(view: SeatView, entry, stations: dict[int, Station]) -> SeatView:
    for name, change in entry.fields.items():
        value = change["to"]
        if name == "date":
            view.date = date.fromisoformat(value) if value else None
        elif name in ("start_time", "end_time"):
            setattr(view, name, time.fromisoformat(value))
        elif name == "note":
            view.note = value
        elif name == "station_id":
            view.station_id = value
            row = stations.get(value) if value is not None else None
            view.station = row.name if row else ""
            if row:
                view.venue = row.venue.name
    return view


def _staged_seats(entry, live: dict[int, Slot], stations: dict[int, Station]) -> list[SeatView]:
    if entry.kind == PENDING_NEW:
        blank = SeatView(
            ref=0, slot_id=None, entry_id=entry.id, date=None, start_time=time(0),
            end_time=time(0), duty_name=entry.duty_name or "", venue="", station="",
            station_id=None, note=None, player_id=None, child_name=None,
        )
        out = []
        for index, seat in enumerate(entry.seats):
            view = _apply_fields(replace(blank), entry, stations)
            view.ref = -(entry.id * 1000 + index + 1)
            view.player_id = seat["to"]
            view.drawn = seat["drawn"]
            out.append(view)
        return out
    out = []
    for seat in entry.seats:
        slot = live.get(seat["slot_id"])
        if slot is None:
            # A deleted seat can't be shown; Granska flags it as missing.
            continue
        view = _apply_fields(_view(slot), entry, stations)
        view.entry_id = entry.id
        view.player_id = seat["to"]
        view.drawn = seat["drawn"]
        out.append(view)
    return out


def effective_seats(session: Session, team_id: int, *, skip_conflicted: bool = False) -> list[SeatView]:
    """The team's seats as they would be once every staged change is published."""
    live = {s.id: s for s in session.query(Slot).filter(Slot.team_id == team_id)}
    player_ids = {p.id for p in session.query(Player.id).filter(Player.team_id == team_id)}
    stations = {s.id: s for s in session.query(Station)}

    staged: list[SeatView] = []
    replaced: set[int] = set()
    for entry in team_entries(session, team_id):
        if skip_conflicted and conflict_of(entry, live, player_ids):
            continue
        replaced.update(s["slot_id"] for s in entry.seats if s["slot_id"] is not None)
        if entry.kind != PENDING_REMOVE:
            staged.extend(_staged_seats(entry, live, stations))
    untouched = [_view(slot) for slot_id, slot in live.items() if slot_id not in replaced]
    return untouched + staged


def entry_for_seats(entries, slot_ids) -> PendingPass | None:
    wanted = set(slot_ids)
    for entry in entries:
        if wanted & {seat["slot_id"] for seat in entry.seats}:
            return entry
    return None


def new_pass(team_id, activity, day, start, end, station_id, seats: int) -> PendingPass:
    """An unsaved "new" entry with empty seats; the caller adds it."""
    return PendingPass(
        team_id=team_id, kind=PENDING_NEW, duty_name=activity,
        fields={
            "date": {"from": None, "to": day.isoformat() if day else None},
            "start_time": {"from": None, "to": start.strftime("%H:%M")},
            "end_time": {"from": None, "to": end.strftime("%H:%M")},
            "station_id": {"from": None, "to": station_id},
            "note": {"from": None, "to": None},
        },
        seats=[{"slot_id": None, "from": None, "to": None, "drawn": False} for _ in range(seats)],
    )


def edit_entry(team_id, slots: list[Slot]) -> PendingPass:
    """An empty "edit" holding every seat of the Pass, so a field change
    reaches all of them and a later swap on one seat is still seen."""
    return PendingPass(
        team_id=team_id, kind=PENDING_EDIT, fields={},
        seats=[
            {"slot_id": s.id, "from": s.player_id, "to": s.player_id, "drawn": False}
            for s in slots
        ],
    )


def events_of(seats) -> dict[str, list[SeatView]]:
    """Seats grouped into events: a run of consecutive dates with one activity.

    Keyed "{activity}|{first ISO date}", or "{activity}|" for undated seats.
    A gap of more than a day starts a new event.
    """
    events: dict[str, list[SeatView]] = {}
    by_duty: dict[str, list[SeatView]] = {}
    for seat in seats:
        by_duty.setdefault(seat.duty_name, []).append(seat)
    for duty, group in by_duty.items():
        undated = [s for s in group if s.date is None]
        if undated:
            events[f"{duty}|"] = undated
        key, last = None, None
        for seat in sorted((s for s in group if s.date is not None), key=lambda s: s.date):
            if last is None or seat.date - last > timedelta(days=1):
                key = f"{duty}|{seat.date.isoformat()}"
                events[key] = []
            events[key].append(seat)
            last = seat.date
    return events
