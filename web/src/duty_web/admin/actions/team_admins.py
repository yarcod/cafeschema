"""team_admins.add / team_admins.remove (spec D8, *Permissions*).

Any ansvarig may make another person on the team's roster ansvarig: this
isn't mission-critical, and handing out the permission is a statement of
trust. Only a club admin can remove a team's last ansvarig, so a team is
never left without one by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import url_for

from ... import notifications
from ...models import ROLE_ANSVARIG, Person, TeamRole
from ...roster import public_name
from ..permissions import is_ansvarig, team_roster
from .base import Action, Plan, ValidationError, register


@dataclass
class TeamAdminInput:
    person_id: int


def _person_on_roster(session, ctx, person_id: int) -> Person:
    person = next((p for p in team_roster(session, ctx.team) if p.id == person_id), None)
    if person is None:
        raise ValidationError({"person_id": "Personen finns inte i lagets lista"})
    return person


@register
class AddTeamAdmin(Action):
    name = "team_admins.add"
    title = "Gör till ansvarig"
    scope = "team"
    Input = TeamAdminInput
    template = "admin/actions/generic.html"

    def form_context(self, session, ctx):
        people = [p for p in team_roster(session, ctx.team) if not is_ansvarig(session, p.id, ctx.team.id)]
        return {"labels": {"person_id": "Person"},
                "choices": {"person_id": [(p.id, public_name(p)) for p in people]}}

    def plan(self, session, ctx, inp):
        person = _person_on_roster(session, ctx, inp.person_id)
        name = public_name(person)
        if is_ansvarig(session, person.id, ctx.team.id):
            raise ValidationError({"person_id": f"{name} är redan ansvarig"})
        plan = Plan()
        plan.add("add", "ansvarig", f"Ny ansvarig: {name}")
        plan.warnings.append(f"{name} får ett mail om det.")
        person_id, team_id = person.id, ctx.team.id
        email, team_name, slug = person.email, ctx.team.name, ctx.team.slug
        plan.writes.append(lambda s: s.add(TeamRole(person_id=person_id, team_id=team_id, role=ROLE_ANSVARIG)))
        plan.after_commit.append(lambda: notifications.send_admin_granted(
            email, team_name, url_for("admin.lag", slug=slug, _external=True)
        ))
        return plan


@register
class RemoveTeamAdmin(Action):
    name = "team_admins.remove"
    title = "Ta bort som ansvarig"
    scope = "team"
    Input = TeamAdminInput
    template = "admin/actions/generic.html"

    def form_context(self, session, ctx):
        people = [p for p in team_roster(session, ctx.team) if is_ansvarig(session, p.id, ctx.team.id)]
        return {"labels": {"person_id": "Person"},
                "choices": {"person_id": [(p.id, public_name(p)) for p in people]}}

    def plan(self, session, ctx, inp):
        role = session.get(TeamRole, (inp.person_id, ctx.team.id, ROLE_ANSVARIG))
        if role is None:
            raise ValidationError({"person_id": "Personen är inte ansvarig i laget"})
        remaining = session.query(TeamRole).filter_by(team_id=ctx.team.id, role=ROLE_ANSVARIG).count()
        if remaining == 1 and not ctx.is_club_admin:
            raise ValidationError({"person_id": "Det måste finnas minst en ansvarig kvar i laget. Be klubbadmin om hjälp."})
        plan = Plan()
        plan.add("delete", "ansvarig", f"Inte längre ansvarig: {public_name(role.person)}")
        person_id, team_id = inp.person_id, ctx.team.id
        plan.writes.append(lambda s: s.query(TeamRole).filter_by(
            person_id=person_id, team_id=team_id, role=ROLE_ANSVARIG
        ).delete())
        return plan
