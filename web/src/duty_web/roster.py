"""Who a duty belongs to: players and their parents.

A duty is the player's, and either parent may staff or trade it — so every
"is this mine?" question in the app resolves through parent_players rather
than through a single owning Person.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy.orm import Session

from .models import Person, Player, Slot, Team, parent_players

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


def teams_for_parent(session: Session, person_id: int) -> list[Team]:
    """Every team this parent has a child on, each once, by name.

    A parent's only way into a team is through a child on it, so this is
    also the list of teams whose calendar and documents they may see.
    """
    return (
        session.query(Team)
        .join(Player, Player.team_id == Team.id)
        .join(parent_players, parent_players.c.player_id == Player.id)
        .filter(parent_players.c.person_id == person_id)
        .distinct()
        .order_by(Team.name)
        .all()
    )


def parents_of_player(session: Session, player_id: int) -> list[Person]:
    player = session.get(Player, player_id)
    return list(player.parents) if player is not None else []


def parents_of_slot(session: Session, slot: Slot | None) -> list[Person]:
    """Everyone who can act on this slot — both parents, when both are known."""
    if slot is None or slot.player_id is None:
        return []
    return parents_of_player(session, slot.player_id)


NAMELESS = "(namn saknas)"


def public_name(person: Person) -> str:
    """A parent's name as other parents may see it.

    Never the address: some rows were created with the address standing in
    for a name, and that must not reach anyone else.
    """
    if not person.name or "@" in person.name:
        return NAMELESS
    return person.name


def parent_names_of_slot(session: Session, slot: Slot | None) -> str:
    """How to name a slot's owners in prose: 'Hans Ahlqvist & Lena Ahlqvist'.

    The Jinja equivalent lives in _names.html's slot_parents macro; both
    fall back to the player's own name when no parent is on file yet.
    """
    parents = parents_of_slot(session, slot)
    if parents:
        return " & ".join(public_name(parent) for parent in parents)
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
