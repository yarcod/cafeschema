"""360Player's members export, read into who is a player, a guardian, a leader.

The export (Truppen → Exportera) has one row per membership. A player's row
names their parents (`Föräldrars namn`, ';'-separated); a guardian's row
names their children (`Barnens namn`). A leader appears twice: once as a
guardian, and once as a membership with neither column filled — that
second row, with an email that belongs to a guardian, is how a leader is
recognized (spec D5). The phone-number column is never read.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from ..roster import normalize_name
from .errors import ValidationError

MAX_BYTES = 1_000_000
COLUMNS = ("Förnamn", "Efternamn", "E-post", "Föräldrars namn", "Barnens namn")


def _names(value: str | None) -> list[str]:
    return [" ".join(part.split()) for part in (value or "").split(";") if part.strip()]


def parse_members_csv(data: bytes) -> list[dict]:
    if len(data) > MAX_BYTES:
        raise ValidationError({"file": "Filen är för stor (högst 1 MB)."})
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            {"file": "Filen är inte UTF-8. Exportera den igen direkt från 360Player."}
        ) from exc
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValidationError({
            "file": "Det här ser inte ut som 360Players medlemsexport (Truppen → Exportera). "
            f"Saknar: {', '.join(missing)}."
        })
    rows = [
        {
            "name": " ".join(f"{row['Förnamn'] or ''} {row['Efternamn'] or ''}".split()),
            "email": (row["E-post"] or "").strip().lower(),
            "parents": _names(row["Föräldrars namn"]),
            "children": _names(row["Barnens namn"]),
        }
        for row in reader
    ]
    if not rows:
        raise ValidationError({"file": "Filen innehåller inga medlemmar."})
    return rows


@dataclass
class Guardian:
    name: str
    children: set[str] = field(default_factory=set)  # normalize_name keys


@dataclass
class Roster:
    players: dict[str, str] = field(default_factory=dict)        # key → name
    guardians: dict[str, Guardian] = field(default_factory=dict)  # email → guardian
    leaders: set[str] = field(default_factory=set)               # emails
    guardians_without_email: int = 0
    ignored_members: int = 0
    children_without_row: int = 0


def interpret(rows: list[dict]) -> Roster:
    roster = Roster()
    for row in rows:
        if row["parents"]:
            roster.players.setdefault(normalize_name(row["name"]), row["name"])
    missing: set[str] = set()
    for row in rows:
        if not row["children"]:
            continue
        # Players come only from player rows: a sibling on another team,
        # named on a guardian's row, is not a player here.
        known = {normalize_name(c) for c in row["children"]} & set(roster.players)
        missing |= {normalize_name(c) for c in row["children"]} - set(roster.players)
        if not row["email"]:
            roster.guardians_without_email += 1
            continue
        if not known:
            continue
        guardian = roster.guardians.setdefault(row["email"], Guardian(row["name"]))
        guardian.children.update(known)
    roster.children_without_row = len(missing)
    for row in rows:
        if row["parents"] or row["children"]:
            continue
        if row["email"] in roster.guardians:
            roster.leaders.add(row["email"])
        else:
            roster.ignored_members += 1
    return roster
