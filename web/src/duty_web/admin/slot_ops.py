"""What every slot-writing admin action shares: clock times, the names
Granska gives shifts, team scoping, and the swaps a change touches."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, time
from functools import partial

from flask import url_for
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..clock import utcnow
from ..dates import short_date_sv, weekday_sv
from ..models import ORIGIN_IMPORT, Player, Slot, SwapRequest
from ..notifications import send_shift_moved, send_swap_cancelled
from ..roster import notifiable_emails
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


def plan_shift_notices(session: Session, plan, edits: Iterable[tuple[Slot, dict]]) -> set[int]:
    """Mail the family whose dated shift gets another day, time or station.

    Only a seat that keeps its player: a new owner is the swap mail's
    business, and a first date is like a new shift, which nobody is mailed
    about (the reminder goes out before it). One warning per seat names the
    player, so Granska says beforehand who gets mail; seats with the same
    family and the same text (siblings on one shift) share one mail. Returns the slots
    mailed: their cancelled swap is in the same mail, so call this before
    plan_swap_cancellations and pass the result as `mailed`. With
    plan.notify off the warnings say who would have been mailed, nothing is
    sent, and nothing counts as mailed: a cancelled swap still gets its own
    mail.
    """
    moves = [
        (slot, fields) for slot, fields in edits
        if slot.player_id is not None and slot.date is not None
        and fields.get("player_id", slot.player_id) == slot.player_id
    ]
    if not moves:
        return set()
    pending = pending_slot_ids(session, [slot.id for slot, _ in moves])
    link = url_for("schedule.mine", _external=True)
    mailed: set[int] = set()
    # Siblings on one shift share their parents and their text: one mail,
    # whichever seats it covers. Dicts keep the order the seats came in.
    groups: dict[tuple, dict] = {}
    for slot, fields in moves:
        before = slot_label(slot)
        after = shift_label(
            fields.get("date", slot.date), fields.get("start_time", slot.start_time),
            fields.get("end_time", slot.end_time), slot.duty_name, fields.get("station", slot.station),
        )
        emails = notifiable_emails(session, slot)
        if after == before or not emails:
            continue
        mail = "får ett mejl" if plan.notify is not False else "får inget mejl"
        plan.warnings.append(f"{slot.player.name} {mail} om att passet flyttas: {before} → {after}.")
        group = groups.setdefault(
            (frozenset(emails), before, after),
            {"emails": emails, "before": before, "after": after, "swap_cancelled": False},
        )
        group["swap_cancelled"] = group["swap_cancelled"] or slot.id in pending
        mailed.add(slot.id)
    plan.move_mails += len(groups)
    if plan.notify is False:
        return set()
    for group in groups.values():
        plan.after_commit.append(partial(
            send_shift_moved, group["emails"], before=group["before"], after=group["after"],
            swap_cancelled=group["swap_cancelled"], link=link,
        ))
    return mailed


def plan_swap_cancellations(
    session: Session, plan, *, changed: Iterable[int] = (), deleted: Iterable[int] = (),
    mailed: Iterable[int] = (),
) -> None:
    """The pending swaps on slots this plan changes or removes.

    One warning per swap, naming both families' players, so Granska says
    beforehand who will get mail. At apply a changed slot's pending swaps
    are marked cancelled. A removed slot's swap rows, in any status, are
    deleted with it, or the foreign key refuses the delete. After commit
    each family gets one mail. Call this before adding the action's own slot
    writes: its write must run first. A slot in `mailed` gets no cancel mail:
    plan_shift_notices already told that family.
    """
    changed, deleted, mailed = set(changed), set(deleted), set(mailed)
    touched = sorted(changed | deleted)
    if touched:
        every = (
            session.query(SwapRequest)
            .filter(or_(
                SwapRequest.proposer_slot_id.in_(touched),
                SwapRequest.target_slot_id.in_(touched),
            ))
            .order_by(SwapRequest.id)
            .all()
        )
        plan.basis.extend([swap.id, swap.status] for swap in every)
    swaps = pending_swaps(session, touched)
    link = url_for("schedule.mine", _external=True)
    for swap in swaps:
        proposer, target = swap.proposer_slot, swap.target_slot
        plan.warnings.append(
            f"Bytet mellan {owner_name(proposer)} ({slot_label(proposer)}) och "
            f"{owner_name(target)} ({slot_label(target)}) ställs in. "
            "Båda familjerna får ett mejl."
        )
        for slot in (proposer, target):
            if slot.id in mailed:
                continue
            emails = notifiable_emails(session, slot)
            if emails:
                plan.after_commit.append(
                    partial(send_swap_cancelled, emails, your_slot=slot_label(slot), link=link)
                )
    # A swap that touches a removed slot goes with the delete below; marking
    # it cancelled as well would update a row that's no longer there.
    to_cancel = [
        swap.id for swap in swaps
        if not ({swap.proposer_slot_id, swap.target_slot_id} & deleted)
    ]
    removed = sorted(deleted)
    if not to_cancel and not removed:
        return

    def write(s: Session) -> None:
        if removed:
            s.query(SwapRequest).filter(
                or_(
                    SwapRequest.proposer_slot_id.in_(removed),
                    SwapRequest.target_slot_id.in_(removed),
                )
            ).delete(synchronize_session=False)
        now = utcnow()
        for swap_id in to_cancel:
            swap = s.get(SwapRequest, swap_id)
            swap.status, swap.resolved_at = "cancelled", now

    plan.writes.append(write)


def owner_fields(slot: Slot, player: Player | None) -> dict:
    """The fields that give a seat a new owner, or none.

    On a seat the coach's file put there, child_name is the file's spelling
    and stays, as it does through a swap: schedule.import matches on it, so
    the next import of the same file keeps this owner instead of putting the
    file's name back. Anywhere else the name follows the player.
    """
    if slot.origin == ORIGIN_IMPORT and slot.child_name:
        return {"player_id": player.id if player else None, "child_name": slot.child_name}
    return {"player_id": player.id if player else None, "child_name": player.name if player else None}
