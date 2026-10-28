"""What every slot-writing admin action shares: clock times, the names
Granska gives shifts, team scoping, and the swaps a change touches."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, time

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..dates import short_date_sv, weekday_sv
from ..models import Slot, SwapRequest
from .errors import ValidationError

_CLOCK = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?")


def parse_clock(raw: object) -> time | None:
    """'9', '9:00' or '09.00' → 09:00. Anything else → None."""
    match = _CLOCK.fullmatch(str(raw or "").strip())
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def day_label(day: date | None) -> str:
    """'lör 13 mar', or 'datum ej satt' for a shift without a date yet."""
    if day is None:
        return "datum ej satt"
    return f"{weekday_sv(day)[:3]} {short_date_sv(day)}"


def shift_label(day: date | None, start: time, end: time, duty_name: str, station: str) -> str:
    """'lör 13 mar 09:00–12:00 Bästkustcupen · Café': a shift as Granska names it."""
    place = f" · {station}" if station else ""
    return f"{day_label(day)} {start:%H:%M}–{end:%H:%M} {duty_name}{place}"


def slot_label(slot: Slot) -> str:
    return shift_label(slot.date, slot.start_time, slot.end_time, slot.duty_name, slot.station)


def owner_name(slot: Slot) -> str:
    """Who has the seat: the player, or Ledigt (with the schedule's spelling)."""
    if slot.player is not None:
        return slot.player.name
    return f"Ledigt ('{slot.child_name}')" if slot.child_name else "Ledigt"


def slot_order(slot: Slot) -> tuple:
    """Date order, undated last, then time, then id: how every list is sorted."""
    return (slot.date is None, slot.date or date.min, slot.start_time, slot.id)


def slot_basis(slot: Slot) -> list:
    """Everything about a seat a write could overwrite, for Plan.basis.

    Granska's text names a shift, not its every field ("anteckning ändrad"),
    so this is what makes an edit made between review and Genomför a 409.
    """
    return [
        slot.id, slot.player_id, slot.child_name, slot.date, slot.start_time, slot.end_time,
        slot.duty_name, slot.venue, slot.station, slot.station_id, slot.note,
    ]


def team_slots(
    session: Session, team_id: int, ids: Iterable[int], field_name: str = "slot_ids"
) -> list[Slot]:
    """The team's slots with these ids. Another team's id is just unknown."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        raise ValidationError({field_name: "Välj minst ett pass"})
    slots = (
        session.query(Slot)
        .filter(Slot.team_id == team_id, Slot.id.in_(wanted))
        .order_by(Slot.id)
        .all()
    )
    missing = sorted(set(wanted) - {slot.id for slot in slots})
    if missing:
        raise ValidationError({field_name: "Okända pass: " + ", ".join(map(str, missing))})
    return slots


def pending_swaps(session: Session, slot_ids: Iterable[int]) -> list[SwapRequest]:
    ids = sorted(set(slot_ids))
    if not ids:
        return []
    return (
        session.query(SwapRequest)
        .filter(
            SwapRequest.status == "pending",
            or_(SwapRequest.proposer_slot_id.in_(ids), SwapRequest.target_slot_id.in_(ids)),
        )
        .order_by(SwapRequest.id)
        .all()
    )


def pending_slot_ids(session: Session, slot_ids: Iterable[int]) -> set[int]:
    """Which of these slots have a swap waiting for an answer."""
    ids = set(slot_ids)
    found = set()
    for swap in pending_swaps(session, ids):
        found.update({swap.proposer_slot_id, swap.target_slot_id} & ids)
    return found
