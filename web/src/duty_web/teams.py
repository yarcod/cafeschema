"""The club's teams, and the short name (slug) each one goes by.

Nothing in the app creates a team on its own; an operator does, once per
team, via `just team-add`. The slug is what everything else names the team
by — ?lag=<slug>, /data/dokument/<slug>/, and the push recipes — so it is
kept to characters that are safe in all three.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from .models import Team

SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")


class TeamError(Exception):
    """A team that can't be created or found. Operator-facing."""


def validate_slug(slug: str) -> str:
    if not SLUG_PATTERN.fullmatch(slug):
        raise TeamError(
            f"Ogiltig slug '{slug}': bara a–z, 0–9 och bindestreck, t.ex. 'f15'."
        )
    return slug


def create_team(session: Session, *, name: str, slug: str, venue: str) -> Team:
    validate_slug(slug)
    name = " ".join(name.split())
    if not name:
        raise TeamError("Gruppen behöver ett namn.")
    if session.query(Team).filter_by(slug=slug).one_or_none() is not None:
        raise TeamError(f"Det finns redan en grupp med slug '{slug}'.")
    team = Team(name=name, slug=slug, venue=venue.strip())
    session.add(team)
    session.commit()
    return team


def team_by_slug(session: Session, slug: str) -> Team:
    team = session.query(Team).filter_by(slug=slug).one_or_none()
    if team is None:
        known = ", ".join(t.slug for t in session.query(Team).order_by(Team.slug))
        raise TeamError(f"Ingen grupp med slug '{slug}'. Finns: {known or 'inga'}.")
    return team
