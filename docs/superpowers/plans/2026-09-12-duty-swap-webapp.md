# Duty Swap Web App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Flask + SQLite web app (`web/`) where parents log in via a
magic link, see their duty schedule, and propose/accept swaps with other
parents — seeded once from the existing xlsx — and give `duty_mailer` a new
adapter that reads from it instead of the file.

**Architecture:** One Flask app, two WSGI processes sharing one SQLite file
on a Fly.io volume: a public process (0.0.0.0:8080) serving parent-facing
pages and `/api/schedule`, and a tiny second process bound to
`127.0.0.1:8081` serving only `/admin/import`. SQLAlchemy ORM models map
directly to the spec's data model. Auth is magic-link tokens (`itsdangerous`
signing + a DB single-use check) plus `Flask-Login` sessions.
`duty_mailer` gains one new `ScheduleSource` adapter (`WebAppApiSource`)
and is otherwise untouched.

**Tech Stack:** Python 3.11+, Flask 3.x, SQLAlchemy 2.x (plain ORM, no
Flask-SQLAlchemy), itsdangerous, Flask-Login, Flask-WTF, openpyxl,
pytest. SQLite file on a Fly.io volume (`../charge-amps` pattern).

**Spec:** `docs/superpowers/specs/2026-09-12-duty-swap-webapp-design.md`

## Global Constraints

- All user-facing text (templates, email copy, flash messages) is Swedish;
  code, identifiers, docstrings, and commit messages are English (spec D6
  inherited from `duty_mailer`'s own convention).
- `requires-python = ">=3.11"`, `from __future__ import annotations` at the
  top of every module, matching `duty_mailer`.
- No change above `duty_mailer`'s `sources/` package — `models.py`,
  `scheduling.py`, `templates.py`, `email_sender.py`, `__main__.py` stay
  untouched (spec: duty_mailer integration).
- Session cookies: `HttpOnly`, `Secure`, `SameSite=Lax`, ~60-day lifetime
  (spec: Security baseline).
- Magic-link tokens: single-use, ~20 minute expiry (spec: Security
  baseline).
- `/admin/import` is reachable only via `127.0.0.1` — never bound to the
  public interface (spec: Seeding the database).
- Parent-facing pages show names, never raw email addresses (spec: Data
  model).
- New web app dependencies stay to the narrow, proven set named in the
  spec (`itsdangerous`, `Flask-Login`, `Flask-WTF`) — no ad-hoc auth or
  session code, no additional heavyweight frameworks.

---

## File Structure

```
web/                              # new, sibling to duty_mailer — independently deployable
  pyproject.toml
  Dockerfile
  fly.toml
  entrypoint.sh
  config.example.yaml
  src/duty_web/
    __init__.py
    __main__.py                  # `python -m duty_web` — public WSGI app on 0.0.0.0
    admin_main.py                # `python -m duty_web.admin_main` — admin app on 127.0.0.1
    app.py                       # create_app() factory, Flask-Login/CSRF wiring, blueprint registration
    admin_app.py                 # create_admin_app() factory: just /admin/import
    config.py                    # env-var loading + validation
    db.py                        # engine/session-factory construction, init_db()
    models.py                    # Team, Person, Slot, SwapRequest, LoginToken (SQLAlchemy ORM)
    auth.py                      # magic-link issue/verify, Flask-Login user loading
    schedule_queries.py          # slots_for_person(), slots_for_team_month()
    seed.py                      # import_schedule(): wipe + reimport Person/Slot from xlsx
    swaps.py                     # propose/accept/decline/expire business logic over the DB
    notifications.py             # Swedish swap-event emails via duty_mailer's SMTP pattern
    routes/
      __init__.py
      auth_routes.py             # POST /login, GET /login/verify
      schedule_routes.py         # GET / (Mina pass), GET /team (Hela laget), GET /api/schedule
      swap_routes.py             # POST /swaps, /swaps/<id>/accept, /swaps/<id>/decline
    templates/
      base.html
      login.html
      mine.html
      team.html
    static/
      app.css
  tests/
    conftest.py                  # in-memory-db app + client fixtures
    test_models.py
    test_seed.py
    test_auth.py
    test_app_auth_gate.py
    test_schedule_queries.py
    test_schedule_routes.py
    test_swaps.py
    test_swap_routes.py
    test_api_schedule.py

src/duty_mailer/sources/web_api.py     # new adapter, mirrors google_csv.py
src/duty_mailer/sources/__init__.py    # modify: register "web_api" source
src/duty_mailer/config.py              # modify: VALID_SOURCES + api_key field
tests/test_web_api_source.py           # new
```

`web/` never imports `duty_mailer` code and vice versa — the only contract
between them is the HTTP `/api/schedule` endpoint (Task 9 + Task 10).

---

## Task 1: Package scaffolding, ORM models, and schema creation

**Files:**
- Create: `web/pyproject.toml`
- Create: `web/src/duty_web/__init__.py`
- Create: `web/src/duty_web/models.py`
- Create: `web/src/duty_web/db.py`
- Create: `web/tests/__init__.py`
- Test: `web/tests/test_models.py`

**Interfaces:**
- Produces: `Base` (SQLAlchemy `DeclarativeBase`), ORM classes `Team`,
  `Person`, `Slot`, `SwapRequest`, `LoginToken` (all in `models.py`);
  `make_engine(db_path: str) -> Engine`, `make_session_factory(engine) ->
  sessionmaker[Session]`, `init_db(engine) -> None` (all in `db.py`).

- [ ] **Step 1: Write `web/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "duty-web"
version = "0.1.0"
description = "Parent-facing duty schedule + swap web app"
requires-python = ">=3.11"
dependencies = [
    "flask>=3.0",
    "sqlalchemy>=2.0",
    "itsdangerous>=2.1",
    "flask-login>=0.6",
    "flask-wtf>=1.2",
    "openpyxl>=3.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Write the failing test**

```python
# web/tests/test_models.py
from datetime import date, datetime, time

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, SwapRequest, Team


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def test_slot_links_team_and_person():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    person = Person(name="Alva Exempel", email="tova@exempel.se")
    session.add_all([team, person])
    session.flush()

    slot = Slot(
        team_id=team.id,
        date=date(2026, 1, 16),
        start_time=time(18, 0),
        end_time=time(21, 0),
        station="Cafe",
        duty_name="Arena värdskap",
        venue="Wallenstam arena",
        note="Hämta nyckel helgen innan",
        person_id=person.id,
    )
    session.add(slot)
    session.commit()

    fetched = session.get(Slot, slot.id)
    assert fetched.team.name == "F14 Blå"
    assert fetched.person.email == "tova@exempel.se"


def test_slot_can_be_unfilled():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()

    slot = Slot(
        team_id=team.id,
        date=date(2026, 1, 16),
        start_time=time(18, 0),
        end_time=time(21, 0),
        station="Cafe",
        duty_name="Arena värdskap",
        venue="Wallenstam arena",
        person_id=None,
    )
    session.add(slot)
    session.commit()

    assert session.get(Slot, slot.id).person is None


def test_swap_request_defaults_to_pending():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    a = Person(name="A", email="a@exempel.se")
    b = Person(name="B", email="b@exempel.se")
    session.add_all([team, a, b])
    session.flush()

    slot_a = Slot(
        team_id=team.id, date=date(2026, 1, 16), start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=a.id,
    )
    slot_b = Slot(
        team_id=team.id, date=date(2026, 1, 23), start_time=time(17, 30),
        end_time=time(20, 30), station="Entré", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=b.id,
    )
    session.add_all([slot_a, slot_b])
    session.flush()

    request = SwapRequest(
        proposer_slot_id=slot_a.id,
        target_slot_id=slot_b.id,
        created_at=datetime(2026, 1, 1, 12, 0),
    )
    session.add(request)
    session.commit()

    assert session.get(SwapRequest, request.id).status == "pending"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd web && pip install -e ".[dev]" && pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_web'`

- [ ] **Step 4: Write `web/src/duty_web/models.py`**

```python
"""ORM models — one table per entity in the design spec's data model."""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    venue: Mapped[str]


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[str] = mapped_column(unique=True)


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    date: Mapped[date]
    start_time: Mapped[time]
    end_time: Mapped[time]
    station: Mapped[str]
    duty_name: Mapped[str]
    venue: Mapped[str]
    note: Mapped[str | None] = mapped_column(default=None)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id"), default=None
    )

    team: Mapped[Team] = relationship()
    person: Mapped[Person | None] = relationship()


class SwapRequest(Base):
    __tablename__ = "swap_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposer_slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"))
    target_slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"))
    status: Mapped[str] = mapped_column(default="pending")
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)

    proposer_slot: Mapped[Slot] = relationship(foreign_keys=[proposer_slot_id])
    target_slot: Mapped[Slot] = relationship(foreign_keys=[target_slot_id])


class LoginToken(Base):
    __tablename__ = "login_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str]
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None] = mapped_column(default=None)
```

- [ ] **Step 5: Write `web/src/duty_web/db.py`**

```python
"""Engine/session construction. One SQLite file, one shared schema.

":memory:" is a special case: plain SQLAlchemy pooling hands out a fresh,
independent in-memory database per connection, so a session created by a
test fixture and a session later created inside a Flask route handler
would each see an empty database. StaticPool pins the engine to a single
connection so every session opened from it shares the same in-memory data
— required for the test suite's pattern of seeding via one session and
reading back via another (e.g. through the Flask test client). A real
on-disk path needs no such pinning; the file itself is the shared state.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


def make_engine(db_path: str) -> Engine:
    if db_path == ":memory:":
        return create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
```

- [ ] **Step 6: Create empty `web/src/duty_web/__init__.py` and `web/tests/__init__.py`**

- [ ] **Step 7: Run test to verify it passes**

Run: `cd web && pytest tests/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Commit**

```bash
git add web/pyproject.toml web/src web/tests
git commit -m "feat(web): add duty-web package scaffolding and ORM models"
```

---

## Task 2: Xlsx seeding

**Files:**
- Create: `web/src/duty_web/seed.py`
- Test: `web/tests/test_seed.py`

**Interfaces:**
- Consumes: `Session` (SQLAlchemy), `Team`/`Person`/`Slot` from `models.py`
- Produces: `import_schedule(session: Session, path: str | Path, *, team_id: int) -> int` — wipes and reimports `Person`/`Slot`, returns row count imported.
- Produces: `class SeedError(Exception)`

Expected xlsx columns (header row, case-insensitive): `ar`, `syssla`,
`arena`, `station`, `vecka`, `datum`, `veckodag`, `tid`, `namn`, `epost`,
`anteckning`. Only `datum`, `tid`, `namn`, `epost` are required; `vecka` and
`veckodag` are read and discarded (redundant with `datum`, per spec).
`tid` is `"18:00-21:00"` and is split on `-`.

- [ ] **Step 1: Write the failing test**

```python
# web/tests/test_seed.py
from datetime import date, time

import pytest
from openpyxl import Workbook

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, Team
from duty_web.seed import SeedError, import_schedule


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def write_xlsx(tmp_path, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(
        ["ar", "syssla", "arena", "station", "vecka", "datum", "veckodag",
         "tid", "namn", "epost", "anteckning"]
    )
    for row in rows:
        ws.append(row)
    path = tmp_path / "schema.xlsx"
    wb.save(path)
    return path


def test_import_creates_person_and_slot(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()

    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Alva Exempel", "tova@exempel.se",
         "Hämta nyckel helgen innan"],
    ])

    count = import_schedule(session, path, team_id=team.id)

    assert count == 1
    slot = session.query(Slot).one()
    assert slot.date == date(2026, 1, 16)
    assert slot.start_time == time(18, 0)
    assert slot.end_time == time(21, 0)
    assert slot.station == "Cafe"
    assert slot.note == "Hämta nyckel helgen innan"
    person = session.query(Person).one()
    assert person.email == "tova@exempel.se"
    assert person.name == "Alva Exempel"
    assert slot.person_id == person.id


def test_import_reuses_existing_person_by_email(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()

    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Alva Exempel", "tova@exempel.se", ""],
        [2026, "Arena värdskap", "Wallenstam arena", "Entré", 4, "2026-01-23",
         "Fredag", "17:30-20:30", "Alva Exempel", "tova@exempel.se", ""],
    ])

    import_schedule(session, path, team_id=team.id)

    assert session.query(Person).count() == 1
    assert session.query(Slot).count() == 2


def test_import_wipes_previous_slots_and_people(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    old_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Gammal Person", "gammal@exempel.se", ""],
    ])
    import_schedule(session, old_path, team_id=team.id)

    new_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Kiosk", 5, "2026-02-06",
         "Fredag", "18:00-21:00", "Ny Person", "ny@exempel.se", ""],
    ])
    import_schedule(session, new_path, team_id=team.id)

    assert session.query(Person).count() == 1
    assert session.query(Person).one().email == "ny@exempel.se"


def test_import_rejects_missing_email(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Alva Exempel", "", ""],
    ])

    with pytest.raises(SeedError, match="e-post"):
        import_schedule(session, path, team_id=team.id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && pytest tests/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_web.seed'`

- [ ] **Step 3: Write `web/src/duty_web/seed.py`**

```python
"""One-time xlsx -> database import. Wipes and reimports Person/Slot.

Safe only because it always runs before any parent has logged in (design
spec: Seeding the database) — there is never live swap state to preserve.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from .models import Person, Slot


class SeedError(Exception):
    """Raised when the xlsx cannot be imported. Message is operator-facing."""


def _parse_time_range(raw: str, *, row_number: int) -> tuple[str, str]:
    parts = str(raw).split("-")
    if len(parts) != 2:
        raise SeedError(f"Rad {row_number}: ogiltigt tidsintervall '{raw}'")
    start, end = (p.strip() for p in parts)
    return start, end


def import_schedule(session: Session, path: str | Path, *, team_id: int) -> int:
    path = Path(path)
    if not path.is_file():
        raise SeedError(f"Schemafilen hittades inte: {path}")

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            raise SeedError(f"Schemafilen är tom: {path}") from None

        columns = [str(c).strip().lower() if c is not None else "" for c in header]
        if not any(columns):
            raise SeedError(f"Schemafilen är tom: {path}")

        records = [
            {col: value for col, value in zip(columns, row) if col}
            for row in rows
            if any(cell is not None for cell in row)
        ]
    finally:
        workbook.close()

    session.query(Slot).delete()
    session.query(Person).delete()
    session.flush()

    people_by_email: dict[str, Person] = {}
    count = 0
    for row_number, record in enumerate(records, start=2):
        email = str(record.get("epost") or "").strip().lower()
        name = str(record.get("namn") or "").strip()
        if not email:
            raise SeedError(f"Rad {row_number}: saknar e-post för '{name}'")

        person = people_by_email.get(email)
        if person is None:
            person = Person(name=name, email=email)
            session.add(person)
            session.flush()
            people_by_email[email] = person

        raw_date = record.get("datum")
        if isinstance(raw_date, datetime):
            slot_date = raw_date.date()
        else:
            slot_date = datetime.strptime(str(raw_date), "%Y-%m-%d").date()

        start_str, end_str = _parse_time_range(record.get("tid", ""), row_number=row_number)

        session.add(
            Slot(
                team_id=team_id,
                date=slot_date,
                start_time=datetime.strptime(start_str, "%H:%M").time(),
                end_time=datetime.strptime(end_str, "%H:%M").time(),
                station=str(record.get("station") or "").strip(),
                duty_name=str(record.get("syssla") or "").strip(),
                venue=str(record.get("arena") or "").strip(),
                note=(str(record.get("anteckning")).strip() or None)
                if record.get("anteckning")
                else None,
                person_id=person.id,
            )
        )
        count += 1

    session.commit()
    return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && pytest tests/test_seed.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add web/src/duty_web/seed.py web/tests/test_seed.py
git commit -m "feat(web): add xlsx seeding into Person/Slot"
```

---

## Task 3: Magic-link auth (issue + verify)

**Files:**
- Create: `web/src/duty_web/auth.py`
- Test: `web/tests/test_auth.py`

**Interfaces:**
- Consumes: `Session`, `LoginToken`, `Person` from `models.py`
- Produces: `class AuthError(Exception)`; `issue_login_token(session, email, *, secret_key, now=None) -> str` (returns the token string to embed in the emailed link); `verify_login_token(session, token, *, secret_key, now=None) -> str` (returns the verified email, raises `AuthError` otherwise, marks the `LoginToken` row consumed).

Token expiry is enforced twice, deliberately: `itsdangerous`'s own
`max_age` (so a tampered timestamp can't extend it) and the DB
`expires_at`/`consumed_at` columns (so a valid, unexpired signature can
still only be used once — `itsdangerous` alone does not prevent replay).

- [ ] **Step 1: Write the failing test**

```python
# web/tests/test_auth.py
from datetime import datetime, timedelta

import pytest
from itsdangerous import URLSafeTimedSerializer

from duty_web.auth import AuthError, issue_login_token, verify_login_token
from duty_web.db import init_db, make_engine, make_session_factory

SECRET = "test-secret"


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def test_issued_token_verifies_to_the_same_email():
    session = make_session()
    token = issue_login_token(session, "tova@exempel.se", secret_key=SECRET)

    email = verify_login_token(session, token, secret_key=SECRET)

    assert email == "tova@exempel.se"


def test_token_cannot_be_used_twice():
    session = make_session()
    token = issue_login_token(session, "tova@exempel.se", secret_key=SECRET)
    verify_login_token(session, token, secret_key=SECRET)

    with pytest.raises(AuthError, match="redan använts"):
        verify_login_token(session, token, secret_key=SECRET)


def test_token_rejected_after_expiry():
    session = make_session()
    issued_at = datetime(2026, 1, 1, 12, 0)
    token = issue_login_token(
        session, "tova@exempel.se", secret_key=SECRET, now=issued_at
    )

    with pytest.raises(AuthError, match="upphört"):
        verify_login_token(
            session, token, secret_key=SECRET,
            now=issued_at + timedelta(minutes=21),
        )


def test_tampered_token_is_rejected():
    session = make_session()
    token = issue_login_token(session, "tova@exempel.se", secret_key=SECRET)
    other_serializer = URLSafeTimedSerializer("wrong-secret", salt="login")
    forged = other_serializer.dumps("attacker@exempel.se")

    with pytest.raises(AuthError, match="ogiltig"):
        verify_login_token(session, forged, secret_key=SECRET)


def test_unknown_token_is_rejected():
    session = make_session()

    with pytest.raises(AuthError, match="ogiltig"):
        verify_login_token(session, "not-a-real-token", secret_key=SECRET)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_web.auth'`

- [ ] **Step 3: Write `web/src/duty_web/auth.py`**

```python
"""Magic-link issue/verify.

Two independent checks gate a login: itsdangerous's own signature + max_age
(catches tampering and enforces the 20-minute window even against a forged
timestamp), and a DB row tracking consumed_at (catches replay of a token
that is still within its time window but has already been used once).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .models import LoginToken

TOKEN_MAX_AGE_SECONDS = 20 * 60
SALT = "login"


class AuthError(Exception):
    """Raised when a login link cannot be honored. Message is user-facing."""


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=SALT)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_login_token(
    session: Session,
    email: str,
    *,
    secret_key: str,
    now: datetime | None = None,
) -> str:
    now = now or datetime.utcnow()
    token = _serializer(secret_key).dumps(email)
    session.add(
        LoginToken(
            token_hash=_hash(token),
            email=email,
            expires_at=now + timedelta(seconds=TOKEN_MAX_AGE_SECONDS),
        )
    )
    session.commit()
    return token


def verify_login_token(
    session: Session,
    token: str,
    *,
    secret_key: str,
    now: datetime | None = None,
) -> str:
    now = now or datetime.utcnow()
    try:
        email = _serializer(secret_key).loads(
            token, max_age=TOKEN_MAX_AGE_SECONDS
        )
    except SignatureExpired as exc:
        raise AuthError("Länken har upphört att gälla.") from exc
    except BadSignature as exc:
        raise AuthError("Länken är ogiltig.") from exc

    row = (
        session.query(LoginToken)
        .filter(LoginToken.token_hash == _hash(token))
        .one_or_none()
    )
    if row is None:
        raise AuthError("Länken är ogiltig.")
    if row.consumed_at is not None:
        raise AuthError("Länken har redan använts.")
    if row.expires_at < now:
        raise AuthError("Länken har upphört att gälla.")

    row.consumed_at = now
    session.commit()
    return email
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && pytest tests/test_auth.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add web/src/duty_web/auth.py web/tests/test_auth.py
git commit -m "feat(web): add magic-link token issue/verify"
```

---

## Task 4: App factory, Flask-Login wiring, and the auth gate

**Files:**
- Create: `web/src/duty_web/config.py`
- Create: `web/src/duty_web/app.py`
- Create: `web/tests/conftest.py`
- Test: `web/tests/test_app_auth_gate.py`

**Interfaces:**
- Consumes: `make_engine`, `make_session_factory`, `init_db` (Task 1); `Person` (Task 1)
- Produces: `class AppConfig` (dataclass: `secret_key: str`, `db_path: str`, `api_key: str`); `load_config() -> AppConfig` (reads env vars, raises `ConfigError`); `create_app(config: AppConfig) -> Flask`; every route registered under `create_app` requires login except `/login` and `/login/verify`.

- [ ] **Step 1: Write `web/src/duty_web/config.py`**

```python
"""Environment-only configuration. No secrets in a committed file."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when required environment variables are missing."""


@dataclass(frozen=True)
class AppConfig:
    secret_key: str
    db_path: str
    api_key: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    from_address: str


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Miljövariabeln {name} saknas")
    return value


def load_config() -> AppConfig:
    return AppConfig(
        secret_key=_require("SECRET_KEY"),
        db_path=os.environ.get("DB_PATH", "/data/duty.db"),
        api_key=_require("SCHEDULE_API_KEY"),
        smtp_host=_require("SMTP_HOST"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=_require("SMTP_USER"),
        smtp_password=_require("SMTP_PASSWORD"),
        from_address=_require("FROM_ADDRESS"),
    )
```

- [ ] **Step 2: Write the failing test**

```python
# web/tests/conftest.py
import pytest

from duty_web.app import create_app
from duty_web.config import AppConfig
from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, Team


@pytest.fixture
def app():
    config = AppConfig(
        secret_key="test-secret",
        db_path=":memory:",
        api_key="test-api-key",
        smtp_host="localhost", smtp_port=587, smtp_user="u",
        smtp_password="p", from_address="noreply@exempel.se",
    )
    application = create_app(config)
    application.config.update(TESTING=True)
    yield application


@pytest.fixture
def session_factory(app):
    return app.extensions["duty_web_session_factory"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded(session_factory):
    """A team, a logged-in-able person, and one of their slots."""
    session = session_factory()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    person = Person(name="Alva Exempel", email="tova@exempel.se")
    session.add_all([team, person])
    session.flush()
    from datetime import date, time

    slot = Slot(
        team_id=team.id, date=date(2026, 1, 16), start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=person.id,
    )
    session.add(slot)
    session.commit()
    return {"team": team, "person": person, "slot": slot}
```

```python
# web/tests/test_app_auth_gate.py
def test_mine_redirects_to_login_when_logged_out(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_is_reachable_when_logged_out(client):
    response = client.get("/login")

    assert response.status_code == 200


def test_mine_reachable_after_session_login(client, session_factory, seeded):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(seeded["person"].id)
        sess["_fresh"] = True

    response = client.get("/")

    assert response.status_code == 200
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd web && pytest tests/test_app_auth_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_web.app'`

- [ ] **Step 4: Write `web/src/duty_web/app.py`**

```python
"""Flask app factory. Every route requires login except /login*."""

from __future__ import annotations

from flask import Flask
from flask_login import LoginManager, UserMixin
from flask_wtf import CSRFProtect

from .config import AppConfig
from .db import init_db, make_engine, make_session_factory
from .models import Person


class LoginUser(UserMixin):
    """Adapts a Person row to Flask-Login's expected interface."""

    def __init__(self, person: Person):
        self.person = person
        self.id = str(person.id)


def create_app(config: AppConfig) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 24 * 60 * 60  # 60 days, seconds

    engine = make_engine(config.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)
    app.extensions["duty_web_session_factory"] = session_factory
    app.extensions["duty_web_app_config"] = config

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id: str) -> LoginUser | None:
        session = session_factory()
        person = session.get(Person, int(user_id))
        return LoginUser(person) if person else None

    login_manager.init_app(app)
    CSRFProtect(app)

    from .routes.auth_routes import auth_bp
    from .routes.schedule_routes import schedule_bp
    from .routes.swap_routes import swap_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(swap_bp)

    return app
```

- [ ] **Step 5: Write minimal `web/src/duty_web/routes/__init__.py`, `auth_routes.py`, `swap_routes.py` (stubs so `app.py` imports succeed — filled in by Tasks 5 and 11-12)**

```python
# web/src/duty_web/routes/__init__.py
```

```python
# web/src/duty_web/routes/auth_routes.py
from __future__ import annotations

from flask import Blueprint, render_template

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login")
def login():
    return render_template("login.html")
```

```python
# web/src/duty_web/routes/swap_routes.py
from __future__ import annotations

from flask import Blueprint

swap_bp = Blueprint("swaps", __name__)
```

- [ ] **Step 6: Write `web/src/duty_web/routes/schedule_routes.py` with the logged-in-only home route**

```python
# web/src/duty_web/routes/schedule_routes.py
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

schedule_bp = Blueprint("schedule", __name__)


@schedule_bp.route("/")
@login_required
def mine():
    return render_template("mine.html")
```

- [ ] **Step 7: Write minimal `web/src/duty_web/templates/login.html` and `mine.html`**

```html
{# web/src/duty_web/templates/login.html #}
<!doctype html>
<title>Vaktschema — Logga in</title>
<h1>Logga in</h1>
```

```html
{# web/src/duty_web/templates/mine.html #}
<!doctype html>
<title>Vaktschema — Mina pass</title>
<h1>Mina pass</h1>
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd web && pytest tests/test_app_auth_gate.py -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Commit**

```bash
git add web/src/duty_web/config.py web/src/duty_web/app.py \
        web/src/duty_web/routes web/src/duty_web/templates \
        web/tests/conftest.py web/tests/test_app_auth_gate.py
git commit -m "feat(web): add app factory with Flask-Login auth gate"
```

---

## Task 5: Login routes (request link + verify) with per-email rate limiting

**Files:**
- Modify: `web/src/duty_web/routes/auth_routes.py`
- Modify: `web/src/duty_web/templates/login.html`
- Test: `web/tests/test_auth_routes.py`

**Interfaces:**
- Consumes: `issue_login_token`, `verify_login_token`, `AuthError` (Task 3); `LoginUser` (Task 4); `Person` (Task 1)
- Produces: `POST /login` (form field `email`) — issues a token, "sends" it via `notifications.send_login_link` (stubbed in tests via monkeypatch), flashes a Swedish confirmation; `GET /login/verify?token=...` — logs the matching `Person` in via Flask-Login or renders an error.

Rate limiting reuses `LoginToken` itself: refuse a new token if the most
recent one for that email was issued within the last 60 seconds, rather
than adding a rate-limiting dependency.

- [ ] **Step 1: Write the failing test**

```python
# web/tests/test_auth_routes.py
from datetime import datetime, timedelta

from duty_web.models import LoginToken, Person


def test_login_post_creates_a_token_and_finds_or_creates_the_person(
    client, session_factory, monkeypatch
):
    sent = {}
    monkeypatch.setattr(
        "duty_web.routes.auth_routes.send_login_link",
        lambda email, link: sent.update(email=email, link=link),
    )

    response = client.post("/login", data={"email": "ny@exempel.se"})

    assert response.status_code == 200
    assert sent["email"] == "ny@exempel.se"
    assert "token=" in sent["link"]

    session = session_factory()
    assert session.query(LoginToken).filter_by(email="ny@exempel.se").count() == 1


def test_login_post_is_rate_limited_within_60_seconds(
    client, session_factory, monkeypatch
):
    monkeypatch.setattr(
        "duty_web.routes.auth_routes.send_login_link", lambda email, link: None
    )
    client.post("/login", data={"email": "ny@exempel.se"})

    response = client.post("/login", data={"email": "ny@exempel.se"})

    assert response.status_code == 200
    session = session_factory()
    assert session.query(LoginToken).filter_by(email="ny@exempel.se").count() == 1


def test_verify_logs_in_an_existing_person_and_redirects_home(
    client, session_factory, seeded
):
    from duty_web.auth import issue_login_token

    session = session_factory()
    token = issue_login_token(session, "tova@exempel.se", secret_key="test-secret")

    response = client.get(f"/login/verify?token={token}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_verify_creates_a_person_on_first_login(client, session_factory):
    from duty_web.auth import issue_login_token

    session = session_factory()
    issue_login_token(session, "ny@exempel.se", secret_key="test-secret")
    token = issue_login_token(session, "ny@exempel.se", secret_key="test-secret")

    client.get(f"/login/verify?token={token}")

    assert session.query(Person).filter_by(email="ny@exempel.se").count() == 1


def test_verify_rejects_a_bad_token(client):
    response = client.get("/login/verify?token=not-real")

    assert response.status_code == 200
    assert "ogiltig".encode() in response.data or "ogiltig" in response.get_data(as_text=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && pytest tests/test_auth_routes.py -v`
Expected: FAIL — `ImportError: cannot import name 'send_login_link'`

- [ ] **Step 3: Write `web/src/duty_web/notifications.py` (login-link email only; swap emails added in Task 13)**

```python
"""Swedish email copy for account/swap events. SMTP delivery mirrors
duty_mailer's email_sender.py: stdlib smtplib, STARTTLS.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

_smtp_config: dict[str, str | int] = {}


def configure(*, host: str, port: int, user: str, password: str, from_address: str) -> None:
    _smtp_config.update(
        host=host, port=port, user=user, password=password, from_address=from_address
    )


def _send(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = _smtp_config["from_address"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(_smtp_config["host"], _smtp_config["port"]) as server:
        server.starttls()
        server.login(_smtp_config["user"], _smtp_config["password"])
        server.send_message(message)


def send_login_link(email: str, link: str) -> None:
    _send(
        email,
        "Din inloggningslänk till Vaktschema",
        f"Klicka på länken för att logga in (giltig i 20 minuter):\n\n{link}\n\n"
        "Om du inte bad om den kan du ignorera det här mailet.",
    )
```

- [ ] **Step 4: Write `web/src/duty_web/routes/auth_routes.py`**

```python
"""Magic-link request + verification."""

from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from flask_login import login_user

from ..auth import AuthError, issue_login_token, verify_login_token
from ..models import LoginToken, Person
from ..notifications import send_login_link

auth_bp = Blueprint("auth", __name__)

RATE_LIMIT_SECONDS = 60


def _session():
    return current_app.extensions["duty_web_session_factory"]()


def _secret_key() -> str:
    return current_app.config["SECRET_KEY"]


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form["email"].strip().lower()
    session = _session()

    recent = (
        session.query(LoginToken)
        .filter(LoginToken.email == email)
        .order_by(LoginToken.expires_at.desc())
        .first()
    )
    if recent is not None:
        issued_at = recent.expires_at - timedelta(minutes=20)
        if datetime.utcnow() - issued_at < timedelta(seconds=RATE_LIMIT_SECONDS):
            return render_template("login.html", sent_to=email)

    token = issue_login_token(session, email, secret_key=_secret_key())
    link = url_for("auth.verify", token=token, _external=True)
    send_login_link(email, link)
    return render_template("login.html", sent_to=email)


@auth_bp.route("/login/verify")
def verify():
    session = _session()
    token = request.args.get("token", "")

    try:
        email = verify_login_token(session, token, secret_key=_secret_key())
    except AuthError as exc:
        return render_template("login.html", error=str(exc))

    person = session.query(Person).filter_by(email=email).one_or_none()
    if person is None:
        name = email.split("@")[0].replace(".", " ").title()
        person = Person(name=name, email=email)
        session.add(person)
        session.commit()

    from ..app import LoginUser

    login_user(LoginUser(person), remember=True)
    return redirect(url_for("schedule.mine"))
```

- [ ] **Step 5: Update `web/src/duty_web/templates/login.html` to show the confirmation/error state**

```html
{# web/src/duty_web/templates/login.html #}
<!doctype html>
<title>Vaktschema — Logga in</title>
<h1>Logga in</h1>
<form method="post" action="/login">
  <label for="email">E-postadress</label>
  <input type="email" id="email" name="email" required>
  <button type="submit">Skicka inloggningslänk</button>
</form>
{% if sent_to %}
  <p>Vi har mailat en länk till {{ sent_to }}.</p>
{% endif %}
{% if error %}
  <p>{{ error }}</p>
{% endif %}
```

- [ ] **Step 6: Wire SMTP config into the app factory — modify `web/src/duty_web/app.py`**

Add, right after `app.extensions["duty_web_app_config"] = config`:

```python
    from .notifications import configure as configure_notifications

    configure_notifications(
        host=config.smtp_host, port=config.smtp_port, user=config.smtp_user,
        password=config.smtp_password, from_address=config.from_address,
    )
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd web && pytest tests/test_auth_routes.py -v`
Expected: PASS (5 tests)

- [ ] **Step 8: Run the full suite to confirm nothing regressed**

Run: `cd web && pytest -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add web/src/duty_web/routes/auth_routes.py web/src/duty_web/notifications.py \
        web/src/duty_web/templates/login.html web/src/duty_web/app.py \
        web/tests/test_auth_routes.py
git commit -m "feat(web): add magic-link login routes with rate limiting"
```

---

## Task 6: Schedule query layer

**Files:**
- Create: `web/src/duty_web/schedule_queries.py`
- Test: `web/tests/test_schedule_queries.py`

**Interfaces:**
- Consumes: `Slot`, `Team` (Task 1)
- Produces: `slots_for_person(session, person_id: int, *, on_or_after: date | None = None) -> list[Slot]` (chronological); `slots_for_team_month(session, team_id: int, year: int, month: int) -> list[Slot]` (chronological, includes unfilled slots).

- [ ] **Step 1: Write the failing test**

```python
# web/tests/test_schedule_queries.py
from datetime import date, time

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, Team
from duty_web.schedule_queries import slots_for_person, slots_for_team_month


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def _slot(team_id, person_id, day, station="Cafe"):
    return Slot(
        team_id=team_id, date=date(2026, 1, day), start_time=time(18, 0),
        end_time=time(21, 0), station=station, duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=person_id,
    )


def test_slots_for_person_are_chronological_and_scoped_to_that_person():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    a = Person(name="A", email="a@exempel.se")
    b = Person(name="B", email="b@exempel.se")
    session.add_all([team, a, b])
    session.flush()
    session.add_all([
        _slot(team.id, a.id, 30),
        _slot(team.id, a.id, 16),
        _slot(team.id, b.id, 9),
    ])
    session.commit()

    result = slots_for_person(session, a.id)

    assert [s.date.day for s in result] == [16, 30]


def test_slots_for_team_month_includes_unfilled_slots():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    session.add_all([
        _slot(team.id, None, 16),
        _slot(team.id, None, 23, station="Entré"),
    ])
    session.commit()

    result = slots_for_team_month(session, team.id, 2026, 1)

    assert len(result) == 2
    assert all(s.person is None for s in result)


def test_slots_for_team_month_excludes_other_months():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    session.add(_slot(team.id, None, 16))
    session.add(Slot(
        team_id=team.id, date=date(2026, 2, 6), start_time=time(18, 0),
        end_time=time(21, 0), station="Kiosk", duty_name="Arena värdskap",
        venue="Wallenstam arena",
    ))
    session.commit()

    result = slots_for_team_month(session, team.id, 2026, 1)

    assert len(result) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && pytest tests/test_schedule_queries.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `web/src/duty_web/schedule_queries.py`**

```python
"""Read queries shared by the schedule routes and /api/schedule."""

from __future__ import annotations

from datetime import date

from sqlalchemy import extract
from sqlalchemy.orm import Session

from .models import Slot


def slots_for_person(
    session: Session, person_id: int, *, on_or_after: date | None = None
) -> list[Slot]:
    query = session.query(Slot).filter(Slot.person_id == person_id)
    if on_or_after is not None:
        query = query.filter(Slot.date >= on_or_after)
    return query.order_by(Slot.date).all()


def slots_for_team_month(
    session: Session, team_id: int, year: int, month: int
) -> list[Slot]:
    return (
        session.query(Slot)
        .filter(
            Slot.team_id == team_id,
            extract("year", Slot.date) == year,
            extract("month", Slot.date) == month,
        )
        .order_by(Slot.date)
        .all()
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && pytest tests/test_schedule_queries.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add web/src/duty_web/schedule_queries.py web/tests/test_schedule_queries.py
git commit -m "feat(web): add schedule query layer"
```

---

## Task 7: Mina pass and Hela laget routes + templates

**Files:**
- Modify: `web/src/duty_web/routes/schedule_routes.py`
- Modify: `web/src/duty_web/templates/mine.html`
- Create: `web/src/duty_web/templates/team.html`
- Test: `web/tests/test_schedule_routes.py`

**Interfaces:**
- Consumes: `slots_for_person`, `slots_for_team_month` (Task 6); `current_user` (Flask-Login, Task 4)
- Produces: `GET /` — renders the logged-in person's upcoming slots; `GET /team?year=&month=` — renders the whole team's slots for that month (defaults to current month).

- [ ] **Step 1: Write the failing test**

```python
# web/tests/test_schedule_routes.py
def _login(client, person):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True


def test_mine_lists_the_logged_in_persons_slots(client, seeded):
    _login(client, seeded["person"])

    response = client.get("/")

    assert response.status_code == 200
    assert "Cafe" in response.get_data(as_text=True)
    assert "16" in response.get_data(as_text=True)


def test_team_lists_slots_for_the_requested_month(client, seeded):
    _login(client, seeded["person"])

    response = client.get("/team?year=2026&month=1")

    assert response.status_code == 200
    assert "Cafe" in response.get_data(as_text=True)


def test_team_is_empty_for_a_month_with_no_slots(client, seeded):
    _login(client, seeded["person"])

    response = client.get("/team?year=2026&month=6")

    assert response.status_code == 200
    assert "Cafe" not in response.get_data(as_text=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && pytest tests/test_schedule_routes.py -v`
Expected: FAIL — mine.html has no slot data, `/team` returns 404

- [ ] **Step 3: Write `web/src/duty_web/routes/schedule_routes.py`**

```python
"""Mina pass (personal list) and Hela laget (team calendar)."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, current_app, render_template, request
from flask_login import current_user, login_required

from ..models import Slot, Team
from ..schedule_queries import slots_for_person, slots_for_team_month

schedule_bp = Blueprint("schedule", __name__)


def _session():
    return current_app.extensions["duty_web_session_factory"]()


def _team_id_for(session, person) -> int:
    """v1 has exactly one team, so every slot's team_id is the same value.

    Looked up from any slot the person holds rather than hard-coded, so the
    single-team assumption lives in one place ready to widen later (see the
    spec's multi-team forward-compatibility note).
    """
    slot = session.query(Slot).filter(Slot.person_id == person.id).first()
    if slot is not None:
        return slot.team_id
    return session.query(Team).first().id


@schedule_bp.route("/")
@login_required
def mine():
    session = _session()
    slots = slots_for_person(session, int(current_user.id), on_or_after=date.today())
    return render_template("mine.html", slots=slots)


@schedule_bp.route("/team")
@login_required
def team():
    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)
    session = _session()
    team_id = _team_id_for(session, current_user.person)
    slots = slots_for_team_month(session, team_id, year, month)
    return render_template("team.html", slots=slots, year=year, month=month)
```

- [ ] **Step 4: Write `web/src/duty_web/templates/mine.html`**

```html
{# web/src/duty_web/templates/mine.html #}
<!doctype html>
<title>Vaktschema — Mina pass</title>
<h1>Mina pass</h1>
<ul>
  {% for slot in slots %}
    <li>
      {{ slot.date.strftime("%d %b") }} — {{ slot.station }} —
      {{ slot.start_time.strftime("%H:%M") }}–{{ slot.end_time.strftime("%H:%M") }}
      {% if slot.note %}<p>{{ slot.note }}</p>{% endif %}
    </li>
  {% else %}
    <li>Inga kommande pass.</li>
  {% endfor %}
</ul>
<a href="/team">Hela laget →</a>
```

- [ ] **Step 5: Write `web/src/duty_web/templates/team.html`**

```html
{# web/src/duty_web/templates/team.html #}
<!doctype html>
<title>Vaktschema — Hela laget</title>
<h1>Hela laget — {{ year }}-{{ "%02d" % month }}</h1>
<ul>
  {% for slot in slots %}
    <li>
      {{ slot.date.strftime("%d %b") }} — {{ slot.station }} —
      {{ slot.person.name if slot.person else "Ledigt" }}
    </li>
  {% else %}
    <li>Inga pass denna månad.</li>
  {% endfor %}
</ul>
<a href="/">← Mina pass</a>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd web && pytest tests/test_schedule_routes.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add web/src/duty_web/routes/schedule_routes.py web/src/duty_web/templates
git commit -m "feat(web): add Mina pass and Hela laget routes"
```

---

## Task 8: Swap proposal + accept/decline + auto-expiry

**Files:**
- Create: `web/src/duty_web/swaps.py`
- Test: `web/tests/test_swaps.py`

**Interfaces:**
- Consumes: `Slot`, `SwapRequest` (Task 1)
- Produces: `class SwapError(Exception)`; `propose_swap(session, *, proposer_slot_id: int, target_slot_id: int, proposer_person_id: int, now: datetime) -> SwapRequest`; `accept_swap(session, swap_request_id: int, *, accepting_person_id: int, now: datetime) -> SwapRequest`; `decline_swap(session, swap_request_id: int, *, declining_person_id: int, now: datetime) -> SwapRequest`; `expire_stale_swaps(session, *, now: datetime, max_age_days: int = 7) -> int` (returns count expired).

- [ ] **Step 1: Write the failing test**

```python
# web/tests/test_swaps.py
from datetime import datetime, timedelta

import pytest

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, SwapRequest, Team
from duty_web.swaps import (
    SwapError,
    accept_swap,
    decline_swap,
    expire_stale_swaps,
    propose_swap,
)


def make_fixtures():
    engine = make_engine(":memory:")
    init_db(engine)
    session = make_session_factory(engine)()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    a = Person(name="A", email="a@exempel.se")
    b = Person(name="B", email="b@exempel.se")
    session.add_all([team, a, b])
    session.flush()
    from datetime import date, time

    slot_a = Slot(
        team_id=team.id, date=date(2026, 1, 16), start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=a.id,
    )
    slot_b = Slot(
        team_id=team.id, date=date(2026, 1, 23), start_time=time(17, 30),
        end_time=time(20, 30), station="Entré", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=b.id,
    )
    session.add_all([slot_a, slot_b])
    session.commit()
    return session, a, b, slot_a, slot_b


def test_propose_swap_creates_a_pending_request():
    session, a, b, slot_a, slot_b = make_fixtures()

    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    assert request.status == "pending"


def test_propose_swap_rejects_a_slot_you_do_not_own():
    session, a, b, slot_a, slot_b = make_fixtures()

    with pytest.raises(SwapError, match="ditt eget"):
        propose_swap(
            session, proposer_slot_id=slot_b.id, target_slot_id=slot_a.id,
            proposer_person_id=a.id, now=datetime(2026, 1, 1),
        )


def test_accept_swap_exchanges_the_two_slots_person_id():
    session, a, b, slot_a, slot_b = make_fixtures()
    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    accept_swap(session, request.id, accepting_person_id=b.id, now=datetime(2026, 1, 2))

    session.refresh(slot_a)
    session.refresh(slot_b)
    assert slot_a.person_id == b.id
    assert slot_b.person_id == a.id
    assert session.get(SwapRequest, request.id).status == "accepted"


def test_accept_swap_rejects_the_wrong_person():
    session, a, b, slot_a, slot_b = make_fixtures()
    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    with pytest.raises(SwapError, match="inte din"):
        accept_swap(session, request.id, accepting_person_id=a.id, now=datetime(2026, 1, 2))


def test_decline_swap_marks_it_declined_without_moving_anyone():
    session, a, b, slot_a, slot_b = make_fixtures()
    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    decline_swap(session, request.id, declining_person_id=b.id, now=datetime(2026, 1, 2))

    session.refresh(slot_a)
    assert slot_a.person_id == a.id
    assert session.get(SwapRequest, request.id).status == "declined"


def test_expire_stale_swaps_expires_only_old_pending_requests():
    session, a, b, slot_a, slot_b = make_fixtures()
    old_request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    count = expire_stale_swaps(session, now=datetime(2026, 1, 9))

    assert count == 1
    assert session.get(SwapRequest, old_request.id).status == "expired"


def test_accepting_an_expired_swap_is_rejected():
    session, a, b, slot_a, slot_b = make_fixtures()
    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )
    expire_stale_swaps(session, now=datetime(2026, 1, 9))

    with pytest.raises(SwapError, match="upphört"):
        accept_swap(session, request.id, accepting_person_id=b.id, now=datetime(2026, 1, 10))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && pytest tests/test_swaps.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `web/src/duty_web/swaps.py`**

```python
"""Swap request state machine: propose -> accept | decline | expire."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import Slot, SwapRequest


class SwapError(Exception):
    """Raised when a swap action is invalid. Message is user-facing."""


def propose_swap(
    session: Session,
    *,
    proposer_slot_id: int,
    target_slot_id: int,
    proposer_person_id: int,
    now: datetime,
) -> SwapRequest:
    proposer_slot = session.get(Slot, proposer_slot_id)
    if proposer_slot is None or proposer_slot.person_id != proposer_person_id:
        raise SwapError("Du kan bara föreslå byte av ditt eget pass.")

    request = SwapRequest(
        proposer_slot_id=proposer_slot_id,
        target_slot_id=target_slot_id,
        status="pending",
        created_at=now,
    )
    session.add(request)
    session.commit()
    return request


def _pending_request(session: Session, swap_request_id: int, *, now: datetime) -> SwapRequest:
    request = session.get(SwapRequest, swap_request_id)
    if request is None:
        raise SwapError("Bytesförslaget hittades inte.")
    if request.status == "expired" or (
        request.status == "pending" and now - request.created_at > timedelta(days=7)
    ):
        request.status = "expired"
        request.resolved_at = now
        session.commit()
        raise SwapError("Bytesförslaget har upphört att gälla.")
    if request.status != "pending":
        raise SwapError("Bytesförslaget är redan avgjort.")
    return request


def accept_swap(
    session: Session, swap_request_id: int, *, accepting_person_id: int, now: datetime
) -> SwapRequest:
    request = _pending_request(session, swap_request_id, now=now)
    target_slot = session.get(Slot, request.target_slot_id)
    if target_slot.person_id != accepting_person_id:
        raise SwapError("Det här bytet är inte ditt att acceptera.")

    proposer_slot = session.get(Slot, request.proposer_slot_id)
    proposer_slot.person_id, target_slot.person_id = (
        target_slot.person_id,
        proposer_slot.person_id,
    )
    request.status = "accepted"
    request.resolved_at = now
    session.commit()
    return request


def decline_swap(
    session: Session, swap_request_id: int, *, declining_person_id: int, now: datetime
) -> SwapRequest:
    request = _pending_request(session, swap_request_id, now=now)
    target_slot = session.get(Slot, request.target_slot_id)
    if target_slot.person_id != declining_person_id:
        raise SwapError("Det här bytet är inte ditt att avböja.")

    request.status = "declined"
    request.resolved_at = now
    session.commit()
    return request


def expire_stale_swaps(session: Session, *, now: datetime, max_age_days: int = 7) -> int:
    cutoff = now - timedelta(days=max_age_days)
    stale = (
        session.query(SwapRequest)
        .filter(SwapRequest.status == "pending", SwapRequest.created_at < cutoff)
        .all()
    )
    for request in stale:
        request.status = "expired"
        request.resolved_at = now
    session.commit()
    return len(stale)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && pytest tests/test_swaps.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add web/src/duty_web/swaps.py web/tests/test_swaps.py
git commit -m "feat(web): add swap propose/accept/decline/expire logic"
```

---

## Task 9: Swap routes + notification emails

**Files:**
- Modify: `web/src/duty_web/routes/swap_routes.py`
- Modify: `web/src/duty_web/notifications.py`
- Modify: `web/src/duty_web/templates/mine.html`
- Test: `web/tests/test_swap_routes.py`

**Interfaces:**
- Consumes: `propose_swap`, `accept_swap`, `decline_swap` (Task 8); `slots_for_person` (Task 6)
- Produces: `POST /swaps` (form: `proposer_slot_id`, `target_slot_ids` — one or more, sent as several proposals) redirects to `/`; `POST /swaps/<id>/accept`, `POST /swaps/<id>/decline` redirect to `/`; `send_swap_proposed(to, ...)`, `send_swap_accepted(to, ...)`, `send_swap_declined(to, ...)` in `notifications.py`.

- [ ] **Step 1: Write the failing test**

```python
# web/tests/test_swap_routes.py
from datetime import date, time

from duty_web.models import Person, Slot


def _login(client, person):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True


def _add_target_slot(session_factory, team, target_person, day=23):
    session = session_factory()
    slot = Slot(
        team_id=team.id, date=date(2026, 1, day), start_time=time(17, 30),
        end_time=time(20, 30), station="Entré", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=target_person.id,
    )
    session.add(slot)
    session.commit()
    return slot


def test_post_swaps_creates_a_pending_request_and_notifies_the_target(
    client, session_factory, seeded, monkeypatch
):
    notified = []
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed",
        lambda to, **kwargs: notified.append(to),
    )
    target_person = Person(name="Sara B", email="sara@exempel.se")
    session = session_factory()
    session.add(target_person)
    session.commit()
    target_slot = _add_target_slot(session_factory, seeded["team"], target_person)

    _login(client, seeded["person"])
    response = client.post(
        "/swaps",
        data={
            "proposer_slot_id": seeded["slot"].id,
            "target_slot_ids": [str(target_slot.id)],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert notified == ["sara@exempel.se"]


def test_post_swaps_accept_route_swaps_ownership_and_notifies_proposer(
    client, session_factory, seeded, monkeypatch
):
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed", lambda to, **kwargs: None
    )
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_accepted",
        lambda to, **kwargs: notified.append(to),
    )
    notified: list[str] = []
    target_person = Person(name="Sara B", email="sara@exempel.se")
    session = session_factory()
    session.add(target_person)
    session.commit()
    target_slot = _add_target_slot(session_factory, seeded["team"], target_person)

    _login(client, seeded["person"])
    client.post(
        "/swaps",
        data={"proposer_slot_id": seeded["slot"].id, "target_slot_ids": [str(target_slot.id)]},
    )
    from duty_web.models import SwapRequest

    swap_id = session.query(SwapRequest).one().id

    _login(client, target_person)
    response = client.post(f"/swaps/{swap_id}/accept", follow_redirects=False)

    assert response.status_code == 302
    session.refresh(seeded["slot"])
    assert seeded["slot"].person_id == target_person.id
    assert notified == [seeded["person"].email]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && pytest tests/test_swap_routes.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Add notification functions to `web/src/duty_web/notifications.py`**

```python
def send_swap_proposed(email: str, *, proposer_name: str, proposer_slot: str, target_slot: str) -> None:
    _send(
        email,
        f"{proposer_name} vill byta pass med dig",
        f"{proposer_name} föreslår att byta sitt pass ({proposer_slot}) mot ditt "
        f"({target_slot}). Logga in på Vaktschema för att acceptera eller avböja.",
    )


def send_swap_accepted(email: str, *, accepter_name: str, your_old_slot: str, your_new_slot: str) -> None:
    _send(
        email,
        "Ditt bytesförslag accepterades",
        f"{accepter_name} accepterade bytet. Du har nu {your_new_slot} istället för "
        f"{your_old_slot}.",
    )


def send_swap_declined(email: str, *, decliner_name: str, your_slot: str) -> None:
    _send(
        email,
        "Ditt bytesförslag avböjdes",
        f"{decliner_name} avböjde bytet. Du behåller {your_slot}.",
    )
```

- [ ] **Step 4: Write `web/src/duty_web/routes/swap_routes.py`**

```python
"""Propose/accept/decline swap routes."""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, redirect, request, url_for
from flask_login import current_user, login_required

from ..models import Slot
from ..notifications import send_swap_accepted, send_swap_declined, send_swap_proposed
from ..swaps import SwapError, accept_swap, decline_swap, propose_swap

swap_bp = Blueprint("swaps", __name__)


def _session():
    return current_app.extensions["duty_web_session_factory"]()


def _format_slot(slot: Slot) -> str:
    return f"{slot.date.strftime('%d %b')} {slot.station} {slot.start_time.strftime('%H:%M')}"


@swap_bp.route("/swaps", methods=["POST"])
@login_required
def create():
    session = _session()
    proposer_slot_id = int(request.form["proposer_slot_id"])
    target_slot_ids = request.form.getlist("target_slot_ids")

    for raw_target_id in target_slot_ids:
        target_slot_id = int(raw_target_id)
        try:
            propose_swap(
                session,
                proposer_slot_id=proposer_slot_id,
                target_slot_id=target_slot_id,
                proposer_person_id=int(current_user.id),
                now=datetime.utcnow(),
            )
        except SwapError:
            continue

        proposer_slot = session.get(Slot, proposer_slot_id)
        target_slot = session.get(Slot, target_slot_id)
        send_swap_proposed(
            target_slot.person.email,
            proposer_name=proposer_slot.person.name,
            proposer_slot=_format_slot(proposer_slot),
            target_slot=_format_slot(target_slot),
        )

    return redirect(url_for("schedule.mine"))


@swap_bp.route("/swaps/<int:swap_id>/accept", methods=["POST"])
@login_required
def accept(swap_id: int):
    session = _session()
    request_row = accept_swap(
        session, swap_id, accepting_person_id=int(current_user.id), now=datetime.utcnow()
    )
    proposer_slot = session.get(Slot, request_row.proposer_slot_id)
    target_slot = session.get(Slot, request_row.target_slot_id)
    send_swap_accepted(
        proposer_slot.person.email,
        accepter_name=target_slot.person.name,
        your_old_slot=_format_slot(target_slot),
        your_new_slot=_format_slot(proposer_slot),
    )
    return redirect(url_for("schedule.mine"))


@swap_bp.route("/swaps/<int:swap_id>/decline", methods=["POST"])
@login_required
def decline(swap_id: int):
    session = _session()
    request_row = decline_swap(
        session, swap_id, declining_person_id=int(current_user.id), now=datetime.utcnow()
    )
    proposer_slot = session.get(Slot, request_row.proposer_slot_id)
    send_swap_declined(
        proposer_slot.person.email,
        decliner_name=proposer_slot.person.name,
        your_slot=_format_slot(proposer_slot),
    )
    return redirect(url_for("schedule.mine"))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web && pytest tests/test_swap_routes.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full suite**

Run: `cd web && pytest -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add web/src/duty_web/routes/swap_routes.py web/src/duty_web/notifications.py \
        web/tests/test_swap_routes.py
git commit -m "feat(web): add swap routes with propose/accept/decline notifications"
```

---

## Task 10: `/api/schedule` read-only endpoint

**Files:**
- Modify: `web/src/duty_web/routes/schedule_routes.py`
- Test: `web/tests/test_api_schedule.py`

**Interfaces:**
- Consumes: `Slot`, `Team` (Task 1)
- Produces: `GET /api/schedule` (header `X-Api-Key`) — JSON array of `{date, start_time, end_time, station, duty_name, venue, note, person: {name, email}}` for every slot with an assigned person, across all teams. No login/CSRF (separate auth scheme, per spec).

- [ ] **Step 1: Write the failing test**

```python
# web/tests/test_api_schedule.py
def test_api_schedule_requires_the_api_key(client):
    response = client.get("/api/schedule")

    assert response.status_code == 401


def test_api_schedule_rejects_the_wrong_key(client):
    response = client.get("/api/schedule", headers={"X-Api-Key": "wrong"})

    assert response.status_code == 401


def test_api_schedule_returns_filled_slots_only(client, seeded, session_factory):
    from datetime import date, time
    from duty_web.models import Slot

    session = session_factory()
    session.add(Slot(
        team_id=seeded["team"].id, date=date(2026, 2, 6), start_time=time(18, 0),
        end_time=time(21, 0), station="Kiosk", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=None,
    ))
    session.commit()

    response = client.get("/api/schedule", headers={"X-Api-Key": "test-api-key"})

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload) == 1
    assert payload[0]["person"]["email"] == "tova@exempel.se"
    assert payload[0]["station"] == "Cafe"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && pytest tests/test_api_schedule.py -v`
Expected: FAIL — 404 (route does not exist)

- [ ] **Step 3: Add the route to `web/src/duty_web/routes/schedule_routes.py`**

```python
from flask import jsonify

from ..models import Slot


@schedule_bp.route("/api/schedule")
def api_schedule():
    expected_key = current_app.extensions["duty_web_app_config"].api_key
    provided_key = request.headers.get("X-Api-Key")
    if provided_key != expected_key:
        return jsonify({"error": "unauthorized"}), 401

    session = _session()
    slots = session.query(Slot).filter(Slot.person_id.isnot(None)).order_by(Slot.date).all()
    return jsonify([
        {
            "date": slot.date.isoformat(),
            "start_time": slot.start_time.isoformat(timespec="minutes"),
            "end_time": slot.end_time.isoformat(timespec="minutes"),
            "station": slot.station,
            "duty_name": slot.duty_name,
            "venue": slot.venue,
            "note": slot.note,
            "person": {"name": slot.person.name, "email": slot.person.email},
        }
        for slot in slots
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && pytest tests/test_api_schedule.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add web/src/duty_web/routes/schedule_routes.py web/tests/test_api_schedule.py
git commit -m "feat(web): add API-key-authenticated /api/schedule endpoint"
```

---

## Task 11: `WebAppApiSource` adapter in `duty_mailer`

**Files:**
- Create: `src/duty_mailer/sources/web_api.py`
- Modify: `src/duty_mailer/sources/__init__.py`
- Modify: `src/duty_mailer/config.py`
- Test: `tests/test_web_api_source.py`

**Interfaces:**
- Consumes: `Occurrence`, `Person` (`duty_mailer/models.py`, unchanged); `RosterError` (`sources/rows.py`, unchanged)
- Produces: `class WebAppApiSource` implementing `ScheduleSource.fetch()`; `build_source` now also handles `cfg.source == "web_api"`; `ScheduleConfig` gains `api_key: str | None = None`.

The adapter groups the API's flat per-slot list into `Occurrence`s by
date + duty, mirroring how `rows.py` already groups wide/tall rows — one
`Occurrence` per date, one `Person` per slot's assignee.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_api_source.py
from datetime import date

import responses

from duty_mailer.sources.rows import RosterError
from duty_mailer.sources.web_api import WebAppApiSource


@responses.activate
def test_fetch_builds_one_occurrence_per_date():
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
                "station": "Cafe", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": "Hämta nyckel",
                "person": {"name": "Alva Exempel", "email": "tova@exempel.se"},
            },
            {
                "date": "2026-01-16", "start_time": "17:30", "end_time": "20:30",
                "station": "Entré", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": None,
                "person": {"name": "Erik L", "email": "erik@exempel.se"},
            },
        ],
        match=[responses.matchers.header_matcher({"X-Api-Key": "secret"})],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert len(occurrences) == 1
    occ = occurrences[0]
    assert occ.due == date(2026, 1, 16)
    assert {p.email for p in occ.people} == {"tova@exempel.se", "erik@exempel.se"}


@responses.activate
def test_fetch_raises_roster_error_on_http_failure():
    responses.get("https://web.example/api/schedule", status=500)
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    try:
        source.fetch()
        assert False, "expected RosterError"
    except RosterError:
        pass


@responses.activate
def test_fetch_raises_roster_error_on_unauthorized():
    responses.get("https://web.example/api/schedule", status=401)
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="wrong", default_lead_days=(1,)
    )

    try:
        source.fetch()
        assert False, "expected RosterError"
    except RosterError:
        pass
```

Add `responses>=0.25` to `duty_mailer`'s `dev` extra in `pyproject.toml`
(matches `requests`, which is already a runtime dependency) before running
this task — modify:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "responses>=0.25"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pip install -e ".[dev]" && pytest tests/test_web_api_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_mailer.sources.web_api'`

- [ ] **Step 3: Write `src/duty_mailer/sources/web_api.py`**

```python
"""Roster read from the duty swap web app's /api/schedule endpoint."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

import requests

from ..models import Occurrence, Person
from .rows import RosterError


class WebAppApiSource:
    """Reads the roster from the web app once it is seeded and authoritative."""

    def __init__(
        self,
        url: str,
        *,
        api_key: str,
        default_lead_days: tuple[int, ...],
        timeout: int = 30,
    ):
        self._url = url
        self._api_key = api_key
        self._default_lead_days = default_lead_days
        self._timeout = timeout

    def fetch(self) -> list[Occurrence]:
        try:
            response = requests.get(
                self._url,
                headers={"X-Api-Key": self._api_key},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RosterError(f"Kunde inte hämta schemat från webbappen: {exc}") from exc

        by_date: dict[date, list[Person]] = defaultdict(list)
        for entry in response.json():
            due = datetime.strptime(entry["date"], "%Y-%m-%d").date()
            person_data = entry["person"]
            by_date[due].append(
                Person(email=person_data["email"], name=person_data["name"])
            )

        return [
            Occurrence(due=due, people=tuple(people), lead_days=self._default_lead_days)
            for due, people in sorted(by_date.items())
        ]
```

- [ ] **Step 4: Modify `src/duty_mailer/sources/__init__.py`**

```python
    if cfg.source == "web_api":
        from .web_api import WebAppApiSource

        assert cfg.url is not None  # guaranteed by config validation
        assert cfg.api_key is not None  # guaranteed by config validation
        return WebAppApiSource(
            cfg.url, api_key=cfg.api_key, default_lead_days=cfg.default_lead_days
        )

```

(Insert this block right after the existing `google_csv` branch, before
the final `raise RosterError(...)` line.)

- [ ] **Step 5: Modify `src/duty_mailer/config.py`**

```python
VALID_SOURCES = ("xlsx", "google_csv", "web_api")
```

Add `api_key: str | None = None` to `ScheduleConfig`, and in `load_config`:

```python
    schedule = ScheduleConfig(
        source=source,
        chore=_require(schedule_raw, "chore", "schedule"),
        path=schedule_raw.get("path"),
        url=schedule_raw.get("url"),
        api_key=os.environ.get("SCHEDULE_API_KEY"),
        default_lead_days=tuple(sorted(lead_days, reverse=True)),
        timezone=schedule_raw.get("timezone", "Europe/Stockholm"),
        schedule_link=schedule_raw.get("schedule_link"),
        max_handoff_gap_days=schedule_raw.get("max_handoff_gap_days", 8),
    )
```

(Add `import os` at the top of `config.py` if not already present.) Then,
alongside the existing xlsx/google_csv validation:

```python
    if schedule.source == "web_api" and not schedule.url:
        raise ConfigError("schedule.url krävs när schedule.source är 'web_api'")
    if schedule.source == "web_api" and not schedule.api_key:
        raise ConfigError("Miljövariabeln SCHEDULE_API_KEY krävs när schedule.source är 'web_api'")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_web_api_source.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run the full duty_mailer suite**

Run: `pytest -v`
Expected: all PASS (existing 97 + 3 new, plus any new config tests you add for the `web_api` validation branch)

- [ ] **Step 8: Commit**

```bash
git add src/duty_mailer/sources/web_api.py src/duty_mailer/sources/__init__.py \
        src/duty_mailer/config.py pyproject.toml tests/test_web_api_source.py
git commit -m "feat: add WebAppApiSource adapter for duty_mailer"
```

---

## Task 12: Admin import app (localhost-only) + CLI wiring

**Files:**
- Create: `web/src/duty_web/admin_app.py`
- Create: `web/src/duty_web/admin_main.py`
- Create: `web/src/duty_web/__main__.py`
- Test: `web/tests/test_admin_app.py`

**Interfaces:**
- Consumes: `import_schedule`, `SeedError` (Task 2); `AppConfig`, `load_config` (Task 4)
- Produces: `create_admin_app(config: AppConfig) -> Flask` — a *separate* Flask app (no Flask-Login, no CSRF — only reachable from inside the VM per the network binding, see Global Constraints) with `POST /admin/import` (multipart file upload, field `file`; query/form param `team_id`).

The test suite cannot assert the `127.0.0.1` binding itself (that is a
runtime/process property, verified in Task 13's deployment smoke test) —
it verifies the *application* behavior: a valid upload imports, an invalid
one reports the `SeedError` message.

- [ ] **Step 1: Write the failing test**

```python
# web/tests/test_admin_app.py
import io

from openpyxl import Workbook

from duty_web.admin_app import create_admin_app
from duty_web.config import AppConfig
from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Slot, Team


def make_app_and_session():
    config = AppConfig(
        secret_key="s", db_path=":memory:", api_key="k",
        smtp_host="h", smtp_port=587, smtp_user="u", smtp_password="p",
        from_address="noreply@exempel.se",
    )
    app = create_admin_app(config)
    session_factory = app.extensions["duty_web_session_factory"]
    return app, session_factory


def xlsx_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["ar", "syssla", "arena", "station", "vecka", "datum", "veckodag",
               "tid", "namn", "epost", "anteckning"])
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def test_admin_import_creates_slots():
    app, session_factory = make_app_and_session()
    session = session_factory()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    client = app.test_client()

    data = {
        "team_id": str(team.id),
        "file": (xlsx_bytes([
            [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
             "Fredag", "18:00-21:00", "Alva Exempel", "tova@exempel.se", ""],
        ]), "schema.xlsx"),
    }
    response = client.post("/admin/import", data=data, content_type="multipart/form-data")

    assert response.status_code == 200
    assert session.query(Slot).count() == 1


def test_admin_import_reports_seed_errors_as_400():
    app, session_factory = make_app_and_session()
    session = session_factory()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    client = app.test_client()

    data = {
        "team_id": str(team.id),
        "file": (xlsx_bytes([
            [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
             "Fredag", "18:00-21:00", "Alva Exempel", "", ""],
        ]), "schema.xlsx"),
    }
    response = client.post("/admin/import", data=data, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "e-post" in response.get_data(as_text=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && pytest tests/test_admin_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_web.admin_app'`

- [ ] **Step 3: Write `web/src/duty_web/admin_app.py`**

```python
"""The localhost-only import app. Never bound to a public interface — see
entrypoint.sh, which starts this on 127.0.0.1 and the public app on 0.0.0.0.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from flask import Flask, jsonify, request

from .config import AppConfig
from .db import init_db, make_engine, make_session_factory
from .seed import SeedError, import_schedule


def create_admin_app(config: AppConfig) -> Flask:
    app = Flask(__name__)
    engine = make_engine(config.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)
    app.extensions["duty_web_session_factory"] = session_factory

    @app.route("/admin/import", methods=["POST"])
    def admin_import():
        uploaded = request.files.get("file")
        team_id = request.form.get("team_id", type=int)
        if uploaded is None or team_id is None:
            return jsonify({"error": "file och team_id krävs"}), 400

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "schema.xlsx"
            uploaded.save(path)
            session = session_factory()
            try:
                count = import_schedule(session, path, team_id=team_id)
            except SeedError as exc:
                return jsonify({"error": str(exc)}), 400

        return jsonify({"imported": count}), 200

    return app
```

- [ ] **Step 4: Write `web/src/duty_web/admin_main.py` and `web/src/duty_web/__main__.py`**

```python
# web/src/duty_web/admin_main.py
"""Entry point for the localhost-only admin process."""

from __future__ import annotations

from .admin_app import create_admin_app
from .config import load_config

if __name__ == "__main__":
    app = create_admin_app(load_config())
    app.run(host="127.0.0.1", port=8081)
```

```python
# web/src/duty_web/__main__.py
"""Entry point for the public-facing process."""

from __future__ import annotations

from .app import create_app
from .config import load_config

if __name__ == "__main__":
    app = create_app(load_config())
    app.run(host="0.0.0.0", port=8080)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web && pytest tests/test_admin_app.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full web/ suite**

Run: `cd web && pytest -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add web/src/duty_web/admin_app.py web/src/duty_web/admin_main.py \
        web/src/duty_web/__main__.py web/tests/test_admin_app.py
git commit -m "feat(web): add localhost-only admin import app"
```

---

## Task 13: Deployment (Dockerfile, fly.toml, entrypoint, README)

**Files:**
- Create: `web/Dockerfile`
- Create: `web/fly.toml`
- Create: `web/entrypoint.sh`
- Create: `web/config.example.yaml` (documents required env vars; no secrets)
- Create: `web/README.md`

This task has no unit test — its deliverable is verified by the manual
deployment smoke test in the Verification section below. No code changes
to `duty_web` are needed; this only adds operational files.

- [ ] **Step 1: Write `web/Dockerfile`**

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

VOLUME /data

EXPOSE 8080

CMD ["./entrypoint.sh"]
```

- [ ] **Step 2: Write `web/entrypoint.sh`**

```bash
#!/bin/sh
set -e

# Admin import app: localhost only, never exposed via fly.toml's
# [http_service] (which only forwards the public port below).
python -m duty_web.admin_main &

exec python -m duty_web
```

- [ ] **Step 3: Write `web/fly.toml`**

```toml
# fly.toml app configuration file for the duty swap web app
app = 'duty-swap-webapp'
primary_region = 'arn'

[build]

[[mounts]]
  source = 'data'
  destination = '/data'

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = 'stop'
  auto_start_machines = true
  min_machines_running = 0

[[vm]]
  memory = '256mb'
  cpus = 1
  memory_mb = 256
```

Note: `[http_service].internal_port = 8080` is the only port Fly's proxy
forwards from the public internet — port 8081 (the admin app) is reachable
only from inside the Fly private network, i.e. via `fly ssh console` +
`curl localhost:8081/admin/import` from within that same machine, or
`fly proxy 8081` from an operator's own machine. This is what makes the
network-binding claim in the design spec true in practice.

- [ ] **Step 4: Write `web/config.example.yaml`**

```yaml
# Required environment variables (set as Fly secrets, never committed):
#   SECRET_KEY          — random, used to sign sessions and magic-link tokens
#   DB_PATH             — defaults to /data/duty.db
#   SCHEDULE_API_KEY    — shared secret for GET /api/schedule; also set as
#                          duty_mailer's SCHEDULE_API_KEY GitHub Secret
#   SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, FROM_ADDRESS
#                       — same Fastmail credentials duty_mailer already uses
```

- [ ] **Step 5: Write `web/README.md`**

```markdown
# Duty swap web app

Parent-facing schedule + swap app for the duty roster. See
`../docs/superpowers/specs/2026-09-12-duty-swap-webapp-design.md` for the
design.

## Local development

    cd web
    pip install -e ".[dev]"
    pytest
    SECRET_KEY=dev DB_PATH=:memory: SCHEDULE_API_KEY=dev \
      SMTP_HOST=localhost SMTP_PORT=587 SMTP_USER=u SMTP_PASSWORD=p \
      FROM_ADDRESS=noreply@exempel.se \
      python -m duty_web

## Seeding

    fly ssh sftp shell -a duty-swap-webapp
    > put schema.xlsx /tmp/schema.xlsx
    > exit
    fly ssh console -a duty-swap-webapp
    # inside the machine:
    curl -s -F team_id=1 -F file=@/tmp/schema.xlsx http://localhost:8081/admin/import

## Deploy

    fly deploy
```

- [ ] **Step 6: Commit**

```bash
git add web/Dockerfile web/fly.toml web/entrypoint.sh web/config.example.yaml web/README.md
git commit -m "feat(web): add Fly.io deployment files and README"
```

---

## Self-Review Notes

**Spec coverage:** hosting/D1 → Task 13; auth/D2 → Tasks 3-5; xlsx-as-seed/D3
→ Tasks 2, 12; swap scope+model/D4-D5 → Tasks 8-9; two views/D6 → Task 7;
no admin UI/D7 → Task 12 (import-only, no CRUD UI); data model → Task 1;
duty_mailer integration → Tasks 10-11; security baseline → Tasks 3-5
(tokens, cookies, rate limit) and app.py's CSRFProtect; seeding mechanism →
Task 12; deliberately-not-built items require no task by definition.

**Not covered by this plan, intentionally:** the final visual/CSS pass
against the approved mockup and a contrast/color review via
`/frontend-design:frontend-design` — the spec's own "Next step" section
flags this as a manual pass on top of the functional routes/templates
built here, not a task with its own test cycle. Do it once Tasks 1-13 are
merged and there's a real running app to point the tool at.

## Verification

1. **Unit tests, both packages:**
   ```bash
   cd web && pytest -v
   cd .. && pytest -v
   ```
   All pass, including the pre-existing 97 `duty_mailer` tests.

2. **End-to-end local run**, using the commands in `web/README.md`: seed a
   small xlsx via the admin app on port 8081, then as a browser: request a
   magic link at `/login`, copy the link from the server log/test SMTP
   capture, visit it, confirm `/` shows the seeded slot, confirm `/team`
   shows it too, propose a swap against a second seeded slot for a second
   person, log in as that second person, accept it, and confirm `/`
   now reflects the swapped slot for both accounts.

3. **duty_mailer integration**: with the web app running locally and
   `SCHEDULE_API_KEY` set to match, run `duty_mailer` with
   `schedule.source: web_api` and `schedule.url` pointing at the local
   `/api/schedule`, and confirm `python -m duty_mailer --dry-run` prints
   the seeded occurrence.

4. **Deployment smoke test** (manual, after `fly deploy`): confirm
   `curl https://<app>.fly.dev/admin/import` from outside the VM does
   **not** succeed (connection refused/timeout, not just a 404 — a 404
   would mean the route is reachable, just misspelled), while
   `fly ssh console` + the `curl localhost:8081/...` command in the README
   does.
