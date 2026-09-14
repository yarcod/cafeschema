"""Swap request state machine: propose -> accept | decline | expire."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import Slot, SwapRequest


class SwapError(Exception):
    """Raised when a swap action is invalid. Message is user-facing."""


def propose_swap(
    session: Session,
    *,
    proposer_slot_id: int,
    target_slot_id: int,
    proposer_person_id: int,
    now: datetime,
) -> SwapRequest:
    proposer_slot = session.get(Slot, proposer_slot_id)
    if proposer_slot is None or proposer_slot.person_id != proposer_person_id:
        raise SwapError("Du kan bara föreslå byte av ditt eget pass.")

    if target_slot_id == proposer_slot_id:
        raise SwapError("Du kan inte föreslå byte med ditt eget pass.")

    target_slot = session.get(Slot, target_slot_id)
    if target_slot is None:
        raise SwapError("Passet du vill byta med hittades inte.")
    if target_slot.person_id is None:
        raise SwapError("Passet du vill byta med är ledigt och har ingen ägare.")
    if target_slot.person_id == proposer_person_id:
        raise SwapError("Du kan inte föreslå byte med ett pass du redan har.")

    request = SwapRequest(
        proposer_slot_id=proposer_slot_id,
        target_slot_id=target_slot_id,
        status="pending",
        created_at=now,
    )
    session.add(request)
    session.commit()
    return request


def _pending_request(session: Session, swap_request_id: int, *, now: datetime) -> SwapRequest:
    request = session.get(SwapRequest, swap_request_id)
    if request is None:
        raise SwapError("Bytesförslaget hittades inte.")
    if request.status == "expired" or (
        request.status == "pending" and now - request.created_at > timedelta(days=7)
    ):
        request.status = "expired"
        request.resolved_at = now
        session.commit()
        raise SwapError("Bytesförslaget har upphört att gälla.")
    if request.status != "pending":
        raise SwapError("Bytesförslaget är redan avgjort.")
    return request


def accept_swap(
    session: Session, swap_request_id: int, *, accepting_person_id: int, now: datetime
) -> SwapRequest:
    request = _pending_request(session, swap_request_id, now=now)
    target_slot = session.get(Slot, request.target_slot_id)
    if target_slot.person_id != accepting_person_id:
        raise SwapError("Det här bytet är inte ditt att acceptera.")

    proposer_slot = session.get(Slot, request.proposer_slot_id)
    proposer_slot.person_id, target_slot.person_id = (
        target_slot.person_id,
        proposer_slot.person_id,
    )
    request.status = "accepted"
    request.resolved_at = now

    # Invalidate every other still-pending request that touches either slot
    # that just changed hands — once one offer on a slot is taken, any
    # sibling offers on that same slot (or on the slot it was traded for)
    # no longer describe a swap either party actually wants.
    superseded = (
        session.query(SwapRequest)
        .filter(
            SwapRequest.id != request.id,
            SwapRequest.status == "pending",
            or_(
                SwapRequest.proposer_slot_id.in_(
                    [request.proposer_slot_id, request.target_slot_id]
                ),
                SwapRequest.target_slot_id.in_(
                    [request.proposer_slot_id, request.target_slot_id]
                ),
            ),
        )
        .all()
    )
    for other in superseded:
        other.status = "declined"
        other.resolved_at = now

    session.commit()
    return request


def decline_swap(
    session: Session, swap_request_id: int, *, declining_person_id: int, now: datetime
) -> SwapRequest:
    request = _pending_request(session, swap_request_id, now=now)
    target_slot = session.get(Slot, request.target_slot_id)
    if target_slot.person_id != declining_person_id:
        raise SwapError("Det här bytet är inte ditt att avböja.")

    request.status = "declined"
    request.resolved_at = now
    session.commit()
    return request


def expire_stale_swaps(session: Session, *, now: datetime, max_age_days: int = 7) -> int:
    cutoff = now - timedelta(days=max_age_days)
    stale = (
        session.query(SwapRequest)
        .filter(SwapRequest.status == "pending", SwapRequest.created_at < cutoff)
        .all()
    )
    for request in stale:
        request.status = "expired"
        request.resolved_at = now
    session.commit()
    return len(stale)
