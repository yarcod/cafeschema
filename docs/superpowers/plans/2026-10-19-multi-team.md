# Multi-Team Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let duty_web host several teams in the same club. Each team has its own roster, schedule, swaps, calendar and instructions. Teams never interact, but a parent with children on more than one team logs in once and sees all their duties in one "Mina pass" list.

**Architecture:** The `teams` table and `team_id` on `players`/`slots` already exist. This plan:
- gives teams a URL- and folder-safe `slug`;
- makes player names unique per team instead of globally;
- scopes both imports to one team;
- keeps swaps inside one team;
- lets "Hela laget" switch between the parent's own teams.

Instructions get two levels. Files at the root of the documents folder are shared by the club. Files in `<documents_dir>/<slug>/` belong to one team, and a team's copy wins over a shared file with the same name.

**Tech Stack:** Python 3, Flask, Flask-Login, SQLAlchemy 2 (SQLite), Jinja2, pytest, `just`, Fly.io.

**Spec:** There is no separate spec. The design was agreed in conversation on 2026-10-19 and is summarized under **Design** below.

## Design

- **Single login, unified schedule:** `Person` is keyed by email and belongs to no team. A parent reaches a team only through their children (`parent_players` → `Player.team_id`). "Mina pass" already lists slots for all of a parent's players, so it merges teams without any change.
- **Isolation:** a parent sees only the calendars and team documents of teams where they have a child. Swaps are proposed and offered only within the slot's own team. Requests for another team's calendar or documents return 404, not 403.
- **Team identity:** `Team.slug` (lowercase `a-z0-9-`, unique, e.g. `f15`, `p13`) names the team in `?lag=<slug>`, in `/data/dokument/<slug>/` and in `just` recipes.
- **Duty profiles stay club-wide** (`duties.py`). The teams share duties for now. A duty that only one team runs simply sits in the catalogue unused by the others. If two teams ever need different venue/station for the same name, give the duty its own name, as with "Cafépass Arena A".
- **Documents:** `DutyProfile.document` stays a bare filename. To resolve it for a slot, look in `<documents_dir>/<team slug>/` first, then in `<documents_dir>/`. Moving a team's instruction to shared later is just moving the file. The reminder mail gets its link from `/api/schedule`, so it follows automatically.
- **No new tables.**

## Global Constraints

- All user-facing text is Swedish and follows the existing tone. Code comments and docstrings are English, as now.
- Commits follow the repo's style: Swedish conventional commits (`feat(web): …`, `fix(web): …`), with a body explaining *why*, ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Run web tests from `web/`: `cd web && uv run pytest`.
- **Baseline:** the whole web suite passes (152) on `main` before this plan. It must stay fully green after every task; "all pass except the baseline" below means all pass.
- The existing team in production is `F15`. Its slug is `f15`.
- No new dependencies.

## Review Focus

1. **Pushing one team's schedule must not wipe another team's season.** `import_schedule` currently deletes *every* slot. Pinned in Task 2.
2. **A child whose name also exists on another team must become a separate player**, never linked to the other team's child, so their parents don't cross over. Pinned in Task 2.
3. **A parent with children on two teams must not be offered, or be able to POST, a swap across teams.** Pinned in Task 5, at both the candidate query and the `propose_swap` guard.
4. **Team documents must be unreachable to non-members by any URL shape**, including `%2F` in the shared route, while old reminder links to `/dokument/<file>.pdf` keep working. Pinned in Task 7.
5. **Re-running the production migration must be a no-op that keeps every id, parent link and pending swap.** Pinned in Task 4.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `web/src/duty_web/models.py` | modify | `Team.slug`, per-team unique player name |
| `web/src/duty_web/roster.py` | modify | `teams_for_parent`, team-scoped roster matching |
| `web/src/duty_web/seed.py` | modify | schedule import replaces only its team's slots |
| `web/src/duty_web/teams.py` | create | slug validation, `create_team`, `team_by_slug` |
| `scripts/remote_team_add.py` | create | create a team on the Fly machine |
| `scripts/remote_import.py`, `scripts/remote_roster_import.py`, `scripts/test_swap_fixture.py` | modify | take a team slug instead of `Team.first()` |
| `scripts/migrate_multi_team.py` | create | one-off, idempotent prod migration |
| `web/src/duty_web/schedule_queries.py` | modify | `swap_candidates_by_slot` (per-team candidates) |
| `web/src/duty_web/swaps.py` | modify | refuse cross-team swaps |
| `web/src/duty_web/routes/schedule_routes.py` | modify | per-slot candidates, team switcher, `show_team`, API doc URL per team |
| `web/src/duty_web/routes/document_routes.py` | modify | shared and team folders, access check, grouped listing |
| `web/src/duty_web/app.py` | modify | `slot_document_url` filter |
| `web/src/duty_web/templates/{team,team_empty,dokument,mine}.html` | modify/create | UI |
| `web/src/duty_web/static/style.css` | modify | `.team-switch`, `.doc-group`, `.slot-team` |
| `web/src/duty_web/duties.py`, `web/src/duty_web/routes/auth_routes.py` | modify | comments only |
| `justfile`, `web/README.md` | modify | team-aware recipes and runbook |
| `web/tests/conftest.py` | modify | `second_team` fixture |

---

### Task 1: Team slug, per-team player names, `teams_for_parent`

**Files:**
- Modify: `web/src/duty_web/models.py` (`Team`, `Player`)
- Modify: `web/src/duty_web/roster.py` (add `teams_for_parent` after `player_ids_for_parent`)
- Modify: every `Team(name="F14 Blå", venue=…)` in `web/tests/*.py`
- Test: `web/tests/test_models.py`, `web/tests/test_roster.py`

**Interfaces:**
- Produces: `Team.slug: str` (NOT NULL, unique). `Player` is unique on `(team_id, name)`. `roster.teams_for_parent(session, person_id: int) -> list[Team]` returns distinct teams ordered by `Team.name`, and `[]` for a person with no players.

- [ ] **Step 1: Give every existing test team a slug** (mechanical, so the suite keeps running once `slug` is required)

```bash
cd /home/daniele/Repos/360-mailer
sed -i 's/Team(name="F14 Blå", venue=/Team(name="F14 Blå", slug="f14-bla", venue=/' web/tests/*.py
grep -rn "Team(" web/tests | grep -v slug=
```
Expected: the `grep` prints nothing. If it prints lines, add a unique `slug=` to each one by hand.

- [ ] **Step 2: Write failing model tests** (append to `web/tests/test_models.py`)

```python
import pytest
from sqlalchemy.exc import IntegrityError

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Player, Team


def _new_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def _two_teams(session):
    f15 = Team(name="F15", slug="f15", venue="Wallenstam arena")
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    session.add_all([f15, p13])
    session.flush()
    return f15, p13


def test_two_teams_may_each_have_a_player_with_the_same_name():
    session = _new_session()
    f15, p13 = _two_teams(session)
    session.add_all([
        Player(name="Elsa Berg", team_id=f15.id),
        Player(name="Elsa Berg", team_id=p13.id),
    ])
    session.commit()

    assert session.query(Player).count() == 2


def test_a_team_cannot_have_two_players_with_the_same_name():
    session = _new_session()
    f15, _ = _two_teams(session)
    session.add_all([
        Player(name="Elsa Berg", team_id=f15.id),
        Player(name="Elsa Berg", team_id=f15.id),
    ])

    with pytest.raises(IntegrityError):
        session.commit()


def test_team_slugs_are_unique():
    session = _new_session()
    session.add_all([
        Team(name="F15", slug="f15", venue="Wallenstam arena"),
        Team(name="F15 igen", slug="f15", venue="Wallenstam arena"),
    ])

    with pytest.raises(IntegrityError):
        session.commit()
```
(If `test_models.py` already imports some of these names at the top, drop the duplicate imports.)

- [ ] **Step 3: Write failing `teams_for_parent` tests** (append to `web/tests/test_roster.py`, and add `teams_for_parent` to its `from duty_web.roster import (...)` list)

```python
def test_teams_for_parent_lists_each_of_their_teams_once(tmp_path):
    session = make_session()
    f15 = Team(name="F15", slug="f15", venue="Wallenstam arena")
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    f11 = Team(name="F11", slug="f11", venue="Wallenstam arena")
    session.add_all([f15, p13, f11])
    session.commit()
    # Two siblings on F15, one child on P13, nobody on F11.
    import_roster(session, write_csv(tmp_path, [
        ("Dan Edh", "dan@exempel.se", "Klara Edh"),
        ("Dan Edh", "dan@exempel.se", "Moa Edh"),
    ]), team_id=f15.id)
    import_roster(session, write_csv(tmp_path, [
        ("Dan Edh", "dan@exempel.se", "Elsa Edh"),
    ], name="p13.csv"), team_id=p13.id)
    dan = session.query(Person).filter_by(email="dan@exempel.se").one()

    assert [team.slug for team in teams_for_parent(session, dan.id)] == ["f15", "p13"]


def test_teams_for_parent_is_empty_for_someone_with_no_child_on_file():
    session = make_session()
    with_team(session)
    outsider = Person(name="Ola", email="ola@exempel.se")
    session.add(outsider)
    session.commit()

    assert teams_for_parent(session, outsider.id) == []
```

- [ ] **Step 4: Run them and watch them fail**

Run: `cd web && uv run pytest tests/test_models.py tests/test_roster.py -v`
Expected: FAIL. You'll see `TypeError: 'slug' is an invalid keyword argument for Team` and `ImportError: cannot import name 'teams_for_parent'`.

- [ ] **Step 5: Implement the models** in `web/src/duty_web/models.py`

Add `UniqueConstraint` to the sqlalchemy import:
```python
from sqlalchemy import Column, ForeignKey, Table, UniqueConstraint
```
In `Team`, between `name` and `venue`, add:
```python
    # Short, lowercase name used in URLs (?lag=f15), as the team's own
    # instruction folder under the documents dir, and in the just recipes.
    slug: Mapped[str] = mapped_column(unique=True)
```
In `Player`, replace `name: Mapped[str] = mapped_column(unique=True)` with:
```python
    # A name is only unique within a team: two teams in the club can each
    # have an Elsa Berg, and they are different children.
    __table_args__ = (UniqueConstraint("team_id", "name"),)

    name: Mapped[str]
```
Update the `Player` docstring's first line to say `A child on a team.` instead of `A child on the team.`

- [ ] **Step 6: Implement `teams_for_parent`** in `web/src/duty_web/roster.py`, right after `player_ids_for_parent`

```python
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
```

- [ ] **Step 7: Run the full web suite**

Run: `cd web && uv run pytest -q`
Expected: everything passes except the known baseline failure.

- [ ] **Step 8: Commit**

```bash
git add web/src/duty_web/models.py web/src/duty_web/roster.py web/tests
git commit -m "feat(web): lag får en slug och spelarnamn blir unika per lag" -m "Förberedelse för fler lag i samma klubb. Ett namn som Elsa Berg kan finnas i två lag och är då två olika barn, så namnet är bara unikt inom laget. Slugen är lagets korta namn i adresser, dokumentmappar och just-kommandon. teams_for_parent säger vilka lag en förälder har barn i – det är också vilka lag hen får se." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Imports touch only their own team

**Files:**
- Modify: `web/src/duty_web/roster.py` (`import_roster`, the `players_by_name` dict)
- Modify: `web/src/duty_web/seed.py` (`import_schedule`, the `session.query(Slot).delete()` line)
- Test: `web/tests/test_roster.py`, `web/tests/test_seed.py`

**Interfaces:**
- Consumes: `Team.slug` (Task 1).
- Produces: `import_roster(..., team_id=)` matches players only within `team_id`. `import_schedule(..., team_id=)` deletes and replaces only slots with that `team_id`. Signatures are unchanged.

- [ ] **Step 1: Failing roster test** (append to `web/tests/test_roster.py`)

```python
def test_a_child_with_the_same_name_on_another_team_is_another_player(tmp_path):
    session = make_session()
    f15 = Team(name="F15", slug="f15", venue="Wallenstam arena")
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    session.add_all([f15, p13])
    session.commit()

    import_roster(session, write_csv(tmp_path, [
        ("Hans Ahlqvist", "hans@exempel.se", "Klara Ahlqvist"),
    ]), team_id=f15.id)
    import_roster(session, write_csv(tmp_path, [
        ("Eva Berg", "eva@exempel.se", "Klara Ahlqvist"),
    ], name="p13.csv"), team_id=p13.id)

    klaras = (
        session.query(Player)
        .filter_by(name="Klara Ahlqvist")
        .order_by(Player.team_id)
        .all()
    )
    assert [p.team_id for p in klaras] == [f15.id, p13.id]
    assert [p.email for p in klaras[0].parents] == ["hans@exempel.se"]
    assert [p.email for p in klaras[1].parents] == ["eva@exempel.se"]
```

- [ ] **Step 2: Failing seed test** (append to `web/tests/test_seed.py`)

```python
def test_importing_one_teams_schedule_leaves_other_teams_slots_alone(tmp_path):
    session = make_session()
    f14 = seeded_team(session)
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    session.add(p13)
    session.flush()
    session.add(Player(name="Elsa Berg", team_id=p13.id))
    session.commit()

    import_schedule(session, write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Tova Exempel", None],
    ]), team_id=f14.id)
    import_schedule(session, write_xlsx(tmp_path, [
        [2026, "Kioskpass", "Mölnlycke idrottshall", "Kiosk", 3, "2026-01-17",
         "Lördag", "09:00-12:00", "Elsa Berg", None],
    ], name="p13.xlsx"), team_id=p13.id)

    assert sorted(slot.team_id for slot in session.query(Slot).all()) == sorted(
        [f14.id, p13.id]
    )
```

- [ ] **Step 3: Run and watch both fail**

Run: `cd web && uv run pytest tests/test_roster.py::test_a_child_with_the_same_name_on_another_team_is_another_player tests/test_seed.py::test_importing_one_teams_schedule_leaves_other_teams_slots_alone -v`
Expected: both FAIL. The roster test finds one Klara with both parents. The seed test is left with only the P13 slot.

- [ ] **Step 4: Scope the roster match** in `roster.py` `import_roster`. Replace:

```python
    players_by_name = {
        normalize_name(player.name): player for player in session.query(Player).all()
    }
```
with:
```python
    # Only this team's players: a child of the same name on another team is
    # a different child, and linking this roster's parents to them would
    # hand a stranger's family the other team's duties.
    players_by_name = {
        normalize_name(player.name): player
        for player in session.query(Player).filter(Player.team_id == team_id).all()
    }
```
Leave `people_by_email` global. A parent is one person across teams, and that is the whole point.

- [ ] **Step 5: Scope the schedule wipe** in `seed.py` `import_schedule`. Replace:

```python
    session.query(Slot).delete()
```
with:
```python
    # Replaces this team's season only; other teams' slots and swaps are
    # none of this file's business.
    session.query(Slot).filter(Slot.team_id == team_id).delete()
```

- [ ] **Step 6: Run the full web suite**

Run: `cd web && uv run pytest -q`
Expected: all pass except the baseline failure.

- [ ] **Step 7: Commit**

```bash
git add web/src/duty_web/roster.py web/src/duty_web/seed.py web/tests/test_roster.py web/tests/test_seed.py
git commit -m "fix(web): import av laglista och schema rör bara sitt eget lag" -m "Schemaimporten raderade alla pass i databasen innan den läste in filen. Med fler lag hade en import för det ena laget tömt det andras säsong, med alla byten. Laglistan matchade barn på namn över alla lag, så ett barn med samma namn i ett annat lag hade fått fel föräldrar kopplade till sig." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Creating teams and naming them in scripts and recipes

**Files:**
- Create: `web/src/duty_web/teams.py`
- Create: `scripts/remote_team_add.py`
- Modify: `scripts/remote_import.py`, `scripts/remote_roster_import.py`, `scripts/test_swap_fixture.py`
- Modify: `justfile`, `web/README.md`
- Test: `web/tests/test_teams.py`

**Interfaces:**
- Consumes: `Team.slug` (Task 1).
- Produces: `duty_web.teams.SLUG_PATTERN`, `TeamError(Exception)`, `validate_slug(slug: str) -> str`, `create_team(session, *, name: str, slug: str, venue: str) -> Team`, `team_by_slug(session, slug: str) -> Team` (raises `TeamError`).

- [ ] **Step 1: Failing tests** in `web/tests/test_teams.py`

```python
import pytest

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Team
from duty_web.teams import TeamError, create_team, team_by_slug, validate_slug


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def test_create_team_stores_name_slug_and_venue():
    session = make_session()

    team = create_team(session, name="  P13 ", slug="p13", venue="Mölnlycke idrottshall")

    assert (team.name, team.slug, team.venue) == ("P13", "p13", "Mölnlycke idrottshall")
    assert session.query(Team).count() == 1


@pytest.mark.parametrize("slug", ["", "F15", "f 15", "-f15", "f15/", "../x", "å13"])
def test_a_slug_that_isnt_folder_and_url_safe_is_refused(slug):
    with pytest.raises(TeamError):
        validate_slug(slug)


def test_a_second_team_with_the_same_slug_is_refused():
    session = make_session()
    create_team(session, name="F15", slug="f15", venue="Wallenstam arena")

    with pytest.raises(TeamError):
        create_team(session, name="F15 B", slug="f15", venue="Wallenstam arena")


def test_a_team_needs_a_name():
    with pytest.raises(TeamError):
        create_team(make_session(), name="  ", slug="f15", venue="Wallenstam arena")


def test_team_by_slug_names_the_known_teams_when_it_misses():
    session = make_session()
    create_team(session, name="F15", slug="f15", venue="Wallenstam arena")

    assert team_by_slug(session, "f15").name == "F15"
    with pytest.raises(TeamError, match="f15"):
        team_by_slug(session, "p13")
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd web && uv run pytest tests/test_teams.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'duty_web.teams'`.

- [ ] **Step 3: Implement** `web/src/duty_web/teams.py`

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `cd web && uv run pytest tests/test_teams.py -v`
Expected: PASS.

- [ ] **Step 5: Create** `scripts/remote_team_add.py`

```python
"""Create a team in the deployed app's database. Runs ON the Fly machine.

Uploaded and executed by `just team-add`; not meant to run locally.

    python /tmp/remote_team_add.py <slug> <namn> <arena>
"""

from __future__ import annotations

import sys

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.teams import TeamError, create_team

DB_PATH = "/data/duty.db"


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 1
    engine = make_engine(DB_PATH)
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        team = create_team(session, slug=argv[1], name=argv[2], venue=argv[3])
    except TeamError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Skapade {team.name} ({team.slug}), id {team.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 6: Make the push scripts take a slug.** In `scripts/remote_import.py`:
  - Update the docstring's usage to `python /tmp/remote_import.py <slug>`.
  - Add `from duty_web.teams import TeamError, team_by_slug`.
  - Drop `Team` from the models import.
  - Replace the `team = session.query(Team).first()` / `if team is None:` block with:

```python
    if len(sys.argv) != 2:
        print("Ange lagets slug, t.ex. f15.", file=sys.stderr)
        return 1
    try:
        team = team_by_slug(session, sys.argv[1])
    except TeamError as exc:
        print(str(exc), file=sys.stderr)
        return 1
```
  Then make the summary counts look at this team only, now that other teams' slots are in the same table:
```python
    team_slots = session.query(Slot).filter(Slot.team_id == team.id)
    dated = team_slots.filter(Slot.date.isnot(None)).count()
    unassigned = team_slots.filter(Slot.player_id.is_(None)).count()
```
  Read `scripts/remote_roster_import.py` first, then apply the same slug-argument replacement to its `session.query(Team).first()` block (lines ~30–34). In `scripts/test_swap_fixture.py` `main()`:
  - Accept `len(sys.argv) == 4` with `sys.argv[3]` as the slug.
  - Resolve the team with `team_by_slug`, catching `TeamError` the same way.
  - Update its docstring usage line.

- [ ] **Step 7: Smoke-check the scripts' argument handling**

Run: `cd web && uv run python ../scripts/remote_team_add.py; echo "exit=$?"`
Expected: the usage docstring prints, then `exit=1`. Nothing touches `/data`.

- [ ] **Step 8: Update the `justfile`.** Replace the `roster-push`, `schedule-push`, `docs-push`, `docs-list`, `test-fixture-add` and `test-fixture-remove` recipes, and add `team-add`, as follows:

```just
# Create a team in the deployed app (once per team). The slug names it in every other recipe
team-add slug name venue="Wallenstam arena":
    printf 'put scripts/remote_team_add.py /tmp/remote_team_add.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/remote_team_add.py {{slug}} '{{name}}' '{{venue}}'"

# Sync a team's players and parents from its 360Player contact list
# (additive — run this whenever a new parent registers, and before schedule-push)
roster-push team="f15" file="data/f15_parent_mailing_list.csv":
    printf 'put %s /tmp/roster.csv\nput scripts/remote_roster_import.py /tmp/remote_roster_import.py\n' "{{file}}" | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/remote_roster_import.py {{team}}"

# Import a built seed file into the deployed app (replaces that team's slots only)
schedule-push team="f15" file="data/seed_sasong_26_27.xlsx":
    printf 'put %s /tmp/seed.xlsx\nput scripts/remote_import.py /tmp/remote_import.py\n' "{{file}}" | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/remote_import.py {{team}}"

# Upload a document. No team: shared by the whole club. With a team slug: that team only,
# and it wins over a shared file of the same name for that team
docs-push file team="":
    fly ssh console -a duty-swap-webapp -C "mkdir -p /data/dokument/{{team}}"
    printf 'put %s /data/dokument/{{ if team == "" { "" } else { team + "/" } }}%s\n' "{{file}}" "$(basename {{file}})" | fly ssh sftp shell -a duty-swap-webapp

# List the documents on the deployed app's volume, shared and per team
docs-list:
    fly ssh console -a duty-swap-webapp -C "ls -laR /data/dokument"

# Add a throwaway test parent + pass to the deployed app (for trying swaps)
test-fixture-add email team="f15":
    printf 'put scripts/test_swap_fixture.py /tmp/test_swap_fixture.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/test_swap_fixture.py add {{email}} {{team}}"

# Remove the throwaway test parent, pass and any swap requests they are in
test-fixture-remove email team="f15":
    printf 'put scripts/test_swap_fixture.py /tmp/test_swap_fixture.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/test_swap_fixture.py remove {{email}} {{team}}"
```
Check the rendering without running anything remote:
Run: `just --dry-run docs-push x.pdf p13 && just --dry-run docs-push x.pdf && just --dry-run team-add p13 "P13 Vit"`
Expected: the `put` lines target `/data/dokument/p13/x.pdf` and `/data/dokument/x.pdf`, and the team-add name stays one quoted argument. If `fly ssh console -C` turns out not to honour the single quotes (that shows up on the first real `team-add`), switch to a slug-only name and fix it afterwards. Note this in the commit.

- [ ] **Step 9: Update `web/README.md`.**
  - Replace step 5 ("Skapa laget") with:

```markdown
5. **Skapa laget** (en gång per lag — inget annat skapar en `Team`-rad):

   ```bash
   just team-add f15 F15
   ```

   Slugen (`f15`) är lagets korta namn i adresser, dokumentmappen och i
   `just roster-push <slug>` / `just schedule-push <slug>`. Bara a–z, 0–9
   och bindestreck.
```
  - In every other README mention of `just roster-push` / `just schedule-push`, add the team slug as the first argument.
  - Add a short paragraph near the documents section saying that `just docs-push fil.pdf` is shared by the club, that `just docs-push fil.pdf p13` is only for P13, and that a team's own file wins over a shared file with the same name.

- [ ] **Step 10: Run the full suite and commit**

Run: `cd web && uv run pytest -q`. Expected: all pass except the baseline.

```bash
git add web/src/duty_web/teams.py web/tests/test_teams.py scripts/remote_team_add.py scripts/remote_import.py scripts/remote_roster_import.py scripts/test_swap_fixture.py justfile web/README.md
git commit -m "feat(web): skapa lag och peka ut laget vid import" -m "Importskripten tog första bästa lag i databasen. Med fler lag måste den som importerar säga vilket lag filen gäller, så recepten tar nu lagets slug. Ett nytt lag skapas med just team-add. Dokument kan laddas upp för hela klubben eller för ett lag." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Production migration (`scripts/migrate_multi_team.py`)

**Files:**
- Create: `scripts/migrate_multi_team.py`
- Modify: `justfile` (add `multi-team-migrate-push`)
- Test: `web/tests/test_migrate_multi_team.py`

**Interfaces:**
- Consumes: `Base.metadata` (the Task 1 models), `duty_web.teams.validate_slug` / `TeamError` (Task 3).
- Produces: `main(argv: list[str]) -> int`. Usage is `[script, db_path, slug]`. It returns 0 on success and 1 on a bad slug or a missing DB.

- [ ] **Step 1: Failing tests** in `web/tests/test_migrate_multi_team.py`. `OLD_SCHEMA` is the exact DDL that `create_all` produces on `main` before Task 1.

```python
"""The one-off migration to multi-team: team slugs, per-team player names.

It lives in scripts/ (it is pushed to the Fly machine), but it is duty_web
code, so it is tested from here where duty_web is importable.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from duty_web.db import make_engine, make_session_factory
from duty_web.models import Player, Slot, SwapRequest, Team

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_multi_team.py"

OLD_SCHEMA = """
CREATE TABLE people ( id INTEGER NOT NULL, name VARCHAR NOT NULL, email VARCHAR NOT NULL, email_notifications BOOLEAN NOT NULL, PRIMARY KEY (id), UNIQUE (email) );
CREATE TABLE teams ( id INTEGER NOT NULL, name VARCHAR NOT NULL, venue VARCHAR NOT NULL, PRIMARY KEY (id) );
CREATE TABLE players ( id INTEGER NOT NULL, name VARCHAR NOT NULL, team_id INTEGER NOT NULL, PRIMARY KEY (id), UNIQUE (name), FOREIGN KEY(team_id) REFERENCES teams (id) );
CREATE TABLE parent_players ( person_id INTEGER NOT NULL, player_id INTEGER NOT NULL, PRIMARY KEY (person_id, player_id), FOREIGN KEY(person_id) REFERENCES people (id), FOREIGN KEY(player_id) REFERENCES players (id) );
CREATE TABLE slots ( id INTEGER NOT NULL, team_id INTEGER NOT NULL, date DATE, start_time TIME NOT NULL, end_time TIME NOT NULL, station VARCHAR NOT NULL, duty_name VARCHAR NOT NULL, venue VARCHAR NOT NULL, child_name VARCHAR, note VARCHAR, player_id INTEGER, PRIMARY KEY (id), FOREIGN KEY(team_id) REFERENCES teams (id), FOREIGN KEY(player_id) REFERENCES players (id) );
CREATE TABLE swap_requests ( id INTEGER NOT NULL, proposer_slot_id INTEGER NOT NULL, target_slot_id INTEGER NOT NULL, status VARCHAR NOT NULL, created_at DATETIME NOT NULL, resolved_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(proposer_slot_id) REFERENCES slots (id), FOREIGN KEY(target_slot_id) REFERENCES slots (id) );
CREATE TABLE login_tokens ( id INTEGER NOT NULL, token_hash VARCHAR NOT NULL, email VARCHAR NOT NULL, expires_at DATETIME NOT NULL, consumed_at DATETIME, PRIMARY KEY (id), UNIQUE (token_hash) );
"""

OLD_ROWS = """
INSERT INTO teams (id, name, venue) VALUES (1, 'F15', 'Wallenstam arena');
INSERT INTO people (id, name, email, email_notifications) VALUES (1, 'Dan', 'dan@exempel.se', 1);
INSERT INTO people (id, name, email, email_notifications) VALUES (2, 'Bo', 'bo@exempel.se', 1);
INSERT INTO players (id, name, team_id) VALUES (7, 'Klara', 1);
INSERT INTO players (id, name, team_id) VALUES (8, 'Bea', 1);
INSERT INTO parent_players (person_id, player_id) VALUES (1, 7);
INSERT INTO parent_players (person_id, player_id) VALUES (2, 8);
INSERT INTO slots (id, team_id, date, start_time, end_time, station, duty_name, venue, child_name, player_id)
  VALUES (3, 1, '2026-11-01', '18:00:00.000000', '21:00:00.000000', 'Café', 'Cafépass', 'Wallenstam arena', 'Klara', 7);
INSERT INTO slots (id, team_id, date, start_time, end_time, station, duty_name, venue, child_name, player_id)
  VALUES (4, 1, '2026-11-08', '18:00:00.000000', '21:00:00.000000', 'Café', 'Cafépass', 'Wallenstam arena', 'Bea', 8);
INSERT INTO swap_requests (id, proposer_slot_id, target_slot_id, status, created_at)
  VALUES (1, 3, 4, 'pending', '2026-10-01 10:00:00.000000');
"""


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("migrate_multi_team", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def old_db(tmp_path):
    path = tmp_path / "duty.db"
    connection = sqlite3.connect(path)
    connection.executescript(OLD_SCHEMA + OLD_ROWS)
    connection.commit()
    connection.close()
    return path


def _session(path):
    return make_session_factory(make_engine(str(path)))()


def test_the_existing_team_gets_the_given_slug(migration, old_db):
    assert migration.main(["x", str(old_db), "f15"]) == 0

    assert _session(old_db).get(Team, 1).slug == "f15"


def test_ids_parents_slots_and_pending_swaps_survive(migration, old_db):
    migration.main(["x", str(old_db), "f15"])
    session = _session(old_db)

    assert [p.email for p in session.get(Player, 7).parents] == ["dan@exempel.se"]
    assert session.get(Slot, 3).player_id == 7
    assert session.get(SwapRequest, 1).target_slot_id == 4


def test_afterwards_another_team_may_have_a_child_of_the_same_name(migration, old_db):
    migration.main(["x", str(old_db), "f15"])
    session = _session(old_db)
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    session.add(p13)
    session.flush()
    session.add(Player(name="Klara", team_id=p13.id))
    session.commit()

    assert session.query(Player).filter_by(name="Klara").count() == 2


def test_running_it_again_changes_nothing(migration, old_db, capsys):
    migration.main(["x", str(old_db), "f15"])
    capsys.readouterr()

    assert migration.main(["x", str(old_db), "f15"]) == 0

    out = capsys.readouterr().out
    assert "fanns redan" in out
    assert "ombyggd" not in out
    assert _session(old_db).get(Player, 8).parents[0].email == "bo@exempel.se"


def test_a_bad_slug_is_refused_before_anything_changes(migration, old_db):
    assert migration.main(["x", str(old_db), "F 15"]) == 1

    connection = sqlite3.connect(old_db)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(teams)")}
    connection.close()
    assert "slug" not in columns
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd web && uv run pytest tests/test_migrate_multi_team.py -v`
Expected: errors, because `scripts/migrate_multi_team.py` doesn't exist yet (FileNotFoundError in the `migration` fixture).

- [ ] **Step 3: Implement** `scripts/migrate_multi_team.py`

```python
"""Give teams a slug and make player names unique per team. Idempotent.

Before: teams had no short name, and players.name was unique across the
whole database, so a child on a second team couldn't share a name with one
on the first. After: teams.slug is NOT NULL and UNIQUE, and players are
unique per (team_id, name).

Runs against a database file, either on the Fly machine (via
`just multi-team-migrate-push`) or on a downloaded copy for a rehearsal:

    python scripts/migrate_multi_team.py [db_path] [slug]

slug goes to the one team that exists before this migration (F15 → f15).
Safe to re-run: each step checks whether it has already been applied.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from sqlalchemy.schema import CreateTable

from duty_web.db import make_engine
from duty_web.models import Base
from duty_web.teams import TeamError, validate_slug

DEFAULT_DB_PATH = "/data/duty.db"
DEFAULT_SLUG = "f15"


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _unique_column_sets(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    """The column lists of every UNIQUE constraint/index on a table."""
    sets = set()
    for _, index_name, unique, *_ in connection.execute(f"PRAGMA index_list({table})"):
        if unique:
            sets.add(tuple(
                row[2] for row in connection.execute(f"PRAGMA index_info('{index_name}')")
            ))
    return sets


def _add_team_slug(connection: sqlite3.Connection, slug: str) -> bool:
    if "slug" in _columns(connection, "teams"):
        return False
    (count,) = connection.execute("SELECT COUNT(*) FROM teams").fetchone()
    if count > 1:
        raise SystemExit("Fler än en grupp saknar slug — sätt dem för hand.")
    connection.execute("ALTER TABLE teams ADD COLUMN slug VARCHAR")
    connection.execute("UPDATE teams SET slug = ?", (slug,))
    return True


def _rebuild(connection: sqlite3.Connection, engine, table: str) -> None:
    """Recreate a table from its model's DDL, keeping every row and id.

    SQLite can't add or drop a constraint in place, so this is the
    documented create-new/copy/swap recipe (as in
    migrate_player_ownership.py). The DDL comes from the model, so the
    result can't drift from what create_all makes on a fresh database.
    Foreign keys are off for the swap: other tables reference this one by
    id, and the ids are kept, which foreign_key_check confirms afterwards.
    """
    model_table = Base.metadata.tables[table]
    ddl = str(CreateTable(model_table).compile(engine)).strip()
    prefix = f"CREATE TABLE {table} ("
    if not ddl.startswith(prefix):
        raise SystemExit(f"Oväntad DDL för {table}: {ddl[:60]}")
    ddl = ddl.replace(prefix, f"CREATE TABLE {table}_new (", 1)
    columns = ", ".join(column.name for column in model_table.columns)

    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN")
    try:
        connection.execute(ddl)
        connection.execute(
            f"INSERT INTO {table}_new ({columns}) SELECT {columns} FROM {table}"
        )
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise SystemExit(f"Trasiga referenser efter ombyggnad av {table}: {violations}")
    connection.execute("PRAGMA foreign_keys=ON")


def main(argv: list[str]) -> int:
    db_path = argv[1] if len(argv) > 1 else DEFAULT_DB_PATH
    slug = argv[2] if len(argv) > 2 else DEFAULT_SLUG

    try:
        validate_slug(slug)
    except TeamError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not Path(db_path).is_file():
        print(f"Databasen hittades inte: {db_path}", file=sys.stderr)
        return 1

    engine = make_engine(db_path)
    connection = sqlite3.connect(db_path, isolation_level=None)
    try:
        added = _add_team_slug(connection, slug)
        print(f"teams.slug: {'tillagd' if added else 'fanns redan'}")

        if ("slug",) in _unique_column_sets(connection, "teams"):
            print("teams: unik slug fanns redan")
        else:
            _rebuild(connection, engine, "teams")
            print("teams: ombyggd med unik slug")

        if ("team_id", "name") in _unique_column_sets(connection, "players"):
            print("players: namn per lag fanns redan")
        else:
            _rebuild(connection, engine, "players")
            print("players: ombyggd, namn unika per lag")
    finally:
        connection.close()

    print("Klart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run the tests**

Run: `cd web && uv run pytest tests/test_migrate_multi_team.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Add the recipe** to the `justfile`, next to `migrate-push`

```just
# One-off: team slugs and per-team player names (idempotent). Run right after the deploy that adds them
multi-team-migrate-push slug="f15":
    printf 'put scripts/migrate_multi_team.py /tmp/migrate_multi_team.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/migrate_multi_team.py /data/duty.db {{slug}}"
```

- [ ] **Step 6: Rehearse against a copy of production** (only if `fly` is authenticated; otherwise skip and say so in the report)

Run: `just db-pull /tmp/duty-copy.db && cd web && uv run python ../scripts/migrate_multi_team.py /tmp/duty-copy.db f15 && uv run python ../scripts/migrate_multi_team.py /tmp/duty-copy.db f15`
Expected: the first run prints `tillagd` / `ombyggd`. The second prints only `fanns redan` lines.

- [ ] **Step 7: Commit**

```bash
git add scripts/migrate_multi_team.py web/tests/test_migrate_multi_team.py justfile
git commit -m "feat(web): migrering till flera lag" -m "Den driftsatta databasen saknar lagets slug och har spelarnamn som är unika över hela databasen. Migreringen ger det befintliga laget en slug och bygger om tabellerna med rätt villkor, med samma id:n, så kopplingar till föräldrar, pass och väntande byten följer med. Den kan köras om utan effekt." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Swaps stay inside a team

**Files:**
- Modify: `web/src/duty_web/schedule_queries.py` (add `swap_candidates_by_slot`)
- Modify: `web/src/duty_web/swaps.py` (`propose_swap` guard)
- Modify: `web/src/duty_web/routes/schedule_routes.py` (`mine()`)
- Test: `web/tests/test_schedule_queries.py`, `web/tests/test_swaps.py`

**Interfaces:**
- Consumes: `Team.slug` (Task 1).
- Produces: `schedule_queries.swap_candidates_by_slot(session, person_id: int, slots: list[Slot], *, on_or_after: date) -> dict[int, list[Slot]]`. `propose_swap` raises `SwapError("Du kan bara byta pass inom samma lag.")` when the teams differ.

- [ ] **Step 1: Failing query test** (append to `web/tests/test_schedule_queries.py`, and add `swap_candidates_by_slot` to its import list)

```python
def test_swap_candidates_stay_inside_each_slots_own_team():
    session = make_session()
    f15 = Team(name="F15", slug="f15", venue="Wallenstam arena")
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    session.add_all([f15, p13])
    session.flush()
    klara = _family(session, f15, "Klara", "dan@exempel.se")
    dan = klara.parents[0]
    elsa = Player(name="Elsa", team_id=p13.id)
    elsa.parents = [dan]
    session.add(elsa)
    moa = _family(session, f15, "Moa", "moa@exempel.se")
    nils = _family(session, p13, "Nils", "nils@exempel.se")
    session.flush()
    klara_slot = _slot(f15.id, klara.id, 10)
    elsa_slot = _slot(p13.id, elsa.id, 11)
    moa_slot = _slot(f15.id, moa.id, 20)
    nils_slot = _slot(p13.id, nils.id, 21)
    session.add_all([klara_slot, elsa_slot, moa_slot, nils_slot])
    session.commit()

    result = swap_candidates_by_slot(
        session, dan.id, [klara_slot, elsa_slot], on_or_after=date(2026, 1, 1)
    )

    assert [s.id for s in result[klara_slot.id]] == [moa_slot.id]
    assert [s.id for s in result[elsa_slot.id]] == [nils_slot.id]
```

- [ ] **Step 2: Failing guard test** (append to `web/tests/test_swaps.py`, and change its top import to `from datetime import date, datetime, time`)

```python
def test_a_swap_across_teams_is_refused():
    engine = make_engine(":memory:")
    init_db(engine)
    session = make_session_factory(engine)()
    f15 = Team(name="F15", slug="f15", venue="Wallenstam arena")
    p13 = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    session.add_all([f15, p13])
    session.flush()
    klara = _family(session, f15, "Klara", "a@exempel.se")
    nils = _family(session, p13, "Nils", "b@exempel.se")

    def slot(team, player):
        return Slot(
            team_id=team.id, date=date(2026, 11, 1), start_time=time(18, 0),
            end_time=time(21, 0), station="Café", duty_name="Cafépass",
            venue="Wallenstam arena", player_id=player.id,
        )

    mine, theirs = slot(f15, klara), slot(p13, nils)
    session.add_all([mine, theirs])
    session.commit()

    with pytest.raises(SwapError, match="samma lag"):
        propose_swap(
            session, proposer_slot_id=mine.id, target_slot_id=theirs.id,
            proposer_person_id=klara.parents[0].id, now=datetime(2026, 10, 19, 12),
        )
    assert session.query(SwapRequest).count() == 0
```

- [ ] **Step 3: Run and watch them fail**

Run: `cd web && uv run pytest tests/test_schedule_queries.py tests/test_swaps.py -v`
Expected: an ImportError for `swap_candidates_by_slot`. The swap test fails with "DID NOT RAISE".

- [ ] **Step 4: Implement the query** in `schedule_queries.py`, after `swap_candidates_for_slot`

```python
def swap_candidates_by_slot(
    session: Session, person_id: int, slots: list[Slot], *, on_or_after: date
) -> dict[int, list[Slot]]:
    """For each of a parent's slots, the slots it could be traded for.

    Trades never cross teams, so each slot is offered only its own team's
    slots — a parent with children on two teams holds slots on both, and
    must not be offered the other team's shifts for either.
    """
    by_team: dict[int, list[Slot]] = {}
    result = {}
    for slot in slots:
        if slot.team_id not in by_team:
            by_team[slot.team_id] = swappable_slots_for(
                session, slot.team_id, person_id, on_or_after=on_or_after
            )
        result[slot.id] = swap_candidates_for_slot(slot, by_team[slot.team_id])
    return result
```

- [ ] **Step 5: Implement the guard** in `swaps.py` `propose_swap`, directly after the `if target_slot.player_id is None:` check

```python
    # Teams never trade with each other. A parent with children on two
    # teams holds slots on both, but each slot stays inside its own team.
    if target_slot.team_id != proposer_slot.team_id:
        raise SwapError("Du kan bara byta pass inom samma lag.")
```

- [ ] **Step 6: Use it in `mine()`** (`schedule_routes.py`). Replace these lines:

```python
    team_id = _team_id_for(session, current_user.person)
    candidates = swappable_slots_for(session, team_id, int(current_user.id), on_or_after=today)

    candidates_by_slot = {
        slot.id: swap_candidates_for_slot(slot, candidates) for slot in slots
    }
```
with:
```python
    candidates_by_slot = swap_candidates_by_slot(
        session, int(current_user.id), slots, on_or_after=today
    )
```
In the `from ..schedule_queries import (...)` list, replace `swap_candidates_for_slot` and `swappable_slots_for` with `swap_candidates_by_slot`. `_team_id_for` is still used by `team()` until Task 6, so leave it for now.

- [ ] **Step 7: Run the full suite and commit**

Run: `cd web && uv run pytest -q`. Expected: all pass except the baseline.

```bash
git add web/src/duty_web/schedule_queries.py web/src/duty_web/swaps.py web/src/duty_web/routes/schedule_routes.py web/tests/test_schedule_queries.py web/tests/test_swaps.py
git commit -m "feat(web): byten sker bara inom ett lag" -m "Bytesförslagen hämtades från ett enda lag, det första barnets. En förälder med barn i två lag hade fått det ena lagets pass som förslag för ett pass i det andra. Nu får varje pass förslag från sitt eget lag, och ett försök att byta mellan lag avvisas också på servern." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: "Hela laget" per team

**Files:**
- Modify: `web/tests/conftest.py` (add the `second_team` fixture)
- Modify: `web/src/duty_web/routes/schedule_routes.py` (`team()`, and remove `_team_id_for`)
- Modify: `web/src/duty_web/templates/team.html`
- Create: `web/src/duty_web/templates/team_empty.html`
- Modify: `web/src/duty_web/static/style.css`
- Modify: `web/src/duty_web/routes/auth_routes.py` (comment)
- Test: `web/tests/test_schedule_routes.py`

**Interfaces:**
- Consumes: `teams_for_parent` (Task 1).
- Produces: `GET /team?lag=<slug>&year=&month=`. It shows the parent's first team (by name) by default, returns 404 for a team they have no child on, and renders `team_empty.html` when they have no team. The `second_team` fixture returns `{"team", "sibling", "sibling_slot", "stranger", "stranger_slot", "neighbour_slot"}`. Tasks 7 and 8 use it too.

- [ ] **Step 1: Add the fixture** to `web/tests/conftest.py` (after `seeded`)

```python
@pytest.fixture
def second_team(session_factory, seeded):
    """A second team in the same club, P13, on the same future day.

    The seeded first parent (only them, not their partner) has a child here
    too, as a parent with children on two teams would. Nora's family is on
    P13 only, and Maja's is a neighbour on the seeded team, so both teams
    have someone to trade with.
    """
    from datetime import time

    session = session_factory()
    team = Team(name="P13", slug="p13", venue="Mölnlycke idrottshall")
    session.add(team)
    session.flush()
    sibling = Player(name="Elsa Exempel", team_id=team.id)
    sibling.parents = [session.get(Person, seeded["person"].id)]
    session.add(sibling)
    stranger = make_family(
        session, team, "Nils Främling", [("Nora Främling", "nora@exempel.se")]
    )
    neighbour = make_family(
        session, session.get(Team, seeded["team"].id), "Moa Granne",
        [("Maja Granne", "maja@exempel.se")],
    )
    day = seeded["future_slot"].date

    def slot(team_id, player, start, duty_name, venue):
        return Slot(
            team_id=team_id, date=day, start_time=time(start, 0),
            end_time=time(start + 2, 0), station="Kiosk", duty_name=duty_name,
            venue=venue, child_name=player.name, player_id=player.id,
        )

    sibling_slot = slot(team.id, sibling, 9, "Kioskpass", "Mölnlycke idrottshall")
    stranger_slot = slot(team.id, stranger, 13, "Kioskpass", "Mölnlycke idrottshall")
    neighbour_slot = slot(
        seeded["team"].id, neighbour, 13, "Arena värdskap", "Wallenstam arena"
    )
    session.add_all([sibling_slot, stranger_slot, neighbour_slot])
    session.commit()
    return {
        "team": team,
        "sibling": sibling,
        "sibling_slot": sibling_slot,
        "stranger": stranger,
        "stranger_slot": stranger_slot,
        "neighbour_slot": neighbour_slot,
    }
```

- [ ] **Step 2: Failing route tests** (append to `web/tests/test_schedule_routes.py`, adding `from duty_web.models import Person` if it isn't imported yet)

```python
def _month(seeded):
    day = seeded["future_slot"].date
    return f"year={day.year}&month={day.month}"


def test_team_page_shows_the_parents_first_team_by_default(client, seeded, second_team):
    _login(client, seeded["person"])

    body = client.get(f"/team?{_month(seeded)}").get_data(as_text=True)

    assert "Arena värdskap" in body
    assert "Kioskpass" not in body


def test_a_parent_on_two_teams_can_switch_to_the_other(client, seeded, second_team):
    _login(client, seeded["person"])

    body = client.get(f"/team?lag=p13&{_month(seeded)}").get_data(as_text=True)

    assert "Kioskpass" in body
    assert "Arena värdskap" not in body
    assert "lag=f14-bla" in body  # the switcher leads back
    assert body.count("lag=p13") >= 2  # month navigation stays on P13


def test_another_teams_calendar_is_not_found(client, seeded, second_team):
    _login(client, seeded["other_parent"])

    assert client.get("/team?lag=p13").status_code == 404


def test_a_parent_on_one_team_gets_no_team_switcher(client, seeded, second_team):
    _login(client, seeded["other_parent"])

    body = client.get(f"/team?{_month(seeded)}").get_data(as_text=True)

    assert "team-switch" not in body


def test_someone_with_no_child_on_any_team_sees_no_calendar(
    client, seeded, session_factory
):
    session = session_factory()
    outsider = Person(name="Ola", email="ola@exempel.se")
    session.add(outsider)
    session.commit()
    _login(client, outsider)

    response = client.get(f"/team?{_month(seeded)}")

    assert response.status_code == 200
    assert "Arena värdskap" not in response.get_data(as_text=True)
```

- [ ] **Step 3: Run and watch them fail**

Run: `cd web && uv run pytest tests/test_schedule_routes.py -v`
Expected: the switch, 404 and no-team tests FAIL. The no-team case currently falls back to the first team and shows "Arena värdskap".

- [ ] **Step 4: Implement `team()`.** In `schedule_routes.py`:
  - Add `abort` to the flask import.
  - Add `teams_for_parent` to the `..roster` import.
  - Delete `_team_id_for` entirely.
  - Drop `Player, Team` from the models import if nothing else uses them (`Slot` stays).
  - Replace the start of `team()`, up to and including `undated_slots = undated_slots_for_team(session, team_id)`, with:

```python
@schedule_bp.route("/team")
@login_required
def team():
    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)
    session = _session()

    # A parent sees the teams they have a child on, and only those. Someone
    # with no child on any team sees no calendar at all, rather than the
    # first team's.
    teams = teams_for_parent(session, int(current_user.id))
    if not teams:
        return render_template("team_empty.html")
    slug = request.args.get("lag")
    if slug is None:
        current_team = teams[0]
    else:
        current_team = next((t for t in teams if t.slug == slug), None)
        if current_team is None:
            # Not found rather than forbidden: whether another team exists
            # is none of this parent's business.
            abort(404)

    slots = slots_for_team_month(session, current_team.id, year, month)
    undated_slots = undated_slots_for_team(session, current_team.id)
```
  Then add `team=current_team, teams=teams,` to the `render_template("team.html", ...)` call.

- [ ] **Step 5: Templates.** In `team.html`, change both month-nav `url_for` calls to include the team:

```html
<a href="{{ url_for('schedule.team', lag=team.slug, year=prev_year, month=prev_month) }}" aria-label="Föregående månad">‹</a>
```
```html
<a href="{{ url_for('schedule.team', lag=team.slug, year=next_year, month=next_month) }}" aria-label="Nästa månad">›</a>
```
Directly after the closing `</div>` of `.section-head`, add:
```html
{% if teams|length > 1 %}
<nav class="team-switch" aria-label="Lag">
  {% for t in teams %}
    <a class="{{ 'active' if t.id == team.id }}" href="{{ url_for('schedule.team', lag=t.slug, year=year, month=month) }}">{{ t.name }}</a>
  {% endfor %}
</nav>
{% endif %}
```
Create `team_empty.html`:
```html
{% extends "base.html" %}
{% block title %}Caféschema — Hela laget{% endblock %}
{% block content %}
<div class="section-head">
  <h2>Hela laget</h2>
</div>
<p class="empty-state">Du har inget barn i något lag här ännu.</p>
{% endblock %}
```
Append to `style.css`:
```css
.team-switch { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 12px; }
.team-switch a { font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 999px; background: var(--surface-2); color: var(--ink-dim); text-decoration: none; }
.team-switch a.active { background: var(--ink); color: var(--surface); }
```

- [ ] **Step 6: Fix the stale comment** in `auth_routes.py` (around line 78). Replace `and, via /team's fallback to the first Team, read the whole roster and season calendar.` with `and could then reach every page behind login.` (/team no longer falls back to the first team.)

- [ ] **Step 7: Run the full suite and look at it**

Run: `cd web && uv run pytest -q`. Expected: all pass except the baseline.
Then `just web-run`, log in locally if you have a dev path, or rely on the tests, and check that the switcher sits under the heading at 375px width without overflowing.

- [ ] **Step 8: Commit**

```bash
git add web/tests/conftest.py web/tests/test_schedule_routes.py web/src/duty_web/routes/schedule_routes.py web/src/duty_web/routes/auth_routes.py web/src/duty_web/templates/team.html web/src/duty_web/templates/team_empty.html web/src/duty_web/static/style.css
git commit -m "feat(web): Hela laget visar ett lag i taget, bara föräldrarnas egna" -m "Den som har barn i två lag kan växla mellan lagen. Ett lag man inte har barn i går inte att öppna, och den som inte har något barn i något lag ser inget schema alls, i stället för det första lagets." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Shared and team-specific instructions

**Files:**
- Modify: `web/src/duty_web/routes/document_routes.py`
- Modify: `web/src/duty_web/routes/schedule_routes.py` (`api_schedule` `document_url`)
- Modify: `web/src/duty_web/app.py` (filter registration)
- Modify: `web/src/duty_web/templates/mine.html` (line ~49), `web/src/duty_web/templates/dokument.html`
- Modify: `web/src/duty_web/static/style.css`, `web/src/duty_web/duties.py` (docstring)
- Test: `web/tests/test_document_routes.py`, `web/tests/test_api_schedule.py`

**Interfaces:**
- Consumes: `teams_for_parent` (Task 1) and the `second_team` fixture (Task 6).
- Produces:
  - `instruction_url_for_duty(duty_name, team_slug: str | None = None, *, external=False) -> str | None`
  - `document_url_for_duty(duty_name, team_slug: str | None = None, *, external=False) -> str`
  - `document_url_for_slot(slot) -> str`, used as the Jinja filter `slot_document_url`, which replaces `duty_document_url`
  - route `documents.team_download` at `/dokument/<slug>/<filename>`
  - `/dokument/<filename>` no longer accepts slashes

- [ ] **Step 1: Failing tests** (append to `web/tests/test_document_routes.py`)

```python
def _login(client, person):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True


def _team_file(documents_dir, slug, name, content=b"%PDF-1.4 team"):
    folder = documents_dir / slug
    folder.mkdir(exist_ok=True)
    (folder / name).write_bytes(content)


def test_a_teams_own_instruction_wins_over_the_shared_one(app, documents_dir):
    from duty_web.routes.document_routes import document_url_for_duty

    _team_file(documents_dir, "p13", "Cafeteria_Instruktion_MIH.pdf")

    with app.test_request_context():
        assert document_url_for_duty("Bästkustcupen", "p13") == (
            "/dokument/p13/Cafeteria_Instruktion_MIH.pdf"
        )
        assert document_url_for_duty("Bästkustcupen", "f14-bla") == (
            "/dokument/Cafeteria_Instruktion_MIH.pdf"
        )


def test_a_team_without_its_own_copy_uses_the_shared_instruction(app):
    from duty_web.routes.document_routes import document_url_for_duty

    with app.test_request_context():
        assert document_url_for_duty("Cafépass", "p13") == (
            "/dokument/Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf"
        )


def test_an_instruction_only_one_team_has_is_not_offered_to_the_others(
    app, documents_dir
):
    from duty_web.routes.document_routes import document_url_for_duty

    (documents_dir / "Cafeteria_Instruktion_MIH.pdf").unlink()
    _team_file(documents_dir, "p13", "Cafeteria_Instruktion_MIH.pdf")

    with app.test_request_context():
        assert document_url_for_duty("Bästkustcupen", "p13").startswith("/dokument/p13/")
        assert document_url_for_duty("Bästkustcupen", "f14-bla") == "/dokument"


def test_a_parent_can_download_their_teams_document(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Kiosk_P13.pdf")
    _login(client, seeded["person"])

    assert client.get("/dokument/p13/Kiosk_P13.pdf").status_code == 200


def test_another_teams_document_is_not_found_by_any_route(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Kiosk_P13.pdf")
    _login(client, seeded["other_parent"])  # F14 Blå only

    for url in (
        "/dokument/p13/Kiosk_P13.pdf",
        "/dokument/p13%2FKiosk_P13.pdf",
        "/dokument/f14-bla/../p13/Kiosk_P13.pdf",
    ):
        assert client.get(url).status_code in (403, 404), url


def test_document_list_groups_team_and_shared_documents(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Kiosk_P13.pdf")
    _login(client, seeded["person"])

    body = client.get("/dokument").get_data(as_text=True)

    assert "Kiosk P13" in body
    assert "/dokument/p13/Kiosk_P13.pdf" in body
    assert "Cafeteria Instruktion MIH" in body
    assert "Gemensamma" in body


def test_other_teams_documents_are_not_listed(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Kiosk_P13.pdf")
    _login(client, seeded["other_parent"])

    assert "Kiosk P13" not in client.get("/dokument").get_data(as_text=True)


def test_a_shared_document_your_only_team_replaces_is_listed_once(
    client, seeded, second_team, documents_dir, session_factory
):
    from duty_web.models import Person

    _team_file(documents_dir, "p13", "Cafeteria_Instruktion_MIH.pdf")
    nora = session_factory().query(Person).filter_by(email="nora@exempel.se").one()
    _login(client, nora)  # P13 only

    body = client.get("/dokument").get_data(as_text=True)

    assert body.count("Cafeteria Instruktion MIH") == 1


def test_a_shared_document_only_one_of_your_teams_replaces_stays_listed(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Cafeteria_Instruktion_MIH.pdf")
    _login(client, seeded["person"])  # F14 Blå (uses shared) and P13 (own copy)

    body = client.get("/dokument").get_data(as_text=True)

    assert body.count("Cafeteria Instruktion MIH") == 2
```
And in `web/tests/test_api_schedule.py`:
```python
def test_api_links_a_teams_shift_to_that_teams_own_instruction(
    client, seeded, second_team, session_factory, documents_dir
):
    from datetime import time

    from duty_web.models import Slot

    (documents_dir / "p13").mkdir()
    (documents_dir / "p13" / "Cafeteria_Instruktion_MIH.pdf").write_bytes(b"%PDF")
    session = session_factory()
    session.add(Slot(
        team_id=second_team["team"].id, date=seeded["future_slot"].date,
        start_time=time(15, 0), end_time=time(17, 0), station="Café",
        duty_name="Bästkustcupen", venue="Mölnlycke idrottshall",
        child_name="Nils Främling", player_id=second_team["stranger"].id,
    ))
    session.commit()

    rows = client.get("/api/schedule", headers={"X-Api-Key": "test-api-key"}).get_json()

    cup = next(r for r in rows if r["duty_name"] == "Bästkustcupen")
    assert cup["document_url"].endswith("/dokument/p13/Cafeteria_Instruktion_MIH.pdf")
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd web && uv run pytest tests/test_document_routes.py tests/test_api_schedule.py -v`
Expected: the new tests FAIL with TypeError (an unexpected positional `team_slug`) or a 404 on the team route. Existing tests pass.

- [ ] **Step 3: Implement in `document_routes.py`.**
  - Update the module docstring with a paragraph:

```
Two levels of documents: files at the root of the documents folder are
shared by the whole club; files in <documents_dir>/<team slug>/ are that
team's own, visible only to parents with a child on it. A duty's
instruction is looked up in the slot's team folder first, so a team can
keep its own version of a shared instruction under the same name.
```
  - Change the imports:

```python
from flask_login import current_user, login_required

from ..duties import profile_for
from ..roster import teams_for_parent
from ..session_scope import get_session as _session
```
  - Replace `_documents`, `instruction_url_for_duty` and `document_url_for_duty` with:

```python
def _documents(directory: Path, slug: str | None = None) -> list[dict]:
    if not directory.is_dir():
        return []
    return [
        {
            "name": path.stem.replace("_", " "),
            "filename": path.name,
            "suffix": path.suffix.lstrip(".").upper(),
            "size_kb": max(1, round(path.stat().st_size / 1024)),
            "url": (
                url_for("documents.team_download", slug=slug, filename=path.name)
                if slug
                else url_for("documents.download", filename=path.name)
            ),
        }
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
    ]


def instruction_url_for_duty(
    duty_name: str | None, team_slug: str | None = None, *, external: bool = False
) -> str | None:
    """This duty's own instruction, or None if there isn't one to link to.

    The team's own copy wins over the shared one. None also covers "mapped,
    but not uploaded yet": a link to a file the volume doesn't have would
    404 on the morning of the shift.
    """
    profile = profile_for(duty_name)
    if profile is None or not profile.document:
        return None
    if team_slug and (_documents_dir() / team_slug / profile.document).is_file():
        return url_for(
            "documents.team_download", slug=team_slug, filename=profile.document,
            _external=external,
        )
    if (_documents_dir() / profile.document).is_file():
        return url_for(
            "documents.download", filename=profile.document, _external=external
        )
    return None


def document_url_for_duty(
    duty_name: str | None, team_slug: str | None = None, *, external: bool = False
) -> str:
    """Where Att göra's "Läs instruktionerna" goes — the index as a fallback,
    so the button always leads somewhere readable."""
    return instruction_url_for_duty(duty_name, team_slug, external=external) or url_for(
        "documents.dokument", _external=external
    )


def document_url_for_slot(slot) -> str:
    return document_url_for_duty(slot.duty_name, slot.team.slug)
```
  - Replace the `dokument` view and the routes:

```python
@document_bp.route("/dokument")
@login_required
def dokument():
    root = _documents_dir()
    teams = teams_for_parent(_session(), int(current_user.id))
    per_team = [(team, _documents(root / team.slug, team.slug)) for team in teams]
    # A shared document that every one of your teams has its own copy of is
    # never the one you need, so it isn't listed. One that only some of them
    # replace still is: the others use it.
    replaced = (
        set.intersection(*({d["filename"] for d in docs} for _, docs in per_team))
        if per_team
        else set()
    )
    shared = [d for d in _documents(root) if d["filename"] not in replaced]
    team_sections = [
        {"team": team, "documents": docs} for team, docs in per_team if docs
    ]
    total = len(shared) + sum(len(s["documents"]) for s in team_sections)
    return render_template(
        "dokument.html", shared=shared, team_sections=team_sections, total=total
    )


# <filename>, not <path:filename>: a slash here would reach into a team's
# folder without the membership check below.
@document_bp.route("/dokument/<filename>")
@login_required
def download(filename: str):
    # send_from_directory refuses to escape the directory, but the allowlist
    # also keeps the response to types we intend to hand out.
    if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        abort(404)
    return send_from_directory(_documents_dir(), filename)


@document_bp.route("/dokument/<slug>/<filename>")
@login_required
def team_download(slug: str, filename: str):
    slugs = {team.slug for team in teams_for_parent(_session(), int(current_user.id))}
    # Not found, like any other team's page: whether the file exists is
    # none of a non-member's business.
    if slug not in slugs or Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        abort(404)
    return send_from_directory(_documents_dir() / slug, filename)
```

- [ ] **Step 4: Wire the callers.**
  - In `app.py`, replace the filter import and registration:

```python
    from .routes.document_routes import document_url_for_slot

    app.jinja_env.filters["slot_document_url"] = document_url_for_slot
```
  - In `mine.html`, change `href="{{ slot.duty_name|duty_document_url }}"` to `href="{{ slot|slot_document_url }}"`. Check `grep -rn duty_document_url web/src`: it should return nothing.
  - In `api_schedule`, change the `document_url` line to:

```python
            "document_url": instruction_url_for_duty(
                slot.duty_name, slot.team.slug, external=True
            ),
```

- [ ] **Step 5: Template `dokument.html`.** Replace its body with:

```html
{% extends "base.html" %}
{% block title %}Caféschema — Dokument{% endblock %}
{% macro doc_row(document) %}
  <a class="doc-row" href="{{ document.url }}">
    <span class="doc-kind">{{ document.suffix }}</span>
    <span class="doc-text">
      <span class="doc-name">{{ document.name }}</span>
      <span class="doc-meta">{{ document.size_kb }} kB</span>
    </span>
    <span class="doc-arrow" aria-hidden="true">→</span>
  </a>
{% endmacro %}
{% block content %}
<div class="section-head">
  <h2>Dokument</h2>
  <span class="count">{{ total }} st</span>
</div>

<p class="empty-note">Instruktioner och annat som är bra att läsa inför ett pass.</p>

{% for section in team_sections %}
  <h3 class="doc-group">{{ section.team.name }}</h3>
  {% for document in section.documents %}{{ doc_row(document) }}{% endfor %}
{% endfor %}

{% if team_sections and shared %}<h3 class="doc-group">Gemensamma för klubben</h3>{% endif %}
{% for document in shared %}{{ doc_row(document) }}{% endfor %}

{% if not total %}
  <p class="empty-state">Inga dokument uppladdade ännu.</p>
{% endif %}
{% endblock %}
```
Append to `style.css`:
```css
.doc-group { font-size: 11px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-dim); margin: 18px 0 6px; }
```
With no team folders the page looks exactly as before: no subheadings.

- [ ] **Step 6: Update the `duties.py` docstring.** Replace the sentence `The document is the exception — Slot has no column for it, and one would only restate what is written here.` with:

```
The document is the exception — Slot has no column for it, and one would
only restate what is written here. It is a bare filename: the document
routes look for it in the slot's team folder first and then among the
club's shared documents, so a team can have its own version of an
instruction without a profile of its own.
```

- [ ] **Step 7: Run the full suite and commit**

Run: `cd web && uv run pytest -q`. Expected: all pass except the baseline.

```bash
git add web/src/duty_web/routes/document_routes.py web/src/duty_web/routes/schedule_routes.py web/src/duty_web/app.py web/src/duty_web/templates/mine.html web/src/duty_web/templates/dokument.html web/src/duty_web/static/style.css web/src/duty_web/duties.py web/tests/test_document_routes.py web/tests/test_api_schedule.py
git commit -m "feat(web): instruktioner gemensamma för klubben eller för ett lag" -m "Lagen har i dag olika versioner av samma instruktioner, men kommer troligen att dela dem framöver. Ett dokument i lagets egen mapp gäller före klubbens gemensamma med samma namn, så att flytta en instruktion till gemensam är bara att flytta filen. Ett lags dokument syns och går att hämta bara för föräldrar med barn i laget. Mailet får länken från appen och följer med automatiskt." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: "Mina pass" says which team a duty belongs to

**Files:**
- Modify: `web/src/duty_web/routes/schedule_routes.py` (`mine()`)
- Modify: `web/src/duty_web/templates/mine.html` (both `.slot-station` divs, lines ~23 and ~105)
- Modify: `web/src/duty_web/static/style.css`
- Test: `web/tests/test_schedule_routes.py`

**Interfaces:**
- Consumes: `teams_for_parent` (Task 1) and the `second_team` fixture (Task 6).
- Produces: a `show_team: bool` template variable.

- [ ] **Step 1: Failing tests** (append to `web/tests/test_schedule_routes.py`)

```python
def test_mine_names_the_team_on_each_pass_for_a_parent_on_two(client, seeded, second_team):
    _login(client, seeded["person"])

    body = client.get("/").get_data(as_text=True)

    assert '<span class="slot-team">P13</span>' in body
    assert '<span class="slot-team">F14 Blå</span>' in body


def test_mine_leaves_the_team_out_for_a_parent_on_one(client, seeded, second_team):
    _login(client, seeded["other_parent"])

    assert "slot-team" not in client.get("/").get_data(as_text=True)
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd web && uv run pytest tests/test_schedule_routes.py -k "names_the_team or leaves_the_team" -v`
Expected: the first test FAILS. The second passes already, which is fine.

- [ ] **Step 3: Implement.** In `mine()`, add before `return render_template(...)`:

```python
    # With children on two teams the cards would otherwise look alike;
    # with one team the name is noise.
    show_team = len(teams_for_parent(session, int(current_user.id))) > 1
```
and pass `show_team=show_team` to `render_template`. In `mine.html`, change both occurrences of
```html
<div class="slot-station"><span class="{{ slot.duty_name|chip_class }}">{{ slot.duty_name }}</span></div>
```
(one with 6-space indent, one with 8) to:
```html
<div class="slot-station">{% if show_team %}<span class="slot-team">{{ slot.team.name }}</span>{% endif %}<span class="{{ slot.duty_name|chip_class }}">{{ slot.duty_name }}</span></div>
```
Append to `style.css`:
```css
.slot-team { display: block; font-size: 11px; font-weight: 700; color: var(--ink-dim); margin-bottom: 4px; }
```

- [ ] **Step 4: Run the full suite and commit**

Run: `cd web && uv run pytest -q && cd .. && uv run pytest -q`. Expected: web passes except the baseline, and the mailer suite passes.

```bash
git add web/src/duty_web/routes/schedule_routes.py web/src/duty_web/templates/mine.html web/src/duty_web/static/style.css web/tests/test_schedule_routes.py
git commit -m "feat(web): Mina pass visar laget när man har barn i flera" -m "Med barn i två lag hamnar båda lagens pass i samma lista. Varje kort säger då vilket lag passet hör till. Med ett lag visas inget lagnamn, som förut." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Rollout (operator, after merge — not part of the tasks)

The migration script imports the new models, so it can only run on the new image. The order is: deploy, then migrate straight away. In the minute between the two, anything that reads `teams.slug` errors.

1. `just db-pull /tmp/duty-copy.db`, then rehearse: `cd web && uv run python ../scripts/migrate_multi_team.py /tmp/duty-copy.db f15`.
2. `just web-deploy`
3. `just multi-team-migrate-push f15`
4. Check that F15 still works: log in, look at Mina pass and Hela laget, and confirm a pending swap is still listed.
5. `just team-add <slug> "<Lagnamn>"`
6. `just schedule-build <tränarens fil> data/<slug>_parent_mailing_list.csv data/seed_<slug>.xlsx`. The default contacts and out paths are F15's, so pass all three.
7. `just roster-push <slug> data/<slug>_parent_mailing_list.csv`
8. `just schedule-push <slug> data/seed_<slug>.xlsx`
9. `just docs-push <fil>.pdf <slug>` for each of the new team's own instructions.

The daily mailer (GitHub Actions) needs no change. It reads every slot from `/api/schedule`, now with team-aware instruction links.
