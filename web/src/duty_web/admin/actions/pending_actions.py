"""Staging a Pass edit, removal or undo as väntande, without telling anyone.

These write only pending_passes rows, never slots, so the HTML door applies
them at once (immediate) and Granska comes later, when the ansvarig publishes
the whole batch. Through the API they still go dry_run, then plan_token.

Each field and seat is staged as {"from", "to"}: "from" is what the live Pass
said when the value was staged, so a later change to the live Pass shows up as
a conflict instead of being overwritten.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time

from ...agenda import shift_key
from ...models import (
    PENDING_EDIT,
    PENDING_NEW,
    PENDING_REMOVE,
    PendingPass,
    Player,
    Slot,
    Station,
)
from ..pending import (
    FIELD_NAMES,
    conflict_of,
    edit_entry,
    encode_slot,
    entry_for_seats,
    team_entries,
)
from ..slot_ops import day_label, parse_clock, slot_basis, slot_label, slot_order, team_slots
from .base import Action, Plan, ValidationError, parse_input, register

# station_id is an int, so a blank cell ("back to the live station") can't be
# the empty string. Station ids start at 1, which leaves 0 free for it.
BLANK_STATION = 0

_LABELS = {
    "date": "datum", "start_time": "starttid", "end_time": "sluttid",
    "station_id": "plats", "note": "anteckning",
}


@dataclass
class EditInput:
    slot_ids: list[int] = field(default_factory=list)
    entry_id: int | None = None
    day: str | None = None
    start: str | None = None
    end: str | None = None
    note: str | None = None
    station_id: int | None = None
    seats: dict = field(default_factory=dict)


@dataclass
class RemoveInput:
    slot_ids: list[int] = field(default_factory=list)
    entry_id: int | None = None


@dataclass
class EntryInput:
    entry_id: int


@dataclass
class DiscardInput:
    confirm: bool


def _same(name: str, a, b) -> bool:
    # A cleared note is None in a staged value but may be "" on a live row.
    if name == "note":
        return (a or None) == (b or None)
    return a == b


def _entry(session, team_id: int, entry_id: int) -> PendingPass:
    row = session.get(PendingPass, entry_id)
    if row is None or row.team_id != team_id:
        raise ValidationError({"entry_id": "Okänt pass"})
    return row


def _entry_basis(entry: PendingPass | None) -> list:
    if entry is None:
        return [None, None, {}, []]
    return [entry.id, entry.kind, entry.fields, entry.seats]


def _live_seats(session, team_id: int, entry: PendingPass) -> dict[int, Slot]:
    ids = [s["slot_id"] for s in entry.seats if s["slot_id"] is not None]
    if not ids:
        return {}
    rows = session.query(Slot).filter(Slot.team_id == team_id, Slot.id.in_(ids)).all()
    return {s.id: s for s in rows}


def _shift_of(session, team_id: int, ids: list[int]) -> list[Slot]:
    """Every seat of the Pass the chosen seats belong to."""
    if not ids:
        raise ValidationError({"slot_ids": "Välj ett pass"})
    chosen = team_slots(session, team_id, ids)
    keys = {shift_key(s) for s in chosen}
    if len(keys) > 1:
        raise ValidationError({"slot_ids": "Välj platser från ett och samma pass"})
    (key,) = keys
    rows = session.query(Slot).filter(Slot.team_id == team_id).all()
    return sorted((s for s in rows if shift_key(s) == key), key=slot_order)


def _owner(session, player_id: int | None) -> str:
    if player_id is None:
        return "Ledigt"
    player = session.get(Player, player_id)
    return player.name if player else "okänd spelare"


def _label(entry: PendingPass | None, live: dict[int, Slot], group: list[Slot]) -> str:
    first = next(iter(group), None) or next(
        (live[s["slot_id"]] for s in (entry.seats if entry else []) if s["slot_id"] in live), None
    )
    if first is not None:
        return slot_label(first)
    return f"{entry.duty_name or 'Pass'} (nytt pass)" if entry else "Pass"


def _show(session, name: str, value) -> str:
    if value is None or value == "":
        return "ingen"
    if name == "date":
        return day_label(date.fromisoformat(value))
    if name == "station_id":
        station = session.get(Station, value)
        return station.name if station else "okänd plats"
    return str(value)


def _entry_changes(session, before_fields, before_seats, fields, seats) -> list[str]:
    parts = []
    for name in FIELD_NAMES:
        old, new = before_fields.get(name), fields.get(name)
        if old == new:
            continue
        parts.append(
            f"{_LABELS[name]}: {_show(session, name, new['to'])}" if new
            else f"{_LABELS[name]}: ångrad"
        )
    for index, seat in enumerate(seats):
        old = before_seats[index]["to"] if index < len(before_seats) else seat["from"]
        if old != seat["to"]:
            parts.append(f"{_owner(session, old)} → {_owner(session, seat['to'])}")
    return parts


class _Pending(Action):
    scope = "team"
    template = "admin/actions/generic.html"
    done_endpoint = "admin.pass_tab"
    immediate = True


@register
class EditPending(_Pending):
    name = "pending.edit"
    title = "Spara ändring som väntande"
    Input = EditInput

    def parse(self, fields: Mapping, files) -> EditInput:
        inp = parse_input(self.Input, fields)
        # "" is a value here (a cleared note, a field back to live), so what
        # the form sent is read again instead of trusting parse_input's None.
        sent = {}
        for name, attr in (("day", "day"), ("start", "start"), ("end", "end"), ("note", "note")):
            raw = fields.get(name)
            if raw is not None and not isinstance(raw, (dict, list)):
                sent[attr] = str(raw).strip()
        station = fields.get("station_id")
        if isinstance(station, str) and station.strip() == "":
            sent["station_id"] = BLANK_STATION
        seats = {}
        for key, raw in (inp.seats or {}).items():
            seats[str(key)] = "" if raw is None else str(raw).strip()
        if hasattr(fields, "keys"):
            for key in fields.keys():
                if str(key).startswith("seat-"):
                    seats[str(key)[5:]] = str(fields.get(key) or "").strip()
        return dataclasses.replace(inp, seats=seats, **sent)

    def plan(self, session, ctx, inp: EditInput) -> Plan:
        team_id = ctx.team.id
        entries = team_entries(session, team_id)
        entry: PendingPass | None
        if inp.entry_id is not None:
            entry = _entry(session, team_id, inp.entry_id)
            live = _live_seats(session, team_id, entry)
            group = []
        else:
            group = _shift_of(session, team_id, inp.slot_ids)
            entry = entry_for_seats(entries, [s.id for s in group])
            live = {s.id: s for s in group}
        if entry is not None and entry.kind == PENDING_REMOVE:
            raise ValidationError({"slot_ids": "Passet är markerat för borttagning"})

        kind = entry.kind if entry else PENDING_EDIT
        if entry is not None:
            before_fields, before_seats = copy.deepcopy(entry.fields), copy.deepcopy(entry.seats)
        else:
            base = edit_entry(team_id, group)
            before_fields, before_seats = {}, copy.deepcopy(base.seats)
        fields, seats = copy.deepcopy(before_fields), copy.deepcopy(before_seats)

        first = next((live[s["slot_id"]] for s in seats if s["slot_id"] in live), None)
        if kind == PENDING_EDIT and first is None:
            raise ValidationError({"entry_id": "Passet finns inte längre"})
        now = encode_slot(first) if first is not None else None

        if kind == PENDING_EDIT:
            # A seat nobody staged follows the live Pass, so a swap made in the
            # meantime is not shown as the old player.
            for seat in seats:
                slot = live.get(seat["slot_id"])
                if slot is not None and seat["to"] == seat["from"]:
                    seat["from"] = seat["to"] = slot.player_id

        for name, value in self._targets(session, inp, kind, now).items():
            staged = fields.get(name)
            if staged is not None and _same(name, staged["to"], value):
                continue
            if kind == PENDING_NEW:
                fields[name] = {"from": None, "to": value}
                continue
            if staged is None and _same(name, value, now[name]):
                continue
            fields[name] = {"from": now[name], "to": value}

        self._place(session, team_id, inp, kind, seats, live)

        if kind == PENDING_EDIT:
            fields = {n: c for n, c in fields.items() if not _same(n, c["from"], c["to"])}
        self._check(fields, seats, now)

        nothing_left = kind == PENDING_EDIT and not fields and all(
            s["to"] == s["from"] for s in seats
        )
        plan = Plan()
        plan.basis.extend(slot_basis(s) for s in live.values())
        plan.basis.append(_entry_basis(entry))
        label = _label(entry, live, group)
        entry_id = entry.id if entry else None

        if nothing_left:
            if entry is not None:
                plan.add("delete", "pending_pass", label, "väntande ändring borttagen")

                def write(s):
                    row = s.get(PendingPass, entry_id)
                    if row is not None:
                        s.delete(row)
                plan.writes.append(write)
            return plan
        if entry is not None and fields == before_fields and seats == before_seats:
            return plan

        parts = _entry_changes(session, before_fields, before_seats, fields, seats)
        plan.add("update" if entry else "add", "pending_pass", label, "; ".join(parts))

        def write(s):
            row = s.get(PendingPass, entry_id) if entry_id is not None else None
            if row is None:
                row = PendingPass(team_id=team_id, kind=kind)
                s.add(row)
            # JSON columns don't track mutation: always assign new objects.
            row.fields = copy.deepcopy(fields)
            row.seats = copy.deepcopy(seats)
        plan.writes.append(write)
        return plan

    def _targets(self, session, inp: EditInput, kind: str, now) -> dict:
        """The value each posted field should end up with, or nothing for one not sent."""
        out: dict = {}
        blank = kind != PENDING_NEW  # a new Pass has no live value to go back to

        def back_to_live(name):
            return now[name]

        if inp.day is not None:
            if inp.day == "":
                out["date"] = back_to_live("date") if blank else None
            else:
                try:
                    out["date"] = date.fromisoformat(inp.day).isoformat()
                except ValueError:
                    raise ValidationError({"day": "Ange datum som ÅÅÅÅ-MM-DD"}) from None
        for attr, name in (("start", "start_time"), ("end", "end_time")):
            raw = getattr(inp, attr)
            if raw is None or (raw == "" and not blank):
                continue
            if raw == "":
                out[name] = back_to_live(name)
                continue
            value = parse_clock(raw)
            if value is None:
                raise ValidationError({attr: "Ange tid som TT:MM"})
            out[name] = value.strftime("%H:%M")
        if inp.station_id is not None:
            if inp.station_id == BLANK_STATION:
                if blank:
                    out["station_id"] = back_to_live("station_id")
            elif session.get(Station, inp.station_id) is None:
                raise ValidationError({"station_id": "Okänd station"})
            else:
                out["station_id"] = inp.station_id
        if inp.note is not None:
            out["note"] = " ".join(inp.note.split()) or None
        return out

    def _place(self, session, team_id, inp, kind, seats, live) -> None:
        """Put the posted players on their seats. "" is Ledigt."""
        keys = {
            (f"new-{i}" if kind == PENDING_NEW else str(seat["slot_id"])): seat
            for i, seat in enumerate(seats)
        }
        for key, raw in inp.seats.items():
            seat = keys.get(key)
            if seat is None:
                raise ValidationError({"seats": "Okänd plats"})
            cell = f"seat-{key}"
            if raw == "":
                player_id = None
            else:
                try:
                    player_id = int(raw)
                except ValueError:
                    raise ValidationError({cell: "Ogiltigt värde"}) from None
                player = session.get(Player, player_id)
                if player is None or player.team_id != team_id:
                    raise ValidationError({cell: "Spelaren finns inte i laget"})
            if player_id == seat["to"]:
                continue
            if kind != PENDING_NEW:
                slot = live.get(seat["slot_id"])
                if slot is None:
                    raise ValidationError({cell: "Platsen finns inte längre"})
                seat["from"] = slot.player_id
            seat["to"] = player_id

    def _check(self, fields, seats, now) -> None:
        def effective(name):
            if name in fields:
                return fields[name]["to"]
            return now[name] if now else None

        start, end = effective("start_time"), effective("end_time")
        if start and end and time.fromisoformat(end) <= time.fromisoformat(start):
            raise ValidationError({"end": "Sluttiden måste vara efter starttiden"})
        owners = [s["to"] for s in seats if s["to"] is not None]
        if len(owners) != len(set(owners)):
            raise ValidationError({"seats": "Spelaren står redan på passet"})


@register
class RemovePending(_Pending):
    name = "pending.remove"
    title = "Markera pass för borttagning"
    Input = RemoveInput

    def plan(self, session, ctx, inp: RemoveInput) -> Plan:
        team_id = ctx.team.id
        plan = Plan()
        if inp.entry_id is not None:
            entry = _entry(session, team_id, inp.entry_id)
            live = _live_seats(session, team_id, entry)
            group = sorted(live.values(), key=slot_order)
        else:
            group = _shift_of(session, team_id, inp.slot_ids)
            entry = entry_for_seats(team_entries(session, team_id), [s.id for s in group])
            live = {s.id: s for s in group}
        plan.basis.extend(slot_basis(s) for s in group)
        plan.basis.append(_entry_basis(entry))
        entry_id = entry.id if entry else None
        label = _label(entry, live, group)

        if entry is not None and entry.kind == PENDING_REMOVE:
            return plan
        if entry is not None and entry.kind == PENDING_NEW:
            plan.add("delete", "pending_pass", label, "nytt pass borttaget")

            def drop(s):
                row = s.get(PendingPass, entry_id)
                if row is not None:
                    s.delete(row)
            plan.writes.append(drop)
            return plan
        if not group:
            raise ValidationError({"entry_id": "Passet finns inte längre"})

        seats = [
            {"slot_id": s.id, "from": s.player_id, "to": s.player_id, "drawn": False}
            for s in group
        ]
        plan.add("delete", "pending_pass", label, ", ".join(_owner(session, s.player_id) for s in group))

        def write(s):
            row = s.get(PendingPass, entry_id) if entry_id is not None else None
            if row is None:
                row = PendingPass(team_id=team_id)
                s.add(row)
            row.kind, row.fields, row.seats = PENDING_REMOVE, {}, copy.deepcopy(seats)
        plan.writes.append(write)
        return plan


@register
class UndoPending(_Pending):
    name = "pending.undo"
    title = "Ångra väntande ändring"
    Input = EntryInput

    def plan(self, session, ctx, inp: EntryInput) -> Plan:
        entry = _entry(session, ctx.team.id, inp.entry_id)
        live = _live_seats(session, ctx.team.id, entry)
        plan = Plan()
        plan.basis.extend(slot_basis(s) for s in live.values())
        plan.basis.append(_entry_basis(entry))
        plan.add("delete", "pending_pass", _label(entry, live, []), "väntande ändring ångrad")
        entry_id = entry.id

        def write(s):
            row = s.get(PendingPass, entry_id)
            if row is not None:
                s.delete(row)
        plan.writes.append(write)
        return plan


@register
class KeepPending(_Pending):
    name = "pending.keep"
    title = "Behåll min ändring"
    Input = EntryInput

    def plan(self, session, ctx, inp: EntryInput) -> Plan:
        team_id = ctx.team.id
        entry = _entry(session, team_id, inp.entry_id)
        live = {s.id: s for s in session.query(Slot).filter(Slot.team_id == team_id)}
        player_ids = {p.id for p in session.query(Player.id).filter(Player.team_id == team_id)}
        conflict = conflict_of(entry, live, player_ids)
        if conflict is not None and conflict.missing:
            raise ValidationError({"entry_id": "Passet finns inte längre"})

        mine = _live_seats(session, team_id, entry)
        plan = Plan()
        plan.basis.extend(slot_basis(s) for s in mine.values())
        plan.basis.append(_entry_basis(entry))
        if conflict is None:
            return plan

        fields, seats = copy.deepcopy(entry.fields), copy.deepcopy(entry.seats)
        first = next(live[s["slot_id"]] for s in seats if s["slot_id"] in live)
        now = encode_slot(first)
        for name in conflict.fields:
            fields[name]["from"] = now[name]
        for seat in seats:
            if seat["slot_id"] in conflict.seat_ids:
                seat["from"] = live[seat["slot_id"]].player_id
                if entry.kind == PENDING_REMOVE:
                    seat["to"] = seat["from"]
        fields = {n: c for n, c in fields.items() if not _same(n, c["from"], c["to"])}
        gone = entry.kind == PENDING_EDIT and not fields and all(s["to"] == s["from"] for s in seats)
        plan.add("update", "pending_pass", _label(entry, live, []), "ändringen behålls mot det nya passet")
        entry_id = entry.id

        def write(s):
            row = s.get(PendingPass, entry_id)
            if row is None:
                return
            if gone:
                s.delete(row)
                return
            row.fields, row.seats = copy.deepcopy(fields), copy.deepcopy(seats)
        plan.writes.append(write)
        return plan


@register
class DiscardPending(_Pending):
    name = "pending.discard"
    title = "Släng alla väntande ändringar"
    Input = DiscardInput

    def plan(self, session, ctx, inp: DiscardInput) -> Plan:
        if not inp.confirm:
            raise ValidationError({"confirm": "Bekräfta att allt väntande ska slängas"})
        team_id = ctx.team.id
        entries = team_entries(session, team_id)
        if not entries:
            raise ValidationError({"batch": "Inget väntande att slänga"})
        plan = Plan()
        for entry in entries:
            live = _live_seats(session, team_id, entry)
            plan.basis.append(_entry_basis(entry))
            plan.add("delete", "pending_pass", _label(entry, live, []), "väntande ändring slängd")

        def write(s):
            s.query(PendingPass).filter(PendingPass.team_id == team_id).delete()
        plan.writes.append(write)
        return plan


def staged_row(session, team_id: int, inp) -> dict:
    """The row an action touched, as the autosave script paints it."""
    entries = team_entries(session, team_id)
    entry_id = getattr(inp, "entry_id", None)
    if entry_id is not None:
        entry = next((e for e in entries if e.id == entry_id), None)
    else:
        entry = entry_for_seats(entries, getattr(inp, "slot_ids", []))
    if entry is None:
        return {"pending": False, "changed": []}
    changed: list[str] = []
    if entry.kind != PENDING_REMOVE:
        changed = [
            name for name in FIELD_NAMES
            if name in entry.fields and (entry.kind != PENDING_NEW or entry.fields[name]["to"] is not None)
        ]
        for index, seat in enumerate(entry.seats):
            key = f"new-{index}" if entry.kind == PENDING_NEW else seat["slot_id"]
            staged = seat["to"] is not None if entry.kind == PENDING_NEW else seat["to"] != seat["from"]
            if staged:
                changed.append(f"seat-{key}")
    return {"pending": True, "changed": changed}
