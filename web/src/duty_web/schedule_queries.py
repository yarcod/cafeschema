"""Read queries shared by the schedule routes and /api/schedule.

Slots belong to a player, so every parent-scoped query here resolves the
parent's players first and filters on those — either parent of a child sees
and acts on exactly the same rows.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import extract
from sqlalchemy.orm import Session

from .models import Slot, SwapRequest
from .roster import player_ids_for_parent


def slots_for_parent(
    session: Session, person_id: int, *, on_or_after: date | None = None
) -> list[Slot]:
    player_ids = player_ids_for_parent(session, person_id)
    if not player_ids:
        return []
    query = session.query(Slot).filter(Slot.player_id.in_(player_ids))
    if on_or_after is not None:
        query = query.filter(Slot.date >= on_or_after)
    return query.order_by(Slot.date).all()


def undated_slots_for_parent(session: Session, person_id: int) -> list[Slot]:
    """A parent's duties that exist but have no date fixed yet."""
    player_ids = player_ids_for_parent(session, person_id)
    if not player_ids:
        return []
    return (
        session.query(Slot)
        .filter(Slot.player_id.in_(player_ids), Slot.date.is_(None))
        .order_by(Slot.duty_name)
        .all()
    )


def undated_slots_for_team(session: Session, team_id: int) -> list[Slot]:
    """A team's duties that exist but have no date fixed yet."""
    return (
        session.query(Slot)
        .filter(Slot.team_id == team_id, Slot.date.is_(None))
        .order_by(Slot.duty_name)
        .all()
    )


def slots_for_team_month(
    session: Session, team_id: int, year: int, month: int
) -> list[Slot]:
    return (
        session.query(Slot)
        .filter(
            Slot.team_id == team_id,
            extract("year", Slot.date) == year,
            extract("month", Slot.date) == month,
        )
        .order_by(Slot.date)
        .all()
    )


def swappable_slots_for(
    session: Session, team_id: int, person_id: int, *, on_or_after: date
) -> list[Slot]:
    """Other families' filled, upcoming slots a parent could propose to swap for."""
    query = session.query(Slot).filter(
        Slot.team_id == team_id,
        Slot.date >= on_or_after,
        Slot.player_id.isnot(None),
    )
    player_ids = player_ids_for_parent(session, person_id)
    if player_ids:
        query = query.filter(Slot.player_id.notin_(player_ids))
    return query.order_by(Slot.date).all()


def _same_shift(a: Slot, b: Slot) -> bool:
    """Two slot rows staffing the same duty at the same time and place."""
    return (
        a.date == b.date
        and a.start_time == b.start_time
        and a.end_time == b.end_time
        and a.duty_name == b.duty_name
    )


def shift_mates_for(session: Session, slot: Slot) -> list[Slot]:
    """The other people rostered on the same shift as this slot."""
    return [
        other
        for other in session.query(Slot)
        .filter(
            Slot.team_id == slot.team_id,
            Slot.id != slot.id,
            Slot.duty_name == slot.duty_name,
            Slot.start_time == slot.start_time,
        )
        .order_by(Slot.id)
        .all()
        if _same_shift(slot, other)
    ]


def swap_candidates_for_slot(slot: Slot, candidates: list[Slot]) -> list[Slot]:
    """Candidates minus any family already rostered on this slot's own shift.

    Swapping with someone standing beside you on the same shift would just
    put their family on it twice, so they are never a valid trade — and the
    family is the unit, since either of a player's parents may show up.
    """
    on_this_shift = {
        other.player_id for other in candidates if _same_shift(slot, other)
    }
    return [
        candidate
        for candidate in candidates
        if candidate.player_id not in on_this_shift
    ]


def _swaps_on_parents_slots(
    session: Session, person_id: int, slot_side
) -> list[SwapRequest]:
    player_ids = player_ids_for_parent(session, person_id)
    if not player_ids:
        return []
    return (
        session.query(SwapRequest)
        .join(Slot, slot_side == Slot.id)
        .filter(SwapRequest.status == "pending", Slot.player_id.in_(player_ids))
        .order_by(SwapRequest.created_at)
        .all()
    )


def outgoing_swaps_for_parent(session: Session, person_id: int) -> list[SwapRequest]:
    """Pending swaps proposed from a slot this parent's family currently owns."""
    return _swaps_on_parents_slots(session, person_id, SwapRequest.proposer_slot_id)


def swaps_pending_for_parent(session: Session, person_id: int) -> list[SwapRequest]:
    """Pending swaps targeting a slot this parent's family currently owns."""
    return _swaps_on_parents_slots(session, person_id, SwapRequest.target_slot_id)
