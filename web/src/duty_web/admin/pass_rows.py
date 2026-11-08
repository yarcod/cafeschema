"""The Pass page as rows: every Pass once, as it would be once published.

A row is one Pass (all its seats). Live Pass nobody staged follow the live
schedule; a staged Pass shows its staged values; a removed one stays,
struck through, until it is published or undone. Rows are placed under the
date they would have, grouped into events the same way the draw reads them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from ..agenda import group_shifts
from ..dates import short_date_sv
from ..models import PENDING_EDIT, PENDING_NEW, PENDING_REMOVE, Player, Slot, Station
from .pending import (
    FIELD_NAMES,
    SeatView,
    conflict_of,
    events_of,
    seat_view,
    shown_seats,
    team_entries,
)
from .permissions import AdminContext
from .slot_ops import pending_slot_ids


@dataclass
class SeatCell:
    key: str
    player_id: int | None
    changed: bool
    drawn: bool
    swapping: bool
    # The schedule's own spelling, only while the seat is live and unstaged:
    # once someone is staged on it (or it is staged free) it no longer applies.
    hint: str | None


@dataclass
class PassRow:
    entry_id: int | None
    slot_ids: list[int]
    status: str  # "live" | "edit" | "new" | "remove"
    conflict: bool
    date: date | None
    start: str
    end: str
    duty_name: str
    station_id: int | None
    station: str
    note: str | None
    seats: list[SeatCell]
    changed: set[str] = field(default_factory=set)
    open_seats: int = 0

    @property
    def form_id(self) -> str:
        return f"row-e{self.entry_id}" if self.status == "new" else f"row-s{self.slot_ids[0]}"


def _row(
    views: list[SeatView], keys: list[str], *, entry_id, status, conflict, changed: set[str],
    staged: set[str], swapping: set[int],
) -> PassRow:
    first = views[0]
    seats = []
    for view, key in zip(views, keys):
        marked = key in staged
        seats.append(SeatCell(
            key=key, player_id=view.player_id, changed=marked, drawn=view.drawn,
            swapping=view.slot_id in swapping if status != "new" else False,
            hint=view.child_name if (
                view.player_id is None and not marked and view.slot_id is not None and view.child_name
            ) else None,
        ))
    return PassRow(
        entry_id=entry_id, slot_ids=[v.slot_id for v in views if v.slot_id is not None],
        status=status, conflict=conflict, date=first.date,
        start=first.start_time.strftime("%H:%M"), end=first.end_time.strftime("%H:%M"),
        duty_name=first.duty_name, station_id=first.station_id, station=first.station,
        note=first.note, seats=seats, changed=changed,
        open_seats=0 if status == "remove" else sum(1 for s in seats if s.player_id is None),
    )


def _title(duty: str, days: list[date]) -> str:
    if not days:
        return duty
    first, last = days[0], days[-1]
    span = short_date_sv(first) if first == last else f"{short_date_sv(first)}–{short_date_sv(last)}"
    return f"{duty} · {span}"


def pass_view(session: Session, ctx: AdminContext, *, include_past: bool) -> list[dict]:
    team_id = ctx.team.id
    live = {s.id: s for s in session.query(Slot).filter(Slot.team_id == team_id).order_by(Slot.id)}
    player_ids = {p.id for p in session.query(Player.id).filter(Player.team_id == team_id)}
    stations = {s.id: s for s in session.query(Station)}
    swapping = pending_slot_ids(session, live)

    rows: list[PassRow] = []
    claimed: set[int] = set()
    for entry in team_entries(session, team_id):
        claimed.update(s["slot_id"] for s in entry.seats if s["slot_id"] is not None)
        flagged = conflict_of(entry, live, player_ids) is not None
        views = shown_seats(entry, live, stations)
        if entry.kind == PENDING_REMOVE:
            keys = [str(v.slot_id) for v in views]
            status, changed, staged = "remove", set(), set()
        else:
            if entry.kind == PENDING_NEW:
                keys = [f"new-{i}" for i in range(len(views))]
                staged = {f"new-{i}" for i, s in enumerate(entry.seats) if s["to"] is not None}
                changed = {n for n in FIELD_NAMES if n in entry.fields and entry.fields[n]["to"] is not None}
                status = "new"
            else:
                assert entry.kind == PENDING_EDIT
                keys = [str(v.slot_id) for v in views]
                staged = {str(s["slot_id"]) for s in entry.seats if s["to"] != s["from"]}
                changed = {n for n in FIELD_NAMES if n in entry.fields}
                status = "edit"
            changed |= {f"seat-{k}" for k in staged}
        if not views:
            # Every live seat is gone (Excel took it): Granska offers the undo.
            continue
        rows.append(_row(
            views, keys, entry_id=entry.id, status=status, conflict=flagged, changed=changed,
            staged=staged, swapping=swapping,
        ))

    untouched = [seat_view(s) for sid, s in live.items() if sid not in claimed]
    for shift in group_shifts(untouched):
        views = shift["slots"]
        rows.append(_row(
            views, [str(v.slot_id) for v in views], entry_id=None, status="live", conflict=False,
            changed=set(), staged=set(), swapping=swapping,
        ))

    if not include_past:
        today = date.today()
        rows = [r for r in rows if r.date is None or r.date >= today]

    # events_of reads only duty_name and date, which a row has too.
    events = []
    for key, members in events_of(rows).items():
        dated = sorted({r.date for r in members if r.date is not None})
        by_day: dict[date | None, list[PassRow]] = {}
        for row in sorted(members, key=lambda r: (r.start, r.end, r.station)):
            by_day.setdefault(row.date, []).append(row)
        events.append({
            "key": key,
            "title": _title(members[0].duty_name, dated),
            "open_seats": sum(r.open_seats for r in members),
            "days": [
                {"date": day, "rows": by_day[day]}
                for day in sorted(by_day, key=lambda d: (d is None, d or date.min))
            ],
            "_sort": (not dated, dated[0] if dated else date.max, members[0].duty_name),
        })
    events.sort(key=lambda e: e["_sort"])
    for event in events:
        del event["_sort"]
    return events
