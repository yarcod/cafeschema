"""Ändra and Ta bort: one shift's seats at a time (spec v1 actions).

A shift is several seat rows, so both take slot_ids. Only what differs
becomes a change, so the prefilled form sent back unchanged plans nothing.
A new player, date, time or station cancels the seats' pending swaps, since
they describe a shift that no longer exists as offered. Removal deletes the
seats' swap rows. Granska says so first, and both families get a mail after
commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from flask import request

from ...agenda import shift_key
from ...models import Player, Slot, Station, Venue
from ..slot_ops import (
    day_label,
    owner_fields,
    owner_name,
    parse_clock,
    plan_swap_cancellations,
    slot_basis,
    slot_label,
    slot_order,
    team_slots,
)
from .base import Action, Plan, ValidationError, register


@dataclass
class UpdateSlotsInput:
    slot_ids: list[int]
    day: str | None = None
    start: str | None = None
    end: str | None = None
    station_id: int | None = None
    player_id: int | None = None
    clear_player: bool = False
    note: str | None = None


@dataclass
class DeleteSlotsInput:
    slot_ids: list[int]


def _selected(session, ctx) -> list[Slot]:
    ids = [int(v) for v in request.values.getlist("slot_ids") if str(v).isdigit()]
    if not ids:
        return []
    slots = session.query(Slot).filter(Slot.team_id == ctx.team.id, Slot.id.in_(ids)).all()
    return sorted(slots, key=slot_order)


def _seats(slots: list[Slot]) -> list[dict]:
    return [{"id": s.id, "label": slot_label(s), "owner": owner_name(s)} for s in slots]


def _day(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise ValidationError({"day": "Ange datum som ÅÅÅÅ-MM-DD"}) from None


def _clock(raw: str | None, field_name: str):
    if not raw:
        return None
    value = parse_clock(raw)
    if value is None:
        raise ValidationError({field_name: "Ange tid som TT:MM"})
    return value


def _preview(plan: Plan, *days: date | None) -> None:
    for day in days:
        if day is None:
            plan.preview_undated = True
        else:
            plan.preview_dates.add(day)


@register
class UpdateSlots(Action):
    name = "slot.update"
    title = "Ändra pass"
    scope = "team"
    Input = UpdateSlotsInput
    template = "admin/actions/slot_update.html"
    done_endpoint = "admin.pass_tab"

    def form_context(self, session, ctx):
        slots = _selected(session, ctx)
        stations = session.query(Station).join(Venue).order_by(Venue.name, Station.name).all()
        players = (
            session.query(Player).filter(Player.team_id == ctx.team.id).order_by(Player.name).all()
        )
        first = slots[0] if slots else None
        current = {
            "day": first.date.isoformat() if first.date else "",
            "start": first.start_time.strftime("%H:%M"),
            "end": first.end_time.strftime("%H:%M"),
            "station_id": str(first.station_id or ""),
            "player_id": str(first.player_id or ""),
            "note": first.note or "",
        } if first else {}
        return {
            "seats": _seats(slots), "current": current, "single": len(slots) == 1,
            "stations": [(s.id, f"{s.name} ({s.venue.name})") for s in stations],
            "players": [(p.id, p.name) for p in players],
        }

    def plan(self, session, ctx, inp):
        slots = sorted(team_slots(session, ctx.team.id, inp.slot_ids), key=slot_order)
        if len({shift_key(slot) for slot in slots}) > 1:
            raise ValidationError({"slot_ids": "Välj platser från ett och samma pass"})
        day = _day(inp.day)
        start, end = _clock(inp.start, "start"), _clock(inp.end, "end")
        station = None
        if inp.station_id is not None:
            station = session.get(Station, inp.station_id)
            if station is None:
                raise ValidationError({"station_id": "Okänd station"})
        player = None
        if inp.player_id is not None or inp.clear_player:
            if len(slots) != 1:
                raise ValidationError({"player_id": "Byt spelare på en plats i taget"})
            if inp.player_id is not None:
                player = session.get(Player, inp.player_id)
                if player is None or player.team_id != ctx.team.id:
                    raise ValidationError({"player_id": "Spelaren finns inte i laget"})
        note = " ".join(inp.note.split()) if inp.note is not None else None

        plan = Plan()
        plan.basis.extend(slot_basis(slot) for slot in slots)
        changed: list[int] = []
        edits: list[tuple[int, dict]] = []
        for slot in slots:
            new_start, new_end = start or slot.start_time, end or slot.end_time
            if new_end <= new_start:
                raise ValidationError({"end": "Sluttiden måste vara efter starttiden"})
            fields: dict = {}
            diffs: list[str] = []
            if day is not None and day != slot.date:
                fields["date"] = day
                diffs.append(
                    f"datum satt: {day_label(day)}" if slot.date is None
                    else f"datum {day_label(slot.date)} → {day_label(day)}"
                )
            if (new_start, new_end) != (slot.start_time, slot.end_time):
                fields.update(start_time=new_start, end_time=new_end)
                diffs.append(
                    f"tid {slot.start_time:%H:%M}–{slot.end_time:%H:%M} → {new_start:%H:%M}–{new_end:%H:%M}"
                )
            if station is not None and station.id != slot.station_id:
                fields.update(station_id=station.id, station=station.name, venue=station.venue.name)
                diffs.append(f"station {slot.station or 'ingen'} → {station.name}")
            if player is not None and player.id != slot.player_id:
                fields.update(owner_fields(slot, player))
                diffs.append(f"{owner_name(slot)} → {player.name}")
            elif inp.clear_player and slot.player_id is not None:
                fields.update(owner_fields(slot, None))
                diffs.append(f"{owner_name(slot)} → Ledigt")
            moves = bool(fields)
            if note and note != (slot.note or ""):
                fields["note"] = note
                before = f"'{slot.note}'" if slot.note else "ingen"
                diffs.append(f"anteckning: {before} → '{note}'")
            if not fields:
                continue
            plan.add("update", "seat", slot_label(slot), "; ".join(diffs))
            if moves:
                changed.append(slot.id)
            edits.append((slot.id, fields))
            _preview(plan, slot.date, fields.get("date", slot.date))

        if player is not None and edits and player.exempt:
            plan.warnings.append(f"{player.name} är undantagen (ledarfamilj).")
        # Whoever ends up on each edited seat, on the day it ends up on: moving
        # a shift can double-book its players as surely as a new player can.
        selected = [slot.id for slot in slots]
        edited = dict(edits)
        for slot in slots:
            fields = edited.get(slot.id, {})
            if "player_id" not in fields and "date" not in fields:
                continue
            owner, when = fields.get("player_id", slot.player_id), fields.get("date", slot.date)
            if owner is None or when is None:
                continue
            busy = session.query(Slot).filter(
                Slot.team_id == ctx.team.id, Slot.player_id == owner, Slot.date == when,
                Slot.id.notin_(selected),
            ).order_by(Slot.id).first()
            if busy is not None:
                plan.warnings.append(f"{session.get(Player, owner).name} har redan ett pass {day_label(when)}.")

        plan_swap_cancellations(session, plan, changed=changed)

        def write(s):
            for slot_id, fields in edits:
                slot = s.get(Slot, slot_id)
                for name, value in fields.items():
                    setattr(slot, name, value)

        if edits:
            plan.writes.append(write)
        return plan


@register
class DeleteSlots(Action):
    name = "slot.delete"
    title = "Ta bort pass"
    scope = "team"
    Input = DeleteSlotsInput
    template = "admin/actions/slot_delete.html"
    done_endpoint = "admin.pass_tab"

    def form_context(self, session, ctx):
        return {"seats": _seats(_selected(session, ctx))}

    def plan(self, session, ctx, inp):
        slots = sorted(team_slots(session, ctx.team.id, inp.slot_ids), key=slot_order)
        if len({shift_key(slot) for slot in slots}) > 1:
            raise ValidationError({"slot_ids": "Välj platser från ett och samma pass"})
        plan = Plan()
        plan.basis.extend(slot_basis(slot) for slot in slots)
        for slot in slots:
            plan.add("delete", "seat", slot_label(slot), owner_name(slot))
            _preview(plan, slot.date)
        ids = [slot.id for slot in slots]
        plan_swap_cancellations(session, plan, deleted=ids)

        def write(s):
            for slot_id in ids:
                s.delete(s.get(Slot, slot_id))

        plan.writes.append(write)
        return plan
