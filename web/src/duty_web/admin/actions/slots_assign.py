"""Bemanna (spec D7): fill the chosen empty seats, fairly.

The team's load is counted here and the choice is made by staffing.assign,
which is pure. Candidates are the team's players who aren't exempt (a
leader's family) and have at least one parent who can be reached. A seat
that's already filled is left alone; a locked one is pinned before anyone is
drawn, and counts towards its player's load.
"""

from __future__ import annotations

import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date

from flask import request

from ...agenda import shift_key
from ...models import Player, Slot
from ...staffing import Candidate, Seat, assign, season_of, spread
from ..slot_ops import day_label, owner_fields, slot_basis, slot_label, slot_order, team_slots
from .base import Action, Plan, ValidationError, parse_input, register


@dataclass
class AssignInput:
    slot_ids: list[int]
    locked: dict = field(default_factory=dict)
    seed: int | None = None
    # The season an all-undated selection counts loads in (its first day).
    season: str | None = None


def _new_seed() -> int:
    return secrets.randbelow(2**31)


def _season(dated: list[date], raw: str | None) -> tuple[date, date]:
    """The one season these seats are staffed in."""
    if dated:
        seasons = {season_of(day) for day in dated}
        if len(seasons) > 1:
            raise ValidationError({"slot_ids": "Välj platser från en säsong i taget (säsongen börjar 1 augusti)"})
        return seasons.pop()
    try:
        return season_of(date.fromisoformat(str(raw)))
    except ValueError:
        raise ValidationError({"season": "Ange säsongens första dag som ÅÅÅÅ-MM-DD"}) from None


def _locked(raw: dict, empty_ids: set[int], players: dict[int, Player]) -> dict[int, int]:
    out = {}
    for key, value in (raw or {}).items():
        try:
            slot_id, player_id = int(key), int(value)
        except (TypeError, ValueError):
            raise ValidationError({"locked": "Ogiltig låsning"}) from None
        if slot_id not in empty_ids:
            raise ValidationError({"locked": f"Plats {slot_id} är inte en av de lediga platserna"})
        if player_id not in players:
            raise ValidationError({"locked": "Den låsta spelaren finns inte i laget"})
        out[slot_id] = player_id
    return out


@register
class AssignSlots(Action):
    name = "slots.assign"
    title = "Bemanna"
    scope = "team"
    Input = AssignInput
    template = "admin/actions/slots_assign.html"
    done_endpoint = "admin.pass_tab"
    refinable = True
    reshuffle = True

    def parse(self, fields, files):
        if hasattr(fields, "getlist"):
            fields = {
                "slot_ids": fields.getlist("slot_ids"),
                "locked": {
                    key[len("lock-"):]: value
                    for key, value in fields.items() if key.startswith("lock-") and value
                },
                "seed": fields.get("seed"),
                "season": fields.get("season"),
            }
        inp = parse_input(AssignInput, fields)
        if inp.seed is None:
            inp = replace(inp, seed=_new_seed())
        if inp.season is None:
            inp = replace(inp, season=season_of(date.today())[0].isoformat())
        return inp

    def refine(self, inp, fields):
        return replace(inp, seed=_new_seed())

    def form_context(self, session, ctx):
        ids = [int(v) for v in request.values.getlist("slot_ids") if str(v).isdigit()]
        seats = (
            session.query(Slot)
            .filter(Slot.team_id == ctx.team.id, Slot.id.in_(ids), Slot.player_id.is_(None))
            .all()
        ) if ids else []
        players = (
            session.query(Player)
            .filter(Player.team_id == ctx.team.id, Player.exempt.is_(False))
            .order_by(Player.name)
            .all()
        )
        return {
            "seats": [{"id": s.id, "label": slot_label(s)} for s in sorted(seats, key=slot_order)],
            "players": [(p.id, p.name) for p in players if p.parents],
            "seed": _new_seed(),
        }

    def plan(self, session, ctx, inp):
        team_id = ctx.team.id
        slots = team_slots(session, team_id, inp.slot_ids)
        players = {
            p.id: p for p in
            session.query(Player).filter(Player.team_id == team_id).order_by(Player.id).all()
        }
        empty = sorted((s for s in slots if s.player_id is None), key=slot_order)
        locked = _locked(inp.locked, {s.id for s in empty}, players)

        plan = Plan()
        plan.basis.extend(slot_basis(slot) for slot in sorted(slots, key=slot_order))
        already = len(slots) - len(empty)
        if already:
            plan.notes.append(
                f"{already} av de valda platserna är redan bemannade och lämnas orörda."
            )
        if not empty:
            return plan

        dated = [s.date for s in empty if s.date is not None]
        first, last = _season(dated, inp.season)
        load: Counter = Counter()
        held: defaultdict = defaultdict(set)
        occupied: defaultdict = defaultdict(set)
        for slot in session.query(Slot).filter(Slot.team_id == team_id, Slot.player_id.isnot(None)):
            if slot.date is None or first <= slot.date <= last:
                load[slot.player_id] += 1
            if slot.date is not None:
                held[slot.player_id].add(slot.date)
            occupied[shift_key(slot)].add(slot.player_id)
        before = dict(load)
        for slot in empty:
            if slot.id in locked:
                # A lock keeps the rules the draw keeps: one shift a day, and
                # never twice on the same shift.
                player_id = locked[slot.id]
                name = players[player_id].name
                if slot.date is not None and slot.date in held[player_id]:
                    raise ValidationError({"locked": f"{name} har redan ett pass {day_label(slot.date)}"})
                if player_id in occupied[shift_key(slot)]:
                    raise ValidationError({"locked": f"{name} står redan på {slot_label(slot)}"})
                load[player_id] += 1
                if slot.date is not None:
                    held[player_id].add(slot.date)
                occupied[shift_key(slot)].add(player_id)

        families = {p.id: frozenset(parent.id for parent in p.parents) for p in players.values()}
        pool = [
            Candidate(p.id, families[p.id], load[p.id], set(held[p.id]))
            for p in players.values() if not p.exempt and p.parents
        ]
        free = [
            Seat(s.id, s.date, s.start_time, s.end_time, s.station_id, key=shift_key(s))
            for s in empty if s.id not in locked
        ]
        results = {
            r.slot_id: r
            for r in assign(free, pool, seed=inp.seed, occupied=occupied, families=families)
        }

        after = Counter(load)
        assignments = []
        for slot in empty:
            label = slot_label(slot)
            if slot.id in locked:
                player = players[locked[slot.id]]
                plan.add("update", "seat", f"{player.name} → {label}", "låst")
                if player.exempt:
                    plan.warnings.append(
                        f"{player.name} är undantagen (ledarfamilj) men låstes till {label}."
                    )
                assignments.append((slot.id, owner_fields(slot, player)))
                continue
            result = results[slot.id]
            if result.player_id is None:
                plan.warnings.append(f"{label}: Ledigt, {result.reason}.")
                continue
            player = players[result.player_id]
            after[player.id] += 1
            plan.add("update", "seat", f"{player.name} → {label}")
            assignments.append((slot.id, owner_fields(slot, player)))

        staffed = {fields["player_id"] for _, fields in assignments}
        for player_id in sorted(staffed, key=lambda i: (players[i].name, i)):
            plan.notes.append(
                f"{players[player_id].name}: {before.get(player_id, 0)} → {after[player_id]} pass"
            )
        mean, width = spread(after[c.player_id] for c in pool)
        mean_text = f"{mean:.1f}".replace(".", ",")
        plan.notes.append(f"Laget: i snitt {mean_text} pass per spelare, spridning {width}.")
        plan.preview_dates.update(dated)
        plan.preview_undated = any(s.date is None for s in empty)

        def write(s):
            for slot_id, fields in assignments:
                slot = s.get(Slot, slot_id)
                for name, value in fields.items():
                    setattr(slot, name, value)

        if assignments:
            plan.writes.append(write)
        return plan
