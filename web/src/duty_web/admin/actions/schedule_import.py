"""Importera Excel (spec Flows): the coach's file becomes a diff, not a wipe.

Every row is matched to the team's existing slots by the name the file gives
the seat (slots.child_name), never by its current owner, so a swap someone
accepted survives the next import of the same file. The passes, in order:

1. exact: same date, time, activity, station and name → unchanged (a note
   may change, and a seat with no player gets one once the name is known);
2. same person: the same shift, and the seat's old name meant the row's
   player (the roster, or a saved answer) → a respelling, only the record
   changes;
3. dated: an undated slot the file now dates (same activity, time,
   station, person; a weekday note that fits) → the date is set, swaps kept;
4. placed: a seat at the station a corrected saved answer gave its activity
   (same shift otherwise, same person) → the station changes, and its
   pending swaps are cancelled;
5. held: a seat with a player, and a row whose name nobody has answered yet,
   on the same shift (or dating the undated seat) → the seat keeps its
   player and schedule name until the question is answered;
6. moved: the same shift, another name → the player changes, and the
   seat's pending swaps are cancelled;
7. edited: a seat changed under Pass (same activity, same person, the
   nearest date) → the file's date, time and station are put back, and its
   pending swaps are cancelled;
8. left in the file → added; left in the database → removed.

What it may touch: activities in the file, in the seasons its dated rows
fall in, plus undated slots. A slot dated before today, or one made under
Pass (origin "admin"), can match passes 1 and 2 (after every other seat)
but is never changed or removed. A place the file names but the club
doesn't have stops the import. An activity with no place, or a name that
isn't on the roster, becomes a question in Granska; answers are saved when
the plan is applied and changes something, so the next import of the same
file asks nothing.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, time

from ...duties import canonical_duty_name, profile_for
from ...models import (
    ORIGIN_IMPORT,
    ActivityStation,
    NameAlias,
    Player,
    Slot,
    Station,
    Venue,
)
from ...names import normalize_key
from ...places import find_station, find_venue
from ...roster import normalize_name
from ...schedule_xlsx import (
    MAX_BYTES,
    MAX_ROWS,
    WEEKDAY_ABBREVIATIONS,
    ScheduleFileError,
    read_schedule,
)
from ...staffing import season_of
from ..slot_ops import (
    day_label,
    owner_name,
    parse_clock,
    plan_swap_cancellations,
    shift_label,
    slot_basis,
    slot_label,
    slot_order,
)
from .base import Action, Plan, ValidationError, register


def _today() -> date:
    """Today, behind a name the tests can pin."""
    return date.today()


@dataclass
class ScheduleImportInput:
    rows: list[dict]
    names: dict = field(default_factory=dict)      # the file's spelling → player id
    stations: dict = field(default_factory=dict)   # activity → station id


@dataclass(frozen=True)
class _Want:
    """One seat the file asks for, resolved against the team and the places."""

    day: date | None
    start: time
    end: time
    activity: str
    station_id: int | None
    venue: str
    station: str
    player_id: int | None
    player_name: str | None
    spelling: str
    note: str | None

    @property
    def ident(self) -> tuple:
        """Which shift: as _ident, so an undated row's weekday note is part of it."""
        extra = (self.note or "") if self.day is None else None
        return (self.day, self.start, self.end, self.activity, self.station_id, extra)

    @property
    def key(self) -> str:
        return normalize_name(self.spelling)

    @property
    def owner(self) -> str:
        return self.player_name or f"Ledigt ('{self.spelling}')"

    def label(self) -> str:
        return shift_label(self.day, self.start, self.end, self.activity, self.station)

    def order(self) -> tuple:
        return (self.day is None, self.day or date.min, self.start, self.activity, self.spelling)


def _ident(slot: Slot) -> tuple:
    """Which shift a seat is on, as the import compares it.

    agenda.shift_key's identity with the activity canonicalised: an undated
    seat's note (the weekday, "Fredagar") is part of it, so two undated
    shifts at the same time on different days are never mistaken for each
    other.
    """
    extra = (slot.note or "") if slot.date is None else None
    return (
        slot.date, slot.start_time, slot.end_time, canonical_duty_name(slot.duty_name),
        slot.station_id, extra,
    )


WEEKDAY_NOTES = list(WEEKDAY_ABBREVIATIONS.values())   # Måndagar … Söndagar


def _weekday_fits(note: str | None, day: date) -> bool:
    """An undated 'Fredagar' shift can only get a Friday."""
    if note in WEEKDAY_NOTES:
        return WEEKDAY_NOTES.index(note) == day.weekday()
    return True


def _reconcile(slot: Slot, want: _Want, kind: str, resolve) -> tuple[dict, list[str], bool]:
    """What a matched seat needs to become what the file says.

    One rule for every kind of match: "same" (the same shift, the same
    person), "dated" (an undated seat the file now dates), "placed" (a seat
    at the station a corrected saved answer had given its activity), "held"
    (a seat with a player, and a row whose name nobody has answered yet),
    "moved" (the same shift, another name) and "edited" (a seat changed
    under Pass, which the file puts back). Returns the fields to write,
    Granska's detail parts, and whether the change cancels the seat's
    pending swaps. A shift change ("placed", "edited") does. Only a "moved"
    seat, or a seat a corrected saved answer had given to the wrong player,
    changes owner; filling a seat that had none cancels nothing. A "held"
    seat keeps its player and its schedule name until the question is
    answered.
    """
    fields: dict = {}
    diffs: list[str] = []
    cancels = False
    if kind == "edited":
        if slot.date != want.day:
            fields["date"] = want.day
            diffs.append(
                f"datum satt: {day_label(want.day)}" if slot.date is None
                else f"datum {day_label(slot.date)} → {day_label(want.day)}"
            )
        if (slot.start_time, slot.end_time) != (want.start, want.end):
            fields.update(start_time=want.start, end_time=want.end)
            diffs.append(
                f"tid {slot.start_time:%H:%M}–{slot.end_time:%H:%M} → {want.start:%H:%M}–{want.end:%H:%M}"
            )
        if slot.station_id != want.station_id:
            fields.update(station_id=want.station_id, station=want.station, venue=want.venue)
            diffs.append(f"station {slot.station or 'ingen'} → {want.station or 'ingen'}")
        # Every earlier pass took the seats on the row's shift, so this one
        # is on another shift: a change of shift, whatever differs.
        cancels = True
    if kind == "dated" or (kind == "held" and slot.date is None and want.day is not None):
        fields["date"] = want.day
        diffs.append(f"datum satt: {day_label(want.day)}")
    if kind == "placed":
        fields.update(station_id=want.station_id, station=want.station, venue=want.venue)
        diffs.append(f"station {slot.station} → {want.station}")
        cancels = True
    wrong = resolve.corrections.get(want.key)
    if kind == "moved":
        if want.player_id != slot.player_id:
            fields["player_id"] = want.player_id
            diffs.append(f"{owner_name(slot)} → {want.owner}")
            cancels = cancels or slot.player_id is not None
    elif kind != "held":
        if slot.player_id is None and want.player_id is not None:
            fields["player_id"] = want.player_id
            diffs.append(f"{owner_name(slot)} → {want.owner}")
        elif wrong is not None and slot.player_id == wrong.id and want.player_id != wrong.id:
            fields["player_id"] = want.player_id
            diffs.append(f"{owner_name(slot)} → {want.owner}")
            cancels = True
    if kind != "held" and (_spelling(slot) != want.key or (slot.child_name or "") != want.spelling):
        fields["child_name"] = want.spelling
        if "player_id" not in fields:
            diffs.append(f"namnet i schemat: '{slot.child_name or ''}' → '{want.spelling}'")
    if kind != "same" or slot.date is not None:
        # An undated seat's note is its weekday, part of its identity; it
        # only changes when the seat gets a date. A weekday note then just
        # goes, since the date says it: "datum satt" is the whole story.
        if (slot.note or None) != want.note:
            fields["note"] = want.note
            gets_date = slot.date is None and fields.get("date") is not None
            if not (gets_date and slot.note in WEEKDAY_NOTES):
                diffs.append(_note_change(slot.note, want.note))
    return fields, diffs, cancels


def _spelling(slot: Slot) -> str:
    """The name the schedule gave the seat. A swap never changes it."""
    if slot.child_name:
        return normalize_name(slot.child_name)
    return normalize_name(slot.player.name) if slot.player is not None else ""


def _mapping(raw, field_name: str) -> dict:
    if raw in (None, ""):
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ValidationError({field_name: "Skicka ett JSON-objekt"}) from None
    if not isinstance(raw, dict):
        raise ValidationError({field_name: "Skicka ett JSON-objekt"})
    return {str(key): value for key, value in raw.items()}


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _note_change(before: str | None, after: str | None) -> str:
    return f"anteckning: {repr(before) if before else 'ingen'} → {repr(after) if after else 'ingen'}"


def _season_name(day: date) -> str:
    first, last = season_of(day)
    return f"{first.year}/{last.year % 100:02d}"


class _Resolver:
    """Who and where each row means.

    A name: the roster, then this review's answer, then an answer saved by
    an earlier import. A place: the activity's duty profile, then this
    review's answer, then a saved answer. It records which answers were used
    and which are new, for Granska's questions and the writes.
    """

    def __init__(self, session, team_id: int, inp: ScheduleImportInput, roster: list[Player]):
        self.session = session
        self.by_id = {p.id: p for p in roster}
        self.roster: dict[str, Player] = {}
        for player in roster:
            self.roster.setdefault(normalize_name(player.name), player)
        self.answered_names: dict[str, Player] = {}
        for spelling, value in sorted(inp.names.items()):
            if _int(value) not in self.by_id:
                raise ValidationError({"names": f"Okänd spelare för '{spelling}'"})
            self.answered_names[normalize_name(spelling)] = self.by_id[_int(value)]
        aliases = session.query(NameAlias).filter(NameAlias.team_id == team_id).order_by(NameAlias.id).all()
        self.saved_names = {
            alias.spelling_key: self.by_id[alias.player_id]
            for alias in aliases if alias.player_id in self.by_id
        }
        # For the plan's basis: the saved answers as they are now, so one
        # changed (or added) by someone else between review and Genomför is
        # a 409 even when no slot moves.
        self.saved_name_rows = [[a.id, a.spelling_key, a.player_id] for a in aliases]
        self.answered_places: dict[str, Station] = {}
        for activity, value in sorted(inp.stations.items()):
            station = session.get(Station, _int(value)) if _int(value) is not None else None
            if station is None:
                raise ValidationError({"stations": f"Okänd station för '{activity}'"})
            self.answered_places[normalize_key(canonical_duty_name(activity))] = station
        saved_places = (
            session.query(ActivityStation)
            .filter(ActivityStation.team_id == team_id).order_by(ActivityStation.id).all()
        )
        self.saved_places = {row.activity_key: row.station for row in saved_places}
        self.saved_place_rows = [[r.id, r.activity_key, r.station_id] for r in saved_places]
        self.profiles: dict[str, Station | None] = {}
        self.unknown: Counter = Counter()          # spelling → seats
        self.unplaced: Counter = Counter()         # activity → seats
        self.new_names: dict[str, Player] = {}     # answered now: saved at apply
        self.corrections: dict[str, Player] = {}   # spelling key → the player a saved answer gave
        self.saved_used_names: dict[str, Player] = {}
        self.new_places: dict[str, Station] = {}
        self.place_corrections: dict[str, Station] = {}   # activity key → the station a saved answer gave
        self.saved_used_places: dict[str, Station] = {}

    def player(self, spelling: str) -> Player | None:
        key = normalize_name(spelling)
        if key in self.roster:
            return self.roster[key]
        if key in self.answered_names:
            answer = self.answered_names[key]
            saved = self.saved_names.get(key)
            if saved is not None and saved.id != answer.id:
                self.corrections[key] = saved
            if saved is None or saved.id != answer.id:
                self.new_names[spelling] = answer
            return answer
        if key in self.saved_names:
            self.saved_used_names[spelling] = self.saved_names[key]
            return self.saved_names[key]
        self.unknown[spelling] += 1
        return None

    def station(self, activity: str) -> Station | None:
        if activity not in self.profiles:
            profile = profile_for(activity)
            venue = find_venue(self.session, profile.venue) if profile is not None else None
            self.profiles[activity] = (
                find_station(self.session, venue, profile.station) if venue is not None else None
            )
        if self.profiles[activity] is not None:
            return self.profiles[activity]
        key = normalize_key(activity)
        if key in self.answered_places:
            answer = self.answered_places[key]
            saved = self.saved_places.get(key)
            if saved is not None and saved.id != answer.id:
                self.place_corrections[key] = saved
            if saved is None or saved.id != answer.id:
                self.new_places[activity] = answer
            return answer
        if key in self.saved_places:
            self.saved_used_places[activity] = self.saved_places[key]
            return self.saved_places[key]
        self.unplaced[activity] += 1
        return None


def _want(session, i, row, resolve: _Resolver) -> _Want:
    if not isinstance(row, dict):
        raise ValidationError({f"rows[{i}]": "Ogiltig rad"})
    where = f"Rad {row.get('row', i + 2)}"
    raw_day = row.get("date")
    try:
        day = date.fromisoformat(raw_day) if raw_day else None
    except (TypeError, ValueError):
        raise ValidationError({f"rows[{i}].date": f"{where}: ogiltigt datum"}) from None
    start, end = parse_clock(row.get("start")), parse_clock(row.get("end"))
    if start is None or end is None or end <= start:
        raise ValidationError({f"rows[{i}].start": f"{where}: ogiltig tid"})
    activity = canonical_duty_name(str(row.get("activity") or ""))
    spelling = " ".join(str(row.get("player") or "").split())
    if not activity or not spelling:
        raise ValidationError({f"rows[{i}]": f"{where}: saknar aktivitet eller spelare"})

    venue_name = " ".join(str(row.get("venue") or "").split())
    station_name = " ".join(str(row.get("station") or "").split())
    if venue_name or station_name:
        venue = find_venue(session, venue_name) if normalize_key(venue_name) else None
        station = (
            find_station(session, venue, station_name)
            if venue is not None and normalize_key(station_name) else None
        )
        if station is None:
            raise ValidationError({"file": (
                f"Okänd station '{station_name}' på '{venue_name}': "
                "lägg till den under Platser eller rätta filen."
            )})
    else:
        station = resolve.station(activity)

    player = resolve.player(spelling)
    note = " ".join(str(row.get("note") or "").split()) or None
    return _Want(
        day, start, end, activity,
        station.id if station else None,
        station.venue.name if station else venue_name,
        station.name if station else station_name,
        player.id if player else None, player.name if player else None, spelling, note,
    )


def _pair(slots: list[Slot], wants: list[_Want], match, rank=None) -> list[tuple[Slot, _Want]]:
    """Take matching (slot, want) pairs out of both lists.

    Each row takes the first matching slot, or with `rank` the matching
    slot that ranks lowest for it.
    """
    pairs = []
    for want in list(wants):
        if rank is None:
            slot = next((s for s in slots if match(s, want)), None)
        else:
            slot = min((s for s in slots if match(s, want)), key=lambda s: rank(s, want), default=None)
        if slot is not None:
            slots.remove(slot)
            wants.remove(want)
            pairs.append((slot, want))
    return pairs


@register
class ImportSchedule(Action):
    name = "schedule.import"
    title = "Importera Excel"
    scope = "team"
    Input = ScheduleImportInput
    template = "admin/actions/schedule_import.html"
    uploads = ("file",)
    done_endpoint = "admin.pass_tab"
    refinable = True

    def parse(self, fields, files):
        names = _mapping(fields.get("names"), "names")
        stations = _mapping(fields.get("stations"), "stations")
        upload = files.get("file") if files else None
        if upload is not None and upload.filename:
            if not upload.filename.lower().endswith(".xlsx"):
                raise ValidationError({"file": "Välj coachens Excelfil (.xlsx)."})
            try:
                rows = read_schedule(upload.read(MAX_BYTES + 1))
            except ScheduleFileError as exc:
                raise ValidationError({"file": str(exc)}) from None
        elif isinstance(fields.get("rows"), list):
            rows = fields.get("rows")
        else:
            raise ValidationError({"file": "Välj coachens Excelfil (.xlsx)."})
        if not rows:
            raise ValidationError({"file": "Filen innehåller inga pass."})
        return ScheduleImportInput(rows=rows, names=names, stations=stations)

    def refine(self, inp, fields):
        names, stations = dict(inp.names), dict(inp.stations)
        for key, value in fields.items():
            if not value:
                continue
            if key.startswith("name::"):
                names[key[len("name::"):]] = value
            elif key.startswith("station::"):
                stations[key[len("station::"):]] = value
        return replace(inp, names=names, stations=stations)

    def plan(self, session, ctx, inp):
        team_id = ctx.team.id
        today = _today()
        if len(inp.rows) > MAX_ROWS:
            raise ValidationError({"file": f"Högst {MAX_ROWS} pass per fil."})
        roster = (
            session.query(Player).filter(Player.team_id == team_id)
            .order_by(Player.name, Player.id).all()
        )
        resolve = _Resolver(session, team_id, inp, roster)
        wants = [_want(session, i, row, resolve) for i, row in enumerate(inp.rows)]

        # What the file is about: its activities, in its seasons, plus the
        # undated. Everything else on the team is none of its business.
        activities = sorted({want.activity for want in wants})
        seasons = sorted({season_of(want.day) for want in wants if want.day is not None})
        team_slots = session.query(Slot).filter(Slot.team_id == team_id).order_by(Slot.id).all()
        existing = [
            s for s in team_slots
            if canonical_duty_name(s.duty_name) in activities
            and (s.date is None or season_of(s.date) in seasons)
        ]
        untouched = sorted({canonical_duty_name(s.duty_name) for s in team_slots} - set(activities))

        def frozen(slot: Slot) -> bool:
            # History, or a shift made under Pass: matched, never changed.
            return slot.origin != ORIGIN_IMPORT or (slot.date is not None and slot.date < today)

        def meant(slot: Slot) -> Player | None:
            # Who the seat's schedule name meant before this review: the
            # roster, or an answer saved earlier. Never its current owner.
            return resolve.roster.get(_spelling(slot)) or resolve.saved_names.get(_spelling(slot))

        def same_person(slot: Slot, want: _Want) -> bool:
            if _spelling(slot) == want.key:
                return True
            old = meant(slot)
            return want.player_id is not None and old is not None and old.id == want.player_id

        def placed_before(slot: Slot, want: _Want) -> bool:
            # The seat sits where a saved answer put its activity, and the
            # row is at the station this review answers instead.
            key = normalize_key(want.activity)
            old = resolve.place_corrections.get(key)
            new = resolve.answered_places.get(key)
            return (
                old is not None and new is not None and slot.station_id == old.id
                and want.station_id == new.id
            )

        plan = Plan()
        plan.basis.extend(slot_basis(slot) for slot in existing)
        plan.basis.append(["name_aliases", resolve.saved_name_rows])
        plan.basis.append(["activity_stations", resolve.saved_place_rows])

        def unknown(want: _Want) -> bool:
            # Nobody knows who the row means yet: not the roster, no answer.
            return want.player_id is None and want.spelling in resolve.unknown

        def nearest(slot: Slot, want: _Want) -> tuple:
            # The seat whose date is closest to the row's. Undated counts as
            # farthest from a date, and as nearest to an undated row.
            if slot.date is not None and want.day is not None:
                return (0, abs((slot.date - want.day).days), slot.id)
            return (0 if slot.date is None and want.day is None else 1, 0, slot.id)

        # Identity first, arbitrary pairing late. Every pass but "moved" only
        # pairs a seat with a row that means the same person (or, "held",
        # keeps the seat's person), so a respelled or reordered file can't
        # hand one family's seat to another. Passes 1 and 2 may match a
        # frozen seat, but try the others first, so a seat made under Pass
        # only matches when no imported seat does.
        slots = sorted(existing, key=lambda s: (frozen(s), s.id))
        todo = list(wants)
        pairs = [(s, w, "same") for s, w in _pair(
            slots, todo, lambda s, w: _ident(s) == w.ident and _spelling(s) == w.key
        )]
        pairs += [(s, w, "same") for s, w in _pair(
            slots, todo, lambda s, w: _ident(s) == w.ident and same_person(s, w)
        )]
        kept_admin = [s for s in slots if s.origin != ORIGIN_IMPORT]
        kept_past = [s for s in slots if s.origin == ORIGIN_IMPORT and frozen(s)]
        slots = sorted((s for s in slots if not frozen(s)), key=lambda s: s.id)
        skipped = [w for w in todo if w.day is not None and w.day < today]
        todo = [w for w in todo if w.day is None or w.day >= today]
        pairs += [(s, w, "dated") for s, w in _pair(slots, todo, lambda s, w: (
            s.date is None and w.day is not None and _ident(s)[1:5] == w.ident[1:5]
            and _weekday_fits(s.note, w.day) and same_person(s, w)
        ))]
        pairs += [(s, w, "placed") for s, w in _pair(slots, todo, lambda s, w: (
            _ident(s)[:4] + _ident(s)[5:] == w.ident[:4] + w.ident[5:]
            and same_person(s, w) and placed_before(s, w)
        ))]
        # An unanswered name never takes a seat from the player it has.
        pairs += [(s, w, "held") for s, w in _pair(slots, todo, lambda s, w: (
            s.player_id is not None and unknown(w) and _ident(s) == w.ident
        ))]
        pairs += [(s, w, "held") for s, w in _pair(slots, todo, lambda s, w: (
            s.player_id is not None and unknown(w) and s.date is None and w.day is not None
            and _ident(s)[1:5] == w.ident[1:5] and _weekday_fits(s.note, w.day)
        ))]
        pairs += [(s, w, "moved") for s, w in _pair(slots, todo, lambda s, w: _ident(s) == w.ident)]
        # A seat changed under Pass since the last import: the file puts its
        # date, time and station back, and the seat keeps its id. A
        # "Fredagar" seat still only gets a Friday.
        pairs += [(s, w, "edited") for s, w in _pair(slots, todo, lambda s, w: (
            canonical_duty_name(s.duty_name) == w.activity and same_person(s, w)
            and (s.date is not None or w.day is None or _weekday_fits(s.note, w.day))
        ), rank=nearest)]

        edits: list[tuple[int, dict]] = []
        changed: list[int] = []
        touched: list[date | None] = []
        unchanged = 0
        for slot in sorted(slots, key=slot_order):
            plan.add("delete", "seat", slot_label(slot), owner_name(slot))
            touched.append(slot.date)
        for slot, want, kind in sorted(pairs, key=lambda item: slot_order(item[0])):
            fields, diffs, cancels = (
                ({}, [], False) if frozen(slot) else _reconcile(slot, want, kind, resolve)
            )
            if not fields:
                unchanged += 1
                continue
            plan.add("update", "seat", want.label(), "; ".join(diffs))
            edits.append((slot.id, fields))
            if cancels:
                changed.append(slot.id)
            touched.extend([slot.date, fields.get("date", slot.date)])
        for want in sorted(todo, key=_Want.order):
            plan.add("add", "seat", want.label(), want.owner)
            touched.append(want.day)

        # An unknown name's seats: the ones it leaves free (added, or kept
        # Ledigt), and the ones that keep their player until it is answered.
        # A past row or a frozen seat changes nothing either way.
        free = Counter(w.spelling for w in todo if unknown(w))
        free.update(w.spelling for s, w, _ in pairs if unknown(w) and not frozen(s) and s.player_id is None)
        held = Counter(w.spelling for _, w, kind in pairs if kind == "held")
        roster_choices = [[p.id, p.name] for p in roster]
        for spelling in sorted(set(free) | set(held)):
            if free[spelling]:
                plan.warnings.append(
                    f"'{spelling}' finns inte i laglistan: {free[spelling]} pass blir lediga "
                    "tills du väljer vem det är."
                )
            plan.questions.append({
                "field": f"name::{spelling}", "label": f"Vem är '{spelling}'?", "choices": roster_choices,
            })
        for spelling, player in sorted(resolve.saved_used_names.items()):
            plan.questions.append({
                "field": f"name::{spelling}", "label": f"Vem är '{spelling}'? (sparat svar)",
                "choices": roster_choices, "selected": player.id,
            })
        places = None
        if resolve.unplaced or resolve.saved_used_places:
            places = [
                [s.id, f"{s.name} ({s.venue.name})"]
                for s in session.query(Station).join(Venue).order_by(Venue.name, Station.name)
            ]
        for activity, count in sorted(resolve.unplaced.items()):
            plan.warnings.append(
                f"'{activity}' har ingen plats: {count} pass får ingen instruktion tills du väljer station."
            )
            plan.questions.append({
                "field": f"station::{activity}", "label": f"Var hålls '{activity}'?", "choices": places,
            })
        for activity, station in sorted(resolve.saved_used_places.items()):
            plan.questions.append({
                "field": f"station::{activity}", "label": f"Var hålls '{activity}'? (sparat svar)",
                "choices": places, "selected": station.id,
            })
        exempt = Counter(
            w.player_id for w in wants if w.player_id is not None and resolve.by_id[w.player_id].exempt
        )
        for player_id, count in sorted(exempt.items(), key=lambda item: resolve.by_id[item[0]].name):
            plan.warnings.append(
                f"{resolve.by_id[player_id].name} är undantagen (ledarfamilj) men har {count} pass i filen."
            )

        season_text = ", ".join(f"säsongen {_season_name(first)}" for first, _ in seasons)
        plan.notes.append(
            f"Importen gäller {', '.join(activities)}"
            + (f", {season_text}" if season_text else "")
            + " och pass utan datum. Pass före i dag och pass som lagts till under Pass ändras aldrig."
        )
        plan.notes.append(f"{unchanged} pass oförändrade.")
        for spelling, count in sorted(held.items()):
            plan.notes.append(f"{count} pass behåller sin spelare tills du svarar vem '{spelling}' är.")
        if skipped:
            plan.notes.append(f"{len(skipped)} pass före i dag i filen läggs inte till: historiken ändras inte.")
        if kept_past:
            plan.notes.append(f"{len(kept_past)} tidigare pass finns inte i filen men lämnas orörda.")
        if kept_admin:
            plan.notes.append(f"{len(kept_admin)} pass som lagts till under Pass lämnas orörda.")
        if untouched:
            plan.notes.append(f"Pass för {', '.join(untouched)} finns inte i filen och lämnas orörda.")
        # Answers are saved by Genomför, which Granska only offers when
        # something changes; without a change, say so instead of promising.
        for spelling, player in sorted(resolve.new_names.items()):
            plan.notes.append(
                f"Svaret sparas till nästa import: '{spelling}' är {player.name}." if plan.changes
                else f"Svaret om '{spelling}' sparas inte: inget pass ändras av det."
            )
        for activity, station in sorted(resolve.new_places.items()):
            plan.notes.append(
                f"Svaret sparas till nästa import: '{activity}' hålls på {station.name}." if plan.changes
                else f"Svaret om '{activity}' sparas inte: inget pass ändras av det."
            )

        removed = [slot.id for slot in slots]
        plan_swap_cancellations(session, plan, changed=changed, deleted=removed)
        # Every day an edit, an add or a removal lands on, before and after.
        for day in touched:
            if day is None:
                plan.preview_undated = True
            else:
                plan.preview_dates.add(day)
        adds = sorted(todo, key=_Want.order)
        new_names = [(spelling, player.id) for spelling, player in sorted(resolve.new_names.items())]
        new_places = [(activity, station.id) for activity, station in sorted(resolve.new_places.items())]

        def write(s):
            for slot_id in removed:
                s.delete(s.get(Slot, slot_id))
            for slot_id, fields in edits:
                slot = s.get(Slot, slot_id)
                for name, value in fields.items():
                    setattr(slot, name, value)
            for want in adds:
                s.add(Slot(
                    team_id=team_id, date=want.day, start_time=want.start, end_time=want.end,
                    station=want.station, venue=want.venue, duty_name=want.activity,
                    station_id=want.station_id, player_id=want.player_id,
                    child_name=want.spelling, note=want.note, origin=ORIGIN_IMPORT,
                ))

        def save_answers(s):
            for spelling, player_id in new_names:
                key = normalize_name(spelling)
                alias = s.query(NameAlias).filter_by(team_id=team_id, spelling_key=key).one_or_none()
                if alias is None:
                    s.add(NameAlias(team_id=team_id, spelling_key=key, spelling=spelling, player_id=player_id))
                else:
                    alias.spelling, alias.player_id = spelling, player_id
            for activity, station_id in new_places:
                key = normalize_key(activity)
                row = s.query(ActivityStation).filter_by(team_id=team_id, activity_key=key).one_or_none()
                if row is None:
                    s.add(ActivityStation(team_id=team_id, activity_key=key, activity=activity,
                                          station_id=station_id))
                else:
                    row.activity, row.station_id = activity, station_id

        if removed or edits or adds:
            plan.writes.append(write)
        if (new_names or new_places) and plan.changes:
            plan.writes.append(save_answers)
        return plan
