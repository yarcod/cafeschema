"""Who may administer which team (spec D8, *Permissions*).

One check for both doors: AdminContext is built before any admin page or
API call. A caller with no admin role in the team gets None here and a 404
from the door, never a 403, so other teams' existence isn't revealed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from ..models import (
    ROLE_ANSVARIG,
    ClubAdmin,
    Person,
    Player,
    Team,
    TeamRole,
    parent_players,
)


@dataclass(frozen=True)
class AdminContext:
    person: Person
    # None for club-wide API calls that name no team.
    team: Team | None
    is_club_admin: bool
    # Every team this person may administer, by name.
    teams: tuple[Team, ...]


def is_club_admin(session: Session, person_id: int) -> bool:
    return session.get(ClubAdmin, person_id) is not None


def is_ansvarig(session: Session, person_id: int, team_id: int) -> bool:
    return (
        session.get(TeamRole, (person_id, team_id, ROLE_ANSVARIG)) is not None
    )


def admin_teams(session: Session, person_id: int) -> list[Team]:
    if is_club_admin(session, person_id):
        return session.query(Team).order_by(Team.name).all()
    return (
        session.query(Team)
        .join(TeamRole, TeamRole.team_id == Team.id)
        .filter(TeamRole.person_id == person_id, TeamRole.role == ROLE_ANSVARIG)
        .order_by(Team.name)
        .all()
    )


def admin_context(
    session: Session, person: Person | None, slug: str | None
) -> AdminContext | None:
    if person is None:
        return None
    teams = admin_teams(session, person.id)
    if not teams:
        return None
    team = None
    if slug is not None:
        team = next((t for t in teams if t.slug == slug), None)
        if team is None:
            return None
    return AdminContext(
        person=person,
        team=team,
        is_club_admin=is_club_admin(session, person.id),
        teams=tuple(teams),
    )


def team_roster(session: Session, team: Team) -> list[Person]:
    """The team's parents: the only people who can be made ansvarig there."""
    return (
        session.query(Person)
        .join(parent_players, parent_players.c.person_id == Person.id)
        .join(Player, Player.id == parent_players.c.player_id)
        .filter(Player.team_id == team.id)
        .distinct()
        .order_by(Person.name)
        .all()
    )


def seed_club_admins(session: Session, emails: Iterable[str]) -> list[str]:
    """Make the configured people club admins, only while there are none.

    After that the database decides, so a changed environment variable can
    never quietly grant anyone rights. Returns the emails that matched
    nobody.
    """
    if session.query(ClubAdmin).count():
        return []
    skipped = []
    for email in emails:
        person = session.query(Person).filter_by(email=email).one_or_none()
        if person is None:
            skipped.append(email)
        else:
            session.add(ClubAdmin(person_id=person.id))
    session.commit()
    return skipped
