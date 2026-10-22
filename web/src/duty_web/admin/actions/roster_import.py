"""roster.import: reconcile a team with 360Player's members export.

Additive like the old roster-push: players, guardians and links are added,
names updated, nobody removed — people missing from the export are listed
as a warning for the ansvarig to decide on. New: leaders are recognized
and their children marked exempt from shifts (spec D5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...models import ROLE_LEADER, Person, Player, Slot, Team, TeamRole, parent_players
from ...roster import normalize_name, public_name
from ..members_csv import MAX_BYTES, Roster, interpret, parse_members_csv
from ..errors import ValidationError
from .base import Action, Plan, register


@dataclass
class RosterImportInput:
    rows: list[dict]


@dataclass
class _Delta:
    new_players: list[str] = field(default_factory=list)
    new_people: list[tuple[str, str]] = field(default_factory=list)   # (email, name)
    renames: list[tuple[int, str]] = field(default_factory=list)      # (person id, name)
    new_links: list[tuple[str, str]] = field(default_factory=list)    # (email, player key)
    add_leaders: list[str] = field(default_factory=list)              # emails
    remove_leaders: list[int] = field(default_factory=list)           # person ids
    exempt: list[tuple[str, bool]] = field(default_factory=list)      # (player key, exempt)


def _upcoming(session: Session, player: Player) -> int:
    return (
        session.query(Slot)
        .filter(Slot.player_id == player.id, or_(Slot.date.is_(None), Slot.date >= date.today()))
        .count()
    )


def _reconcile(session: Session, team: Team, roster: Roster) -> tuple[Plan, _Delta]:
    plan, delta = Plan(), _Delta()
    players = {normalize_name(p.name): p for p in session.query(Player).filter_by(team_id=team.id).order_by(Player.id)}
    people = {p.email: p for p in session.query(Person).order_by(Person.id)}
    # Only people already tied to THIS team may be renamed or have their stored
    # name shown; anyone else matched by email is linked, never touched.
    team_people = {
        pid for (pid,) in session.query(parent_players.c.person_id)
        .join(Player, Player.id == parent_players.c.player_id)
        .filter(Player.team_id == team.id)
    } | {pid for (pid,) in session.query(TeamRole.person_id).filter_by(team_id=team.id)}
    links = {
        (person_id, player_id)
        for person_id, player_id in session.query(
            parent_players.c.person_id, parent_players.c.player_id
        )
    }

    def player_name(key: str) -> str:
        return players[key].name if key in players else roster.players.get(key, key)

    for key in sorted(roster.players, key=lambda k: (roster.players[k].casefold(), k)):
        if key not in players:
            delta.new_players.append(roster.players[key])
            plan.add("add", "player", f"Ny spelare: {roster.players[key]}")

    for email in sorted(roster.guardians, key=lambda e: (roster.guardians[e].name.casefold(), e)):
        guardian, person = roster.guardians[email], people.get(email)
        if person is None:
            delta.new_people.append((email, guardian.name))
            plan.add("add", "parent", f"Ny vårdnadshavare: {guardian.name}",
                     "till " + ", ".join(sorted(player_name(k) for k in guardian.children)))
        elif person.id in team_people and guardian.name and person.name != guardian.name:
            delta.renames.append((person.id, guardian.name))
            plan.add("update", "parent", "Namn ändrat", f"{public_name(person)} → {guardian.name}")
        for key in sorted(guardian.children):
            player = players.get(key)
            if person is not None and player is not None and (person.id, player.id) in links:
                continue
            delta.new_links.append((email, key))
            if person is not None:  # a new guardian's links are on their own line
                plan.add("add", "link", f"{guardian.name} kopplas till {player_name(key)}")

    current = session.query(TeamRole).filter_by(team_id=team.id, role=ROLE_LEADER).all()
    current_ids = {role.person_id for role in current}
    desired_ids = {people[e].id for e in roster.leaders if e in people}
    for email in sorted(roster.leaders, key=lambda e: (roster.guardians[e].name.casefold(), e)):
        person = people.get(email)
        if person is None or person.id not in current_ids:
            delta.add_leaders.append(email)
            plan.add("add", "leader", f"Ledare: {roster.guardians[email].name}")
    for role in sorted(current, key=lambda r: (public_name(r.person).casefold(), r.person_id)):
        if role.person_id not in desired_ids:
            delta.remove_leaders.append(role.person_id)
            plan.add("delete", "leader", f"Inte längre ledare: {public_name(role.person)}")

    # Exempt follows the parents each player will have after this import.
    email_by_id = {p.id: e for e, p in people.items()}
    key_by_id = {p.id: k for k, p in players.items()}
    parents: dict[str, set[str]] = {}
    for person_id, player_id in links:
        if player_id in key_by_id:
            parents.setdefault(key_by_id[player_id], set()).add(email_by_id[person_id])
    for email, key in delta.new_links:
        parents.setdefault(key, set()).add(email)
    final_exempt = {}
    for key in sorted(set(players) | set(roster.players), key=lambda k: (player_name(k).casefold(), k)):
        exempt = bool(parents.get(key, set()) & roster.leaders)
        final_exempt[key] = exempt
        if exempt != (players[key].exempt if key in players else False):
            delta.exempt.append((key, exempt))
            plan.add(
                "update", "exempt",
                f"Står inte på pass: {player_name(key)}" if exempt else f"Står på pass igen: {player_name(key)}",
            )

    if roster.guardians_without_email:
        plan.warnings.append(
            f"{roster.guardians_without_email} vårdnadshavare saknar e-post i 360Player "
            "och kan inte logga in."
        )
    if roster.children_without_row:
        plan.warnings.append(
            f"{roster.children_without_row} barn i föräldrarnas rader finns inte som spelare "
            "i listan och hoppades över."
        )
    if roster.ignored_members:
        plan.warnings.append(
            f"{roster.ignored_members} medlemmar utan barn eller föräldrar i laget hoppades över."
        )
    for key, player in sorted(players.items(), key=lambda item: (item[1].name.casefold(), item[0])):
        if key not in roster.players:
            plan.warnings.append(
                f"{player.name} finns inte längre i 360Player ({_upcoming(session, player)} "
                "kommande pass). Ingenting tas bort."
            )
        elif final_exempt.get(key) and (count := _upcoming(session, player)):
            plan.warnings.append(
                f"{player.name} står inte på pass men har {count} kommande pass. "
                "Lägg dem på någon annan."
            )
    return plan, delta


def _write(session: Session, team_id: int, delta: _Delta) -> None:
    players = {normalize_name(p.name): p for p in session.query(Player).filter_by(team_id=team_id).order_by(Player.id)}
    for name in delta.new_players:
        player = Player(name=name, team_id=team_id)
        session.add(player)
        players[normalize_name(name)] = player
    session.flush()
    people = {p.email: p for p in session.query(Person).order_by(Person.id)}
    for email, name in delta.new_people:
        person = Person(name=name, email=email)
        session.add(person)
        people[email] = person
    session.flush()
    for person_id, name in delta.renames:
        session.get(Person, person_id).name = name
    for email, key in delta.new_links:
        session.execute(parent_players.insert().values(
            person_id=people[email].id, player_id=players[key].id
        ))
    for email in delta.add_leaders:
        session.add(TeamRole(person_id=people[email].id, team_id=team_id, role=ROLE_LEADER))
    for person_id in delta.remove_leaders:
        session.query(TeamRole).filter_by(
            person_id=person_id, team_id=team_id, role=ROLE_LEADER
        ).delete()
    for key, exempt in delta.exempt:
        players[key].exempt = exempt


@register
class RosterImport(Action):
    name = "roster.import"
    title = "Uppdatera från 360Player"
    scope = "team"
    Input = RosterImportInput
    template = "admin/actions/roster_import.html"
    uploads = ("file",)

    def parse(self, fields, files):
        upload = files.get("file") if files else None
        if upload is not None and upload.filename:
            if not upload.filename.lower().endswith(".csv"):
                raise ValidationError({"file": "Välj CSV-filen som 360Player exporterar."})
            data = upload.read(MAX_BYTES + 1)
        elif fields.get("csv"):
            data = str(fields.get("csv")).encode("utf-8")
        else:
            raise ValidationError({"file": "Välj filen från 360Player (Truppen → Exportera)."})
        return RosterImportInput(rows=parse_members_csv(data))

    def plan(self, session, ctx, inp):
        plan, delta = _reconcile(session, ctx.team, interpret(inp.rows))
        team_id = ctx.team.id
        plan.writes.append(lambda s: _write(s, team_id, delta))
        return plan
