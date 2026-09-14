"""Propose/accept/decline swap routes."""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.exceptions import BadRequest

from ..clock import utcnow
from ..dates import short_date_sv
from ..models import Person, Slot, SwapRequest
from ..notifications import send_swap_accepted, send_swap_declined, send_swap_proposed
from ..roster import notifiable_emails, parent_names_of_slot
from ..schedule_queries import outgoing_swaps_for_parent, swaps_pending_for_parent
from ..session_scope import get_session as _session
from ..swaps import SwapError, accept_swap, decline_swap, propose_swap

swap_bp = Blueprint("swaps", __name__)


def _required_int_form_field(field_name: str) -> int:
    """Read an integer form field, or raise a 400 for a missing/non-numeric one.

    request.form's own KeyError already becomes a 400 via Flask's
    BadRequestKeyError, but int() on a non-numeric value raises a bare
    ValueError, which Flask does not translate to a 400 on its own.
    """
    try:
        return int(request.form[field_name])
    except ValueError:
        raise BadRequest(f"{field_name} måste vara ett tal.") from None


def _format_slot(slot: Slot) -> str:
    return f"{short_date_sv(slot.date)} {slot.duty_name} {slot.start_time.strftime('%H:%M')}"


def _player_name(slot: Slot) -> str | None:
    """The player the slot belongs to, falling back to the schedule's spelling."""
    return slot.player.name if slot.player is not None else slot.child_name


@swap_bp.route("/byten")
@login_required
def byten():
    session = _session()
    person_id = int(current_user.id)
    incoming = [
        {
            "request": req,
            "other_name": parent_names_of_slot(
                session, session.get(Slot, req.proposer_slot_id)
            ),
            "other_child": _player_name(session.get(Slot, req.proposer_slot_id)),
            "you_give": _format_slot(session.get(Slot, req.target_slot_id)),
            "you_receive": _format_slot(session.get(Slot, req.proposer_slot_id)),
        }
        for req in swaps_pending_for_parent(session, person_id)
    ]
    outgoing = [
        {
            "request": req,
            "other_name": parent_names_of_slot(
                session, session.get(Slot, req.target_slot_id)
            ),
            "other_child": _player_name(session.get(Slot, req.target_slot_id)),
            "you_give": _format_slot(session.get(Slot, req.proposer_slot_id)),
            "you_receive": _format_slot(session.get(Slot, req.target_slot_id)),
        }
        for req in outgoing_swaps_for_parent(session, person_id)
    ]
    return render_template("byten.html", incoming=incoming, outgoing=outgoing)


@swap_bp.route("/swaps", methods=["POST"])
@login_required
def create():
    session = _session()
    proposer_slot_id = _required_int_form_field("proposer_slot_id")
    target_slot_ids = request.form.getlist("target_slot_ids")
    # Either parent may propose on the family's behalf, so the mail names
    # the one who actually clicked rather than the household.
    proposer = session.get(Person, int(current_user.id))
    proposer_name = proposer.name if proposer is not None else ""

    for raw_target_id in target_slot_ids:
        try:
            target_slot_id = int(raw_target_id)
        except ValueError:
            continue
        try:
            propose_swap(
                session,
                proposer_slot_id=proposer_slot_id,
                target_slot_id=target_slot_id,
                proposer_person_id=int(current_user.id),
                now=utcnow(),
            )
        except SwapError:
            continue

        proposer_slot = session.get(Slot, proposer_slot_id)
        target_slot = session.get(Slot, target_slot_id)
        # The offer goes to the whole household that holds the target slot;
        # whichever parent reads it first can answer for the family.
        recipients = notifiable_emails(session, target_slot)
        if recipients:
            send_swap_proposed(
                recipients,
                proposer_name=proposer_name,
                proposer_slot=_format_slot(proposer_slot),
                target_slot=_format_slot(target_slot),
                link=url_for("swaps.byten", _external=True),
            )

    return redirect(url_for("schedule.mine"))


@swap_bp.route("/swaps/<int:swap_id>/accept", methods=["POST"])
@login_required
def accept(swap_id: int):
    session = _session()

    # accept_swap() swaps player_id on the proposer/target slot rows
    # in place, so the original proposer household's addresses and the
    # accepter's name must be captured *before* calling it — reading them
    # afterwards off the (now-swapped) rows would notify the wrong family.
    swap_request = session.get(SwapRequest, swap_id)
    proposer_emails: list[str] = []
    if swap_request is not None:
        original_proposer_slot = session.get(Slot, swap_request.proposer_slot_id)
        proposer_emails = notifiable_emails(session, original_proposer_slot)
    accepter = session.get(Person, int(current_user.id))
    accepter_name = accepter.name if accepter is not None else ""

    try:
        request_row = accept_swap(
            session, swap_id, accepting_person_id=int(current_user.id), now=utcnow()
        )
    except SwapError as exc:
        return str(exc), 400
    proposer_slot = session.get(Slot, request_row.proposer_slot_id)
    target_slot = session.get(Slot, request_row.target_slot_id)
    if proposer_emails:
        # proposer_slot's schedule fields are unaffected by the player_id
        # swap above, so this row still describes what "you" (the
        # original proposer) gave up; target_slot describes what you
        # now have.
        send_swap_accepted(
            proposer_emails,
            accepter_name=accepter_name,
            your_old_slot=_format_slot(proposer_slot),
            your_new_slot=_format_slot(target_slot),
            link=url_for("schedule.mine", _external=True),
        )
    return redirect(url_for("swaps.byten"))


@swap_bp.route("/swaps/<int:swap_id>/decline", methods=["POST"])
@login_required
def decline(swap_id: int):
    session = _session()
    try:
        request_row = decline_swap(
            session, swap_id, declining_person_id=int(current_user.id), now=utcnow()
        )
    except SwapError as exc:
        return str(exc), 400
    proposer_slot = session.get(Slot, request_row.proposer_slot_id)
    decliner = session.get(Person, int(current_user.id))
    recipients = notifiable_emails(session, proposer_slot)
    if recipients:
        send_swap_declined(
            recipients,
            decliner_name=decliner.name if decliner is not None else "",
            your_slot=_format_slot(proposer_slot),
            link=url_for("schedule.mine", _external=True),
        )
    return redirect(url_for("swaps.byten"))
