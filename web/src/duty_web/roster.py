"""Who a duty belongs to: players, their parents, and the roster import.

A duty is the player's, and either parent may staff or trade it — so every
"is this mine?" question in the app resolves through parent_players rather
than through a single owning Person.
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .models import Person, Player, Slot, Team, parent_players

ROSTER_COLUMNS = ("parent_name", "parent_email", "children_on_team")


class RosterError(Exception):
    """Raised when the roster file cannot be imported. Operator-facing."""


def normalize_name(name: object) -> str:
    """Fold the spacing/case/unicode differences between two spellings.

    The schedule and the 360Player export write the same child's name with
    different whitespace and capitalisation; matching on this rather than
    on the raw string keeps those rows joined.
    """
    return unicodedata.normalize("NFKC", " ".join(str(name).split())).casefold()


def player_ids_for_parent(session: Session, person_id: int) -> set[int]:
    """Every player this parent may act for."""
    rows = (
        session.query(parent_players.c.player_id)
        .filter(parent_players.c.person_id == person_id)
        .all()
    )
    return {row[0] for row in rows}


def parents_of_player(session: Session, player_id: int) -> list[Person]:
    player = session.get(Player, player_id)
    return list(player.parents) if player is not None else []


def parents_of_slot(session: Session, slot: Slot | None) -> list[Person]:
    """Everyone who can act on this slot — both parents, when both are known."""
    if slot is None or slot.player_id is None:
        return []
    return parents_of_player(session, slot.player_id)


def parent_names_of_slot(session: Session, slot: Slot | None) -> str:
    """How to name a slot's owners in prose: 'Hans Ahlqvist & Lena Ahlqvist'.

    The Jinja equivalent lives in _names.html's slot_parents macro; both
    fall back to the player's own name when no parent is on file yet.
    """
    parents = parents_of_slot(session, slot)
    if parents:
        return " & ".join(parent.name for parent in parents)
    if slot is not None and slot.player is not None:
        return slot.player.name
    return "Ledigt"


def notifiable_emails(session: Session, slot: Slot | None) -> list[str]:
    """Slot owners who still want mail about it."""
    return [
        parent.email
        for parent in parents_of_slot(session, slot)
        if parent.email_notifications
    ]


def parent_owns_slot(session: Session, person_id: int, slot: Slot | None) -> bool:
    return (
        slot is not None
        and slot.player_id is not None
        and slot.player_id in player_ids_for_parent(session, person_id)
    )


@dataclass
class RosterImport:
    """What a roster import changed. Everything it reports is an addition."""

    players_added: int = 0
    parents_added: int = 0
    parents_renamed: int = 0
    links_added: int = 0
    rows_without_email: int = 0


def _read_roster_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise RosterError(f"Lagfilen hittades inte: {path}")

    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ROSTER_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise RosterError(f"Lagfilen saknar kolumn(er): {', '.join(missing)}")
        return [dict(row) for row in reader]


def import_roster(session: Session, path: str | Path, *, team_id: int) -> RosterImport:
    """Reconcile players and parents from a 360Player contact export.

    Additive on purpose: it adds players, parents and the links between
    them, and updates a parent's name when the export spells it
    differently, but it never removes anything. Re-running it after a new
    parent registers in 360Player adds exactly that parent, and re-running
    it unchanged is a no-op — so this can be kept in sync without ever
    rebuilding the database or disturbing live swap state.
    """
    rows = _read_roster_rows(Path(path))

    # Same reasoning as import_schedule: SQLite won't catch a dangling
    # team_id on its own, and failing before any write is cheaper than
    # discovering it afterwards.
    if session.get(Team, team_id) is None:
        raise RosterError(
            f"Ingen grupp med team_id={team_id} finns. Skapa gruppen innan import."
        )

    players_by_name = {
        normalize_name(player.name): player for player in session.query(Player).all()
    }
    people_by_email = {
        person.email: person for person in session.query(Person).all()
    }
    existing_links = {
        (row.person_id, row.player_id)
        for row in session.query(
            parent_players.c.person_id, parent_players.c.player_id
        ).all()
    }

    result = RosterImport()
    for row_number, row in enumerate(rows, start=2):
        player_name = " ".join(str(row.get("children_on_team") or "").split())
        if not player_name:
            raise RosterError(f"Rad {row_number}: saknar spelarnamn")

        player = players_by_name.get(normalize_name(player_name))
        if player is None:
            player = Player(name=player_name, team_id=team_id)
            session.add(player)
            session.flush()
            players_by_name[normalize_name(player_name)] = player
            result.players_added += 1

        email = str(row.get("parent_email") or "").strip().lower()
        parent_name = " ".join(str(row.get("parent_name") or "").split())
        if not email:
            # The player is real and keeps their slots; this household just
            # has no way in yet. Nothing to link, and nothing to fail over.
            result.rows_without_email += 1
            continue

        person = people_by_email.get(email)
        if person is None:
            person = Person(name=parent_name or email, email=email)
            session.add(person)
            session.flush()
            people_by_email[email] = person
            result.parents_added += 1
        elif parent_name and person.name != parent_name:
            person.name = parent_name
            result.parents_renamed += 1

        if (person.id, player.id) not in existing_links:
            session.execute(
                parent_players.insert().values(
                    person_id=person.id, player_id=player.id
                )
            )
            existing_links.add((person.id, player.id))
            result.links_added += 1

    session.commit()
    return result
