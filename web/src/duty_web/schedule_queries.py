"""Read queries shared by the schedule routes and /api/schedule."""

from __future__ import annotations

from datetime import date

from sqlalchemy import extract
from sqlalchemy.orm import Session

from .models import Slot, SwapRequest


def slots_for_person(
    session: Session, person_id: int, *, on_or_after: date | None = None
) -> list[Slot]:
    query = session.query(Slot).filter(Slot.person_id == person_id)
    if on_or_after is not None:
        query = query.filter(Slot.date >= on_or_after)
    return query.order_by(Slot.date).all()


def undated_slots_for_person(session: Session, person_id: int) -> list[Slot]:
    """A person's duties that exist but have no date fixed yet."""
    return (
        session.query(Slot)
        .filter(Slot.person_id == person_id, Slot.date.is_(None))
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
    """Other people's filled, upcoming slots a person could propose to swap for."""
    return (
        session.query(Slot)
        .filter(
            Slot.team_id == team_id,
            Slot.date >= on_or_after,
            Slot.person_id.isnot(None),
            Slot.person_id != person_id,
        )
        .order_by(Slot.date)
        .all()
    )


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
    """Candidates minus anyone already rostered on this slot's own shift.

    Swapping with someone standing beside you on the same shift would just
    put them on it twice, so they are never a valid trade.
    """
    on_this_shift = {
        other.person_id for other in candidates if _same_shift(slot, other)
    }
    return [
        candidate
        for candidate in candidates
        if candidate.person_id not in on_this_shift
    ]


def outgoing_swaps_for_person(session: Session, person_id: int) -> list[SwapRequest]:
    """Pending swap requests proposed from a slot the given person currently owns."""
    return (
        session.query(SwapRequest)
        .join(Slot, SwapRequest.proposer_slot_id == Slot.id)
        .filter(SwapRequest.status == "pending", Slot.person_id == person_id)
        .order_by(SwapRequest.created_at)
        .all()
    )


def swaps_pending_for_person(session: Session, person_id: int) -> list[SwapRequest]:
    """Pending swap requests targeting a slot the given person currently owns.

    There is no route or template rendering this yet (deferred to the
    frontend pass), but the query exists so a future "incoming swap
    requests" view isn't blocked on writing it from scratch.
    """
    return (
        session.query(SwapRequest)
        .join(Slot, SwapRequest.target_slot_id == Slot.id)
        .filter(SwapRequest.status == "pending", Slot.person_id == person_id)
        .order_by(SwapRequest.created_at)
        .all()
    )
