"""The team's people as the admin lists them: ansvariga, ledare, föräldrar."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models import ROLE_ANSVARIG, ROLE_LEADER, Person, Player, Team, TeamRole
from ..roster import public_name


@dataclass
class RosterEntry:
    person: Person
    children: list[Player] = field(default_factory=list)
    roles: set[str] = field(default_factory=set)


def roster_groups(session: Session, team: Team) -> dict[str, list]:
    players = (
        session.query(Player).filter_by(team_id=team.id).order_by(Player.name).all()
    )
    entries: dict[int, RosterEntry] = {}
    for player in players:
        for parent in player.parents:
            entries.setdefault(parent.id, RosterEntry(parent)).children.append(player)
    for role in session.query(TeamRole).filter_by(team_id=team.id):
        if role.person_id in entries:
            entries[role.person_id].roles.add(role.role)
    ordered = sorted(entries.values(), key=lambda e: public_name(e.person).casefold())
    return {
        "ansvariga": [e for e in ordered if ROLE_ANSVARIG in e.roles],
        "ledare": [e for e in ordered if ROLE_LEADER in e.roles and ROLE_ANSVARIG not in e.roles],
        "foraldrar": [e for e in ordered if not e.roles],
        "utan_foraldrar": [p for p in players if not p.parents],
    }
