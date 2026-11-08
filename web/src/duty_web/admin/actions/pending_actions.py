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
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time

from ...agenda import shift_key
from ...models import (
    ORIGIN_ADMIN,
    PENDING_EDIT,
    PENDING_NEW,
    PENDING_REMOVE,
    PendingPass,
    Player,
    Slot,
    Station,
)
from ...staffing import season_of, spread
from ..pending import (
    FIELD_NAMES,
    Conflict,
    SeatView,
    conflict_of,
    edit_entry,
    effective_seats,
    encode_slot,
    entry_for_seats,
    staged_seats,
    team_entries,
)
from ..slot_ops import (
    day_label,
    owner_fields,
    owner_name,
    parse_clock,
    plan_shift_notices,
    plan_swap_cancellations,
    shift_label,
    slot_basis,
    slot_label,
    slot_order,
    team_slots,
)
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


@dataclass
class PublishInput:
    notify: bool = True


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

_OPS = {PENDING_EDIT: "update", PENDING_NEW: "add", PENDING_REMOVE: "delete"}
_CONFLICT_LABELS = {
    "date": "datum", "start_time": "tid", "end_time": "tid",
    "station_id": "plats", "note": "anteckning",
}


def _conflict_text(conflict: Conflict, entry: PendingPass, live: dict[int, Slot]) -> str:
    parts = list(dict.fromkeys(_CONFLICT_LABELS[name] for name in conflict.fields))
    if conflict.seat_ids:
        parts.append("spelare")
    if conflict.missing:
        gone = any(s["slot_id"] is not None and s["slot_id"] not in live for s in entry.seats)
        parts.append("passet finns inte längre" if gone else "spelaren finns inte längre i laget")
    return "Ändrat sedan det lades in: " + ", ".join(parts)


def _quoted(note: str | None) -> str:
    return f"'{note}'" if note else "ingen"


def _new_detail(session, entry: PendingPass) -> str:
    count = len(entry.seats)
    names = [_owner(session, s["to"]) for s in entry.seats if s["to"] is not None]
    detail = f"{count} {'plats' if count == 1 else 'platser'}"
    return f"{detail}; {', '.join(names)}" if names else detail


def _edit_detail(session, entry: PendingPass, live: dict[int, Slot], after: SeatView) -> str:
    """What changes on the Pass, in the words Granska has always used."""
    before = next((live[s["slot_id"]] for s in entry.seats if s["slot_id"] in live), None)
    if before is None:
        return ""
    parts: list[str] = []
    if "date" in entry.fields:
        parts.append(
            f"datum satt: {day_label(after.date)}" if before.date is None
            else f"datum {day_label(before.date)} → {day_label(after.date)}"
        )
    if "start_time" in entry.fields or "end_time" in entry.fields:
        parts.append(
            f"tid {before.start_time:%H:%M}–{before.end_time:%H:%M}"
            f" → {after.start_time:%H:%M}–{after.end_time:%H:%M}"
        )
    if "station_id" in entry.fields:
        parts.append(f"station {before.station or 'ingen'} → {after.station or 'ingen'}")
    if "note" in entry.fields:
        parts.append(f"anteckning: {_quoted(before.note)} → {_quoted(after.note)}")
    for seat in entry.seats:
        slot = live.get(seat["slot_id"])
        if slot is not None and seat["to"] != seat["from"]:
            parts.append(f"{owner_name(slot)} → {_owner(session, seat['to'])}")
    return "; ".join(parts)


def _seat_changes(entry: PendingPass, seat: dict, slot: Slot, after: SeatView, players) -> dict:
    """The Slot columns one edited seat gets, decoded for the live row."""
    changes: dict = {}
    for name in entry.fields:
        if name == "station_id":
            changes.update(station_id=after.station_id, station=after.station, venue=after.venue)
        else:
            changes[name] = getattr(after, name)
    if seat["to"] != seat["from"]:
        changes.update(owner_fields(slot, players[seat["to"]] if seat["to"] is not None else None))
    return changes


def _double_bookings(seats: list[SeatView], touched: set[int], players) -> list[str]:
    """One warning per player and day where a staged seat meets another Pass."""
    by_day: defaultdict = defaultdict(list)
    for seat in seats:
        # A live seat can outlast its player's place on the team.
        if seat.player_id in players and seat.date is not None:
            by_day[(seat.player_id, seat.date)].append(seat)
    found = []
    for (player_id, when), group in by_day.items():
        if {s.ref for s in group} & touched and len({shift_key(s) for s in group}) > 1:
            found.append((when, players[player_id].name))
    return [f"{name} har redan ett pass {day_label(when)}." for when, name in sorted(found)]


def _overlaps(heads: list[tuple[int, SeatView]], seats: list[SeatView]) -> list[str]:
    """Seats, staged ones included, that a new Pass shares its place and time with."""
    labels = {
        shift_label(seat.date, seat.start_time, seat.end_time, seat.duty_name, seat.station)
        for entry_id, head in heads
        for seat in seats
        if head.date is not None and head.station_id is not None
        and seat.entry_id != entry_id and seat.date == head.date
        and seat.station_id == head.station_id
        and seat.start_time < head.end_time and head.start_time < seat.end_time
    }
    return [f"Laget har redan ett pass som överlappar: {label}" for label in sorted(labels)]


def _staffing_notes(live, seats: list[SeatView], placed: dict[int, list], players) -> list[str]:
    """Each newly placed player's load before and after, in the season placed in."""
    days = [day for placements in placed.values() for day in placements if day is not None]
    first, last = season_of(min(days) if days else date.today())

    def load(rows) -> Counter:
        return Counter(
            row.player_id for row in rows
            if row.player_id is not None and (row.date is None or first <= row.date <= last)
        )

    before, after = load(live.values()), load(seats)
    gaining = [pid for pid in placed if after[pid] > before[pid]]
    if not gaining:
        return []
    pool = [p.id for p in players.values() if not p.exempt and p.parents]
    mean, width = spread(after[pid] for pid in pool)
    mean_text = f"{mean:.1f}".replace(".", ",")
    return [
        *(f"{players[pid].name}: {before[pid]} → {after[pid]} pass"
          for pid in sorted(gaining, key=lambda i: (players[i].name, i))),
        f"Laget: i snitt {mean_text} pass per spelare, spridning {width}.",
    ]


# ─────────────────────────────────────────────────────────────────────────
# AGENTS: READ THIS BEFORE CALLING pending.publish.
#
# Publishing changes what parents see and can send them mail. It is hugely
# preferable that a human reviews the batch in Granska and presses Genomför.
#
# You may publish ONLY when you have been explicitly asked to publish AND
# you have received approval for this specific publish. Staging changes
# with the pending.* actions and leaving them for a person is the default.
# ─────────────────────────────────────────────────────────────────────────
@register
class PublishPending(_Pending):
    name = "pending.publish"
    title = "Genomför väntande ändringar"
    Input = PublishInput
    immediate = False
    review_template = "admin/granska_vantande.html"
    agent_note = (
        "Låt helst en människa granska och genomföra. Agenter får bara genomföra "
        "på uttrycklig begäran och efter godkännande av just den här publiceringen."
    )

    def plan(self, session, ctx, inp: PublishInput) -> Plan:
        team_id = ctx.team.id
        entries = team_entries(session, team_id)
        if not entries:
            raise ValidationError({"batch": "Inget väntande att genomföra"})
        live = {s.id: s for s in session.query(Slot).filter(Slot.team_id == team_id).order_by(Slot.id)}
        players = {
            p.id: p for p in session.query(Player).filter(Player.team_id == team_id).order_by(Player.id)
        }
        stations = {s.id: s for s in session.query(Station)}
        views = {e.id: staged_seats(e, live, stations) for e in entries}
        conflicts = {e.id: conflict_of(e, live, set(players)) for e in entries}

        def order(entry: PendingPass) -> tuple:
            # slot_order on the Pass as it will be, so a move sorts under its new day.
            head = next(iter(views[entry.id]), None)
            if head is None:
                return (True, date.min, time.min, entry.id)
            return (head.date is None, head.date or date.min, head.start_time, entry.id)

        entries.sort(key=order)

        plan = Plan(notify=inp.notify)
        edits: list[tuple[int, dict]] = []
        changed: list[int] = []
        removed: list[int] = []
        additions: list[dict] = []
        published: list[int] = []
        # Staged owners and dates, by SeatView.ref: what a double booking can be new about.
        touched: set[int] = set()
        new_heads: list[tuple[int, SeatView]] = []
        # Who gets a seat, and on which days.
        placed: dict[int, list] = {}
        held_back = 0

        def preview(*days) -> None:
            for day in days:
                if day is None:
                    plan.preview_undated = True
                else:
                    plan.preview_dates.add(day)

        for entry in entries:
            plan.basis.append(_entry_basis(entry))
            plan.basis.extend(slot_basis(live[s["slot_id"]]) for s in entry.seats if s["slot_id"] in live)
            seats = views[entry.id]
            head = next(iter(seats), None)
            conflict = conflicts[entry.id]
            op = _OPS[entry.kind]
            review = {
                "entry_id": entry.id,
                "day": day_label(head.date) if head is not None and head.date is not None else None,
                "op": op,
                "summary": (
                    shift_label(head.date, head.start_time, head.end_time, head.duty_name, head.station)
                    if head is not None else "Pass som inte längre finns"
                ),
                "detail": "",
                "conflict": None,
                "missing": False,
                "drawn": any(s["drawn"] for s in entry.seats),
            }
            plan.review.append(review)

            if head is not None:
                if entry.kind == PENDING_NEW:
                    review["detail"] = _new_detail(session, entry)
                elif entry.kind == PENDING_EDIT:
                    review["detail"] = _edit_detail(session, entry, live, head)
                else:
                    review["detail"] = ", ".join(owner_name(live[s.slot_id]) for s in seats)

            if conflict is not None or head is None:
                held_back += 1
                review["missing"] = conflict is None or conflict.missing
                review["conflict"] = (
                    _conflict_text(conflict, entry, live) if conflict is not None
                    else "Ändrat sedan det lades in: passet finns inte längre"
                )
                continue

            plan.add(op, "shift", review["summary"], review["detail"])
            published.append(entry.id)

            if entry.kind == PENDING_NEW:
                new_heads.append((entry.id, head))
                preview(head.date)
                for seat, view in zip(entry.seats, seats):
                    player = players[seat["to"]] if seat["to"] is not None else None
                    if player is not None:
                        touched.add(view.ref)
                        placed.setdefault(player.id, []).append(head.date)
                    additions.append({
                        "team_id": team_id, "date": head.date, "start_time": head.start_time,
                        "end_time": head.end_time, "station": head.station, "venue": head.venue,
                        "duty_name": head.duty_name, "station_id": head.station_id, "note": head.note,
                        "player_id": player.id if player else None,
                        "child_name": player.name if player else None,
                        "origin": ORIGIN_ADMIN,
                    })
                continue

            if entry.kind == PENDING_REMOVE:
                for view in seats:
                    removed.append(view.slot_id)
                    preview(view.date)
                continue

            by_slot = {view.slot_id: view for view in seats}
            for seat in entry.seats:
                slot, after = live[seat["slot_id"]], by_slot[seat["slot_id"]]
                changes = _seat_changes(entry, seat, slot, after, players)
                if not changes:
                    continue
                edits.append((slot.id, changes))
                preview(slot.date, after.date)
                # A note alone doesn't move a shift or cancel its swaps.
                if set(changes) - {"note"}:
                    changed.append(slot.id)
                if seat["to"] != seat["from"] and seat["to"] is not None:
                    placed.setdefault(seat["to"], []).append(after.date)
                if "date" in changes or "player_id" in changes:
                    touched.add(after.ref)

        if held_back:
            plan.warnings.append(f"{held_back} pass behöver ses över och följer inte med.")

        # Conflicted entries stay väntande, so everything below counts them as live.
        effective = effective_seats(session, team_id, skip_conflicted=True)
        plan.warnings.extend(_double_bookings(effective, touched, players))
        plan.warnings.extend(
            f"{players[pid].name} är undantagen (ledarfamilj)."
            for pid in sorted(placed, key=lambda i: (players[i].name, i)) if players[pid].exempt
        )
        plan.warnings.extend(_overlaps(new_heads, effective))
        plan.notes.extend(_staffing_notes(live, effective, placed, players))

        mailed = plan_shift_notices(session, plan, [(live[i], changes) for i, changes in edits])
        plan_swap_cancellations(session, plan, changed=changed, deleted=removed, mailed=mailed)

        def write(s):
            for slot_id, changes in edits:
                slot = s.get(Slot, slot_id)
                for name, value in changes.items():
                    setattr(slot, name, value)
            for slot_id in removed:
                s.delete(s.get(Slot, slot_id))
            for values in additions:
                s.add(Slot(**values))
            for entry_id in published:
                s.delete(s.get(PendingPass, entry_id))

        if published:
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
