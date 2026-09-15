"""Mina pass (personal list) and Hela laget (team calendar)."""

from __future__ import annotations

import hmac
from datetime import date

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from ..models import Player, Slot, Team
from ..roster import player_ids_for_parent
from .document_routes import instruction_url_for_duty
from ..schedule_queries import (
    shift_mates_for,
    slots_for_parent,
    slots_for_team_month,
    swap_candidates_for_slot,
    swappable_slots_for,
    undated_slots_for_parent,
    undated_slots_for_team,
)
from ..session_scope import get_session as _session

schedule_bp = Blueprint("schedule", __name__)


def _team_id_for(session, person) -> int:
    """v1 has exactly one team, so every slot's team_id is the same value.

    Looked up from the parent's own player rather than hard-coded, so the
    single-team assumption lives in one place ready to widen later (see the
    spec's multi-team forward-compatibility note). Reading it off the player
    rather than off a slot also works for a family with no duties this term.
    """
    player_ids = player_ids_for_parent(session, person.id)
    if player_ids:
        player = session.get(Player, min(player_ids))
        if player is not None:
            return player.team_id
    return session.query(Team).first().id


@schedule_bp.route("/")
@login_required
def mine():
    session = _session()
    today = date.today()
    slots = slots_for_parent(session, int(current_user.id), on_or_after=today)
    undated_slots = undated_slots_for_parent(session, int(current_user.id))
    team_id = _team_id_for(session, current_user.person)
    candidates = swappable_slots_for(session, team_id, int(current_user.id), on_or_after=today)

    candidates_by_slot = {
        slot.id: swap_candidates_for_slot(slot, candidates) for slot in slots
    }
    mates_by_slot = {
        slot.id: shift_mates_for(session, slot) for slot in slots + undated_slots
    }
    return render_template(
        "mine.html",
        slots=slots,
        undated_slots=undated_slots,
        candidates_by_slot=candidates_by_slot,
        mates_by_slot=mates_by_slot,
    )


def _shifts(slots: list[Slot]) -> list[dict]:
    """Collapse per-person slot rows into one entry per staffed shift.

    The schedule staffs a single shift with several people, stored as one
    Slot each so they can be swapped individually; the agenda reads far
    better as one card per shift listing everyone on it.
    """
    shifts: dict[tuple, dict] = {}
    for slot in sorted(
        slots,
        key=lambda s: (s.date or date.max, s.start_time, s.duty_name),
    ):
        key = (slot.date, slot.start_time, slot.end_time, slot.duty_name, slot.venue)
        shift = shifts.setdefault(
            key,
            {
                "date": slot.date,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
                "duty_name": slot.duty_name,
                "venue": slot.venue,
                "slots": [],
            },
        )
        shift["slots"].append(slot)
    return list(shifts.values())


def _days(shifts: list[dict]) -> list[dict]:
    """Group shifts by date, and tell each one where it sits in its day.

    Being first or last on a day carries real duties (opening up, locking
    after), so position within the day is information the page has to show,
    not decoration.
    """
    days: dict[date, list[dict]] = {}
    for shift in shifts:
        days.setdefault(shift["date"], []).append(shift)

    out = []
    for day, day_shifts in days.items():
        for index, shift in enumerate(day_shifts, start=1):
            shift["position"] = index
            shift["of"] = len(day_shifts)
            shift["is_first"] = index == 1
            shift["is_last"] = index == len(day_shifts)
        out.append({"date": day, "shifts": day_shifts})
    return out


@schedule_bp.route("/team")
@login_required
def team():
    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)
    session = _session()
    team_id = _team_id_for(session, current_user.person)
    slots = slots_for_team_month(session, team_id, year, month)
    undated_slots = undated_slots_for_team(session, team_id)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    # One dot per activity per day, not per person — a shift staffed by
    # three parents is still one thing happening that day.
    duties_by_day: dict[int, list[str]] = {}
    for slot in slots:
        day = duties_by_day.setdefault(slot.date.day, [])
        if slot.duty_name not in day:
            day.append(slot.duty_name)

    duty_names = sorted({slot.duty_name for slot in slots + undated_slots})

    return render_template(
        "team.html",
        days=_days(_shifts(slots)),
        duties_by_day=duties_by_day,
        undated_shifts=_shifts(undated_slots),
        duty_names=duty_names,
        year=year,
        month=month,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
    )


@schedule_bp.route("/api/schedule")
def api_schedule():
    expected_key = current_app.extensions["duty_web_app_config"].api_key
    provided_key = request.headers.get("X-Api-Key")
    # Use constant-time comparison to prevent timing attacks
    if provided_key is None or not hmac.compare_digest(provided_key, expected_key):
        return jsonify({"error": "unauthorized"}), 401

    session = _session()
    slots = (
        session.query(Slot)
        .filter(Slot.player_id.isnot(None), Slot.date.isnot(None))
        .order_by(Slot.date)
        .all()
    )
    # A duty belongs to the player, and either parent may turn up for it, so
    # every parent on file is a reminder recipient — the mailer filters them
    # on email_notifications individually.
    return jsonify([
        {
            "date": slot.date.isoformat(),
            "start_time": slot.start_time.isoformat(timespec="minutes"),
            "end_time": slot.end_time.isoformat(timespec="minutes"),
            "station": slot.station,
            "duty_name": slot.duty_name,
            "venue": slot.venue,
            "note": slot.note,
            # The app owns the documents, so it is the one that knows which
            # instruction a duty needs — the mailer just puts the link in the
            # mail. None when the duty has no instruction on the volume.
            "document_url": instruction_url_for_duty(slot.duty_name, external=True),
            "child_name": slot.player.name,
            "player": {"name": slot.player.name},
            "parents": [
                {
                    "name": parent.name,
                    "email": parent.email,
                    "email_notifications": parent.email_notifications,
                }
                for parent in slot.player.parents
            ],
        }
        for slot in slots
    ])
