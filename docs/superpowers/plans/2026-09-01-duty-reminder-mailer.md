# Duty Reminder Mailer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily batch job that reads a duty roster, works out which groups are due a reminder, and emails them in Swedish.

**Architecture:** A single stateless CLI. Two I/O edges — a `ScheduleSource` port that reads the roster, and an SMTP sender — with pure functions between them holding all the logic. No database, no retries, no write access to the roster. Scheduled by GitHub Actions cron.

**Tech Stack:** Python 3.11+, `openpyxl` (xlsx), `requests` (CSV export), `PyYAML` (config), stdlib `smtplib`/`email` (sending), `pytest` (tests). Packaging with setuptools `src/` layout, mirroring `../charge-amps`.

**Spec:** `docs/superpowers/specs/2026-09-01-duty-reminder-mailer-design.md`

## Global Constraints

- **Python `>=3.11`.** Use `X | None` unions and `from __future__ import annotations`.
- **All user-facing text is Swedish** — email subjects and bodies, README, CLI output. Code, docstrings, identifiers, and commit messages are English. (Spec D6)
- **The roster is read-only.** No task may add write access to any source. (Spec D5)
- **No persistence.** No sent-log, no database, no state file, no cache. (Spec D2)
- **`today` is always derived in the configured timezone** (`Europe/Stockholm`), never from UTC and never with a bare `date.today()` in library code. (Spec, "Scheduling rules")
- **Nothing above `sources/` may reference rows, cells, columns, sheets, CSV, or HTTP.** (Spec, "Modules")
- **Swedish month and weekday names are hardcoded**, never `locale`-dependent — CI containers have no Swedish locale installed.
- **At most 2 lead times per occurrence.** (Spec D1)
- Secrets never appear in code or in `config.yaml`. `SMTP_PASSWORD` comes from the environment only.

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, dependencies |
| `.gitignore` | Ignore venv, env, caches, real roster/config |
| `src/duty_mailer/__init__.py` | Package marker |
| `src/duty_mailer/models.py` | `Role`, `Person`, `Occurrence`, `Message`. Plain data + construction invariants. |
| `src/duty_mailer/scheduling.py` | Pure date logic: what is due today, in which role, and who preceded it |
| `src/duty_mailer/templates.py` | Renders an `Occurrence` into a Swedish `Message` |
| `src/duty_mailer/config.py` | Loads and validates `config.yaml` |
| `src/duty_mailer/sources/__init__.py` | `ScheduleSource` protocol + `build_source()` factory |
| `src/duty_mailer/sources/rows.py` | Shared row→`Occurrence` normalization used by every tabular adapter |
| `src/duty_mailer/sources/xlsx.py` | `XlsxSource` — local `.xlsx` file |
| `src/duty_mailer/sources/google_csv.py` | `GoogleCsvSource` — Sheets CSV export URL |
| `src/duty_mailer/email_sender.py` | SMTP delivery of a `Message` |
| `src/duty_mailer/__main__.py` | CLI wiring |
| `tests/*.py` | One test module per source module |
| `.github/workflows/daily-reminders.yml` | Daily cron + manual trigger |
| `config.example.yaml`, `.env.example`, `README.md` | Operator documentation |

**Note on a spec key rename:** the spec's `schedule.sheet_link` is implemented as **`schedule.schedule_link`**. The roster may end up being an API rather than a sheet, and the config key should not outlive the assumption. Same value, source-neutral name.

---

### Task 1: Project scaffolding and domain models

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/duty_mailer/__init__.py`, `src/duty_mailer/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Role` (enum with `FORHANDSBESKED`, `PAMINNELSE`), `Person(email, name=None, external_id=None)` with `.display -> str`, `Occurrence(due, people, lead_days=(1,), key_location=None)`, `Message(to: tuple[str, ...], subject: str, body: str)`

**Why the invariants live in `Occurrence.__post_init__`:** the spec says invalid lead times are "rejected at parse time". Enforcing it at construction is strictly stronger — it makes an invalid `Occurrence` unrepresentable, so every adapter (including future ones nobody has written yet) gets the check for free without having to remember to call a validator.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "duty-mailer"
version = "0.1.0"
description = "Skickar påminnelser om kommande sysslor enligt ett schema"
requires-python = ">=3.11"
dependencies = [
    "openpyxl>=3.1",
    "pyyaml>=6.0",
    "requests>=2.31",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
*.egg-info/
.env
config.yaml
schema.xlsx
```

`config.yaml` and `schema.xlsx` are ignored because the real ones hold member email addresses. Only the `.example` files are committed.

- [ ] **Step 3: Write the failing test**

Create `tests/test_models.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from duty_mailer.models import Occurrence, Person, Role


def test_person_display_prefers_name():
    assert Person(email="a@x.se", name="Anna").display == "Anna"


def test_person_display_falls_back_to_email():
    assert Person(email="a@x.se").display == "a@x.se"


def test_occurrence_defaults_to_single_lead_day():
    occ = Occurrence(due=date(2026, 9, 19), people=(Person("a@x.se"),))
    assert occ.lead_days == (1,)


def test_occurrence_sorts_lead_days_descending():
    occ = Occurrence(
        due=date(2026, 9, 19), people=(Person("a@x.se"),), lead_days=(1, 7)
    )
    assert occ.lead_days == (7, 1)


def test_occurrence_rejects_more_than_two_lead_days():
    with pytest.raises(ValueError, match="högst 2"):
        Occurrence(
            due=date(2026, 9, 19), people=(Person("a@x.se"),), lead_days=(14, 7, 1)
        )


def test_occurrence_rejects_duplicate_lead_days():
    with pytest.raises(ValueError, match="dubblerade"):
        Occurrence(due=date(2026, 9, 19), people=(Person("a@x.se"),), lead_days=(7, 7))


def test_occurrence_rejects_non_positive_lead_days():
    with pytest.raises(ValueError, match="positiva"):
        Occurrence(due=date(2026, 9, 19), people=(Person("a@x.se"),), lead_days=(0,))


def test_occurrence_rejects_empty_people():
    with pytest.raises(ValueError, match="minst en"):
        Occurrence(due=date(2026, 9, 19), people=())


def test_roles_exist():
    assert {r.name for r in Role} == {"FORHANDSBESKED", "PAMINNELSE"}
```

The error messages are Swedish because they surface to the operator in the GitHub Actions log when a roster is malformed — that is user-facing text.

- [ ] **Step 4: Run the test to verify it fails**

Run: `python -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_mailer.models'`

- [ ] **Step 5: Write the implementation**

Create `src/duty_mailer/__init__.py` (empty file) and `src/duty_mailer/models.py`:

```python
"""Domain models.

Plain, frozen data. Construction enforces the invariants so that no other
layer has to re-check them, and so adapters cannot produce an invalid
Occurrence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

MAX_LEAD_TIMES = 2


class Role(Enum):
    """Which of the two messages an occurrence is getting."""

    FORHANDSBESKED = "forhandsbesked"
    PAMINNELSE = "paminnelse"


@dataclass(frozen=True)
class Person:
    email: str
    name: str | None = None
    external_id: str | None = None

    @property
    def display(self) -> str:
        """Name if we have one, otherwise the address."""
        return self.name or self.email


@dataclass(frozen=True)
class Occurrence:
    """One scheduled instance of the duty, handled by a group."""

    due: date
    people: tuple[Person, ...]
    lead_days: tuple[int, ...] = (1,)
    key_location: str | None = None

    def __post_init__(self) -> None:
        if not self.people:
            raise ValueError(f"{self.due}: varje tillfälle behöver minst en person")

        lead_days = tuple(self.lead_days)
        if len(lead_days) > MAX_LEAD_TIMES:
            raise ValueError(
                f"{self.due}: högst {MAX_LEAD_TIMES} påminnelser per tillfälle, "
                f"fick {lead_days}"
            )
        if len(set(lead_days)) != len(lead_days):
            raise ValueError(f"{self.due}: dubblerade antal dagar innan: {lead_days}")
        if any(d <= 0 for d in lead_days):
            raise ValueError(
                f"{self.due}: antal dagar innan måste vara positiva, fick {lead_days}"
            )

        # Descending order makes the largest offset (the heads-up) first,
        # which is the order the rest of the code reasons in.
        object.__setattr__(self, "lead_days", tuple(sorted(lead_days, reverse=True)))


@dataclass(frozen=True)
class Message:
    """A rendered email, ready to send."""

    to: tuple[str, ...]
    subject: str
    body: str
```

`object.__setattr__` is how a frozen dataclass mutates itself during `__post_init__`; normal assignment raises `FrozenInstanceError`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: PASS (9 tests)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore src/duty_mailer/__init__.py src/duty_mailer/models.py tests/test_models.py
git commit -m "feat: add domain models with construction invariants"
```

---

### Task 2: Scheduling logic

**Files:**
- Create: `src/duty_mailer/scheduling.py`
- Test: `tests/test_scheduling.py`

**Interfaces:**
- Consumes: `Occurrence`, `Role` from `duty_mailer.models`
- Produces:
  - `today_in(timezone: str) -> date`
  - `role_for(offset: int, lead_days: tuple[int, ...]) -> Role`
  - `previous_occurrence(occurrences: Sequence[Occurrence], occ: Occurrence, *, max_gap_days: int) -> Occurrence | None`
  - `due_on(occurrences: Sequence[Occurrence], today: date) -> list[tuple[Occurrence, Role]]`

This is the heart of the system and it is entirely pure — no I/O, no clock reads except the single explicit `today_in()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduling.py`:

```python
from __future__ import annotations

from datetime import date

from duty_mailer.models import Occurrence, Person, Role
from duty_mailer.scheduling import (
    due_on,
    previous_occurrence,
    role_for,
    today_in,
)


def occ(day: int, *, lead_days: tuple[int, ...] = (1,), month: int = 9) -> Occurrence:
    return Occurrence(
        due=date(2026, month, day),
        people=(Person("a@x.se", "Anna"),),
        lead_days=lead_days,
    )


# --- role_for -------------------------------------------------------------


def test_single_lead_time_is_always_the_nudge():
    assert role_for(7, (7,)) is Role.PAMINNELSE


def test_two_lead_times_split_by_size_not_by_value():
    assert role_for(14, (14, 2)) is Role.FORHANDSBESKED
    assert role_for(2, (14, 2)) is Role.PAMINNELSE


# --- due_on ---------------------------------------------------------------


def test_sends_nothing_when_no_occurrence_matches():
    assert due_on([occ(19)], date(2026, 9, 10)) == []


def test_sends_the_nudge_one_day_before():
    assert due_on([occ(19)], date(2026, 9, 18)) == [(occ(19), Role.PAMINNELSE)]


def test_does_not_send_on_the_day_itself():
    assert due_on([occ(19)], date(2026, 9, 19)) == []


def test_does_not_send_after_the_occurrence_has_passed():
    assert due_on([occ(19)], date(2026, 9, 20)) == []


def test_sends_the_heads_up_seven_days_before():
    o = occ(19, lead_days=(7, 1))
    assert due_on([o], date(2026, 9, 12)) == [(o, Role.FORHANDSBESKED)]


def test_sends_both_messages_on_their_own_days():
    o = occ(19, lead_days=(7, 1))
    assert due_on([o], date(2026, 9, 12)) == [(o, Role.FORHANDSBESKED)]
    assert due_on([o], date(2026, 9, 18)) == [(o, Role.PAMINNELSE)]


def test_returns_results_sorted_by_due_date():
    early, late = occ(19), occ(20)
    assert due_on([late, early], date(2026, 9, 18)) == [(early, Role.PAMINNELSE)]
    assert due_on([late, early], date(2026, 9, 19)) == [(late, Role.PAMINNELSE)]


def test_two_occurrences_can_be_due_on_the_same_day():
    a = occ(19, lead_days=(1,))
    b = occ(25, lead_days=(7,))
    result = due_on([a, b], date(2026, 9, 18))
    assert result == [(a, Role.PAMINNELSE), (b, Role.PAMINNELSE)]


# --- previous_occurrence --------------------------------------------------


def test_no_predecessor_for_the_first_occurrence():
    a, b = occ(12), occ(19)
    assert previous_occurrence([a, b], a, max_gap_days=8) is None


def test_finds_the_immediately_preceding_occurrence():
    a, b, c = occ(5), occ(12), occ(19)
    assert previous_occurrence([a, b, c], c, max_gap_days=8) == b


def test_predecessor_is_found_regardless_of_input_order():
    a, b = occ(12), occ(19)
    assert previous_occurrence([b, a], b, max_gap_days=8) == a


def test_predecessor_at_exactly_the_gap_limit_is_included():
    a, b = occ(11), occ(19)
    assert previous_occurrence([a, b], b, max_gap_days=8) == a


def test_predecessor_beyond_the_gap_limit_is_dropped():
    a, b = occ(10), occ(19)
    assert previous_occurrence([a, b], b, max_gap_days=8) is None


def test_predecessor_across_a_season_break_is_dropped():
    spring, autumn = occ(20, month=5), occ(5)
    assert previous_occurrence([spring, autumn], autumn, max_gap_days=8) is None


# --- today_in -------------------------------------------------------------


def test_today_in_returns_a_date():
    assert isinstance(today_in("Europe/Stockholm"), date)


def test_stockholm_is_ahead_of_utc_late_in_the_evening():
    # At 23:30 UTC on 2026-09-18 it is already 2026-09-19 in Stockholm.
    # A UTC-derived "today" would send the wrong day's reminders.
    from datetime import datetime, timezone

    from duty_mailer.scheduling import date_in_zone

    instant = datetime(2026, 9, 18, 23, 30, tzinfo=timezone.utc)
    assert date_in_zone(instant, "Europe/Stockholm") == date(2026, 9, 19)


def test_dst_change_does_not_shift_the_date():
    # Sweden leaves DST at 03:00 on 2026-10-25. 00:30 UTC is 02:30 local,
    # still the 25th.
    from datetime import datetime, timezone

    from duty_mailer.scheduling import date_in_zone

    instant = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    assert date_in_zone(instant, "Europe/Stockholm") == date(2026, 10, 25)
```

Note the tests rely on `Occurrence` being a frozen dataclass, so two separately-built occurrences with identical fields compare equal. That is what makes `occ(19) == occ(19)` work.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_scheduling.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_mailer.scheduling'`

- [ ] **Step 3: Write the implementation**

Create `src/duty_mailer/scheduling.py`:

```python
"""Pure scheduling logic.

The system keeps no record of what it has already sent (see spec D2), so
"should this go out?" is answered purely by arithmetic on dates. Everything
here is deterministic given an explicit `today`, which is what makes the
whole season replayable via `--date`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .models import Occurrence, Role


def date_in_zone(instant: datetime, timezone: str) -> date:
    """The calendar date at `instant`, as seen in `timezone`."""
    return instant.astimezone(ZoneInfo(timezone)).date()


def today_in(timezone: str) -> date:
    """Today's date in `timezone`.

    The only clock read in the library. The roster holds local calendar
    dates, so a UTC-derived date would be wrong either side of midnight.
    """
    return date_in_zone(datetime.now(tz=ZoneInfo("UTC")), timezone)


def role_for(offset: int, lead_days: tuple[int, ...]) -> Role:
    """Which message a given lead time carries.

    Decided by role rather than by literal value, so (7, 1), (14, 2) and
    (3,) all behave sensibly. A lone reminder is always the nudge; when
    there are two, the earlier one is the heads-up.
    """
    if len(lead_days) < 2:
        return Role.PAMINNELSE
    return Role.FORHANDSBESKED if offset == max(lead_days) else Role.PAMINNELSE


def due_on(
    occurrences: Sequence[Occurrence], today: date
) -> list[tuple[Occurrence, Role]]:
    """Every (occurrence, role) whose reminder falls exactly on `today`."""
    due: list[tuple[Occurrence, Role]] = []
    for occ in occurrences:
        days_left = (occ.due - today).days
        for offset in occ.lead_days:
            if days_left == offset:
                due.append((occ, role_for(offset, occ.lead_days)))
    return sorted(due, key=lambda pair: pair[0].due)


def previous_occurrence(
    occurrences: Sequence[Occurrence],
    occ: Occurrence,
    *,
    max_gap_days: int,
) -> Occurrence | None:
    """The occurrence immediately before `occ`, if it is close enough.

    Used to tell the incoming group who to collect keys from. Across a
    season break the previous group is not a meaningful handover, so
    anything further back than `max_gap_days` is treated as no predecessor
    at all.
    """
    earlier = [o for o in occurrences if o.due < occ.due]
    if not earlier:
        return None
    candidate = max(earlier, key=lambda o: o.due)
    if (occ.due - candidate.due).days > max_gap_days:
        return None
    return candidate
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_scheduling.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add src/duty_mailer/scheduling.py tests/test_scheduling.py
git commit -m "feat: add stateless scheduling and predecessor lookup"
```

---

### Task 3: Swedish email templates

**Files:**
- Create: `src/duty_mailer/templates.py`
- Test: `tests/test_templates.py`

**Interfaces:**
- Consumes: `Occurrence`, `Person`, `Role`, `Message` from `duty_mailer.models`
- Produces: `render(occ: Occurrence, role: Role, previous: Occurrence | None, *, chore: str, schedule_link: str | None) -> Message`; `format_date_sv(d: date) -> str`

**Why dates are formatted by hand:** `locale.setlocale(locale.LC_TIME, "sv_SE")` fails on the GitHub Actions runner, which has no Swedish locale generated. Hardcoded name tables are the only portable option and are trivially testable.

`previous` is passed in already filtered by `previous_occurrence()` — the template never decides adjacency, it only renders what it is given. That keeps the "keys only matter for consecutive weeks" rule in one place.

- [ ] **Step 1: Write the failing test**

Create `tests/test_templates.py`:

```python
from __future__ import annotations

from datetime import date

from duty_mailer.models import Occurrence, Person, Role
from duty_mailer.templates import format_date_sv, render

ANNA = Person("anna@x.se", "Anna Svensson")
BJORN = Person("bjorn@x.se", "Björn Ek")
CARINA = Person("carina@x.se", "Carina Lund")

THIS_WEEK = Occurrence(due=date(2026, 9, 19), people=(BJORN, CARINA), lead_days=(7, 1))
LAST_WEEK = Occurrence(due=date(2026, 9, 12), people=(ANNA,))


def test_format_date_sv():
    assert format_date_sv(date(2026, 9, 19)) == "lördag 19 september"


def test_format_date_sv_handles_every_month():
    months = [format_date_sv(date(2026, m, 1)).split()[-1] for m in range(1, 13)]
    assert months == [
        "januari", "februari", "mars", "april", "maj", "juni",
        "juli", "augusti", "september", "oktober", "november", "december",
    ]


def test_recipients_are_the_whole_group():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert msg.to == ("bjorn@x.se", "carina@x.se")


def test_nudge_subject_mentions_tomorrow():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert msg.subject == "Påminnelse — matchvärd i morgon"


def test_nudge_body_names_the_group():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert "Björn Ek" in msg.body
    assert "Carina Lund" in msg.body
    assert "lördag 19 september" in msg.body


def test_heads_up_subject_includes_the_date():
    msg = render(
        THIS_WEEK, Role.FORHANDSBESKED, None, chore="matchvärd", schedule_link=None
    )
    assert msg.subject == "Er tur snart — matchvärd lördag 19 september"


def test_heads_up_names_the_previous_group_when_adjacent():
    msg = render(
        THIS_WEEK, Role.FORHANDSBESKED, LAST_WEEK, chore="matchvärd", schedule_link=None
    )
    assert "Anna Svensson" in msg.body
    assert "anna@x.se" in msg.body


def test_heads_up_omits_the_handover_when_there_is_no_predecessor():
    msg = render(
        THIS_WEEK, Role.FORHANDSBESKED, None, chore="matchvärd", schedule_link=None
    )
    assert "nyckl" not in msg.body.lower()
    assert "förra" not in msg.body.lower()


def test_nudge_never_mentions_the_previous_group():
    # The handover belongs in the heads-up; by the day before it is noise.
    msg = render(
        THIS_WEEK, Role.PAMINNELSE, LAST_WEEK, chore="matchvärd", schedule_link=None
    )
    assert "Anna Svensson" not in msg.body


def test_schedule_link_is_included_when_configured():
    msg = render(
        THIS_WEEK,
        Role.PAMINNELSE,
        None,
        chore="matchvärd",
        schedule_link="https://example.com/schema",
    )
    assert "https://example.com/schema" in msg.body


def test_schedule_link_is_omitted_when_not_configured():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert "Schema:" not in msg.body


def test_people_without_names_show_their_address():
    occ = Occurrence(due=date(2026, 9, 19), people=(Person("x@x.se"),))
    msg = render(occ, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert "x@x.se" in msg.body


def test_body_has_no_leading_or_trailing_blank_lines():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert msg.body == msg.body.strip()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_templates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_mailer.templates'`

- [ ] **Step 3: Write the implementation**

Create `src/duty_mailer/templates.py`:

```python
"""Swedish email rendering.

The whole group goes in To: (spec D4) so that reply-all reaches everyone on
duty — coordinating the handover is the point of the heads-up message.
"""

from __future__ import annotations

from datetime import date

from .models import Message, Occurrence, Role

# Hardcoded rather than locale-derived: CI runners have no sv_SE locale.
MONTHS_SV = (
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
)
WEEKDAYS_SV = (
    "måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag",
)


def format_date_sv(d: date) -> str:
    """E.g. 'lördag 19 september'."""
    return f"{WEEKDAYS_SV[d.weekday()]} {d.day} {MONTHS_SV[d.month - 1]}"


def _names(occ: Occurrence) -> str:
    return ", ".join(p.display for p in occ.people)


def _contacts(occ: Occurrence) -> str:
    return ", ".join(
        f"{p.display} ({p.email})" if p.name else p.email for p in occ.people
    )


def render(
    occ: Occurrence,
    role: Role,
    previous: Occurrence | None,
    *,
    chore: str,
    schedule_link: str | None,
) -> Message:
    """Render one occurrence into a ready-to-send Swedish message.

    `previous` has already been filtered for adjacency by the caller; if it
    is not None, the handover line is shown.
    """
    when = format_date_sv(occ.due)
    lines: list[str] = []

    if role is Role.FORHANDSBESKED:
        subject = f"Er tur snart — {chore} {when}"
        lines.append(f"Hej! Ni står på tur för {chore} {when}.")
        lines.append("")
        lines.append(f"Denna gång: {_names(occ)}.")
        if previous is not None:
            lines.append("")
            if previous.key_location:
                lines.append(f"Nycklarna: {previous.key_location}")
            lines.append(
                f"Förra gången ({format_date_sv(previous.due)}) var det "
                f"{_contacts(previous)} — hör av er till dem om nycklar "
                "eller frågor."
            )
    else:
        subject = f"Påminnelse — {chore} i morgon"
        lines.append(f"Hej! I morgon, {when}, är det er tur för {chore}.")
        lines.append("")
        lines.append(f"Denna gång: {_names(occ)}.")

    if schedule_link:
        lines.append("")
        lines.append(f"Schema: {schedule_link}")

    return Message(
        to=tuple(p.email for p in occ.people),
        subject=subject,
        body="\n".join(lines).strip(),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_templates.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/duty_mailer/templates.py tests/test_templates.py
git commit -m "feat: add Swedish message templates"
```

---

### Task 4: Configuration

**Files:**
- Create: `src/duty_mailer/config.py`, `config.example.yaml`, `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ScheduleConfig`, `EmailConfig`, `Config`, `load_config(path: Path) -> Config`, `ConfigError`

`ScheduleConfig` fields: `source: str`, `path: str | None`, `url: str | None`, `default_lead_days: tuple[int, ...]`, `timezone: str`, `chore: str`, `schedule_link: str | None`, `max_handoff_gap_days: int`
`EmailConfig` fields: `smtp_host: str`, `smtp_port: int`, `smtp_user: str`, `from_address: str`, `reply_to: str | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
from __future__ import annotations

import textwrap

import pytest

from duty_mailer.config import ConfigError, load_config

FULL = """
schedule:
  source: xlsx
  path: schema.xlsx
  default_lead_days: [7, 1]
  timezone: Europe/Stockholm
  chore: matchvärd
  schedule_link: https://example.com/schema
  max_handoff_gap_days: 8

email:
  smtp_host: smtp.fastmail.com
  smtp_port: 587
  smtp_user: robot@example.com
  from_address: robot@example.com
  reply_to: styrelsen@example.com
"""

MINIMAL = """
schedule:
  source: xlsx
  path: schema.xlsx
  chore: matchvärd

email:
  smtp_host: smtp.fastmail.com
  smtp_port: 587
  smtp_user: robot@example.com
  from_address: robot@example.com
"""


def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_loads_a_full_config(tmp_path):
    cfg = load_config(write(tmp_path, FULL))
    assert cfg.schedule.source == "xlsx"
    assert cfg.schedule.default_lead_days == (7, 1)
    assert cfg.schedule.chore == "matchvärd"
    assert cfg.email.smtp_port == 587
    assert cfg.email.reply_to == "styrelsen@example.com"


def test_applies_defaults_for_optional_keys(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.schedule.default_lead_days == (1,)
    assert cfg.schedule.timezone == "Europe/Stockholm"
    assert cfg.schedule.max_handoff_gap_days == 8
    assert cfg.schedule.schedule_link is None
    assert cfg.email.reply_to is None


def test_rejects_an_unknown_source(tmp_path):
    bad = FULL.replace("source: xlsx", "source: carrier_pigeon")
    with pytest.raises(ConfigError, match="carrier_pigeon"):
        load_config(write(tmp_path, bad))


def test_rejects_xlsx_source_without_a_path(tmp_path):
    bad = MINIMAL.replace("  path: schema.xlsx\n", "")
    with pytest.raises(ConfigError, match="path"):
        load_config(write(tmp_path, bad))


def test_rejects_google_csv_source_without_a_url(tmp_path):
    bad = MINIMAL.replace("source: xlsx", "source: google_csv")
    with pytest.raises(ConfigError, match="url"):
        load_config(write(tmp_path, bad))


def test_rejects_more_than_two_default_lead_days(tmp_path):
    bad = FULL.replace("default_lead_days: [7, 1]", "default_lead_days: [14, 7, 1]")
    with pytest.raises(ConfigError, match="högst 2"):
        load_config(write(tmp_path, bad))


def test_rejects_a_missing_required_key(tmp_path):
    bad = FULL.replace("  chore: matchvärd\n", "")
    with pytest.raises(ConfigError, match="chore"):
        load_config(write(tmp_path, bad))


def test_rejects_a_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="hittades inte"):
        load_config(tmp_path / "nope.yaml")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_mailer.config'`

- [ ] **Step 3: Write the implementation**

Create `src/duty_mailer/config.py`:

```python
"""Config loading.

config.yaml is committed and holds no secrets; SMTP_PASSWORD comes from the
environment only. Validation happens here so a misconfiguration fails
immediately with a readable Swedish message in the Actions log, rather than
halfway through a send.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import MAX_LEAD_TIMES

VALID_SOURCES = ("xlsx", "google_csv")


class ConfigError(Exception):
    """Raised when config.yaml is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class ScheduleConfig:
    source: str
    chore: str
    path: str | None = None
    url: str | None = None
    default_lead_days: tuple[int, ...] = (1,)
    timezone: str = "Europe/Stockholm"
    schedule_link: str | None = None
    max_handoff_gap_days: int = 8


@dataclass(frozen=True)
class EmailConfig:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    from_address: str
    reply_to: str | None = None


@dataclass(frozen=True)
class Config:
    schedule: ScheduleConfig
    email: EmailConfig


def _require(section: dict[str, Any], key: str, where: str) -> Any:
    if key not in section or section[key] is None:
        raise ConfigError(f"Saknad inställning: {where}.{key}")
    return section[key]


def load_config(path: Path) -> Config:
    """Read and validate config.yaml."""
    if not path.is_file():
        raise ConfigError(f"Konfigurationsfilen hittades inte: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    schedule_raw = raw.get("schedule") or {}
    email_raw = raw.get("email") or {}

    source = _require(schedule_raw, "source", "schedule")
    if source not in VALID_SOURCES:
        raise ConfigError(
            f"Okänd källa '{source}'. Giltiga värden: {', '.join(VALID_SOURCES)}"
        )

    lead_days = tuple(schedule_raw.get("default_lead_days") or (1,))
    if len(lead_days) > MAX_LEAD_TIMES:
        raise ConfigError(
            f"schedule.default_lead_days: högst {MAX_LEAD_TIMES} påminnelser, "
            f"fick {list(lead_days)}"
        )

    schedule = ScheduleConfig(
        source=source,
        chore=_require(schedule_raw, "chore", "schedule"),
        path=schedule_raw.get("path"),
        url=schedule_raw.get("url"),
        default_lead_days=tuple(sorted(lead_days, reverse=True)),
        timezone=schedule_raw.get("timezone", "Europe/Stockholm"),
        schedule_link=schedule_raw.get("schedule_link"),
        max_handoff_gap_days=schedule_raw.get("max_handoff_gap_days", 8),
    )

    if schedule.source == "xlsx" and not schedule.path:
        raise ConfigError("schedule.path krävs när schedule.source är 'xlsx'")
    if schedule.source == "google_csv" and not schedule.url:
        raise ConfigError("schedule.url krävs när schedule.source är 'google_csv'")

    email = EmailConfig(
        smtp_host=_require(email_raw, "smtp_host", "email"),
        smtp_port=int(_require(email_raw, "smtp_port", "email")),
        smtp_user=_require(email_raw, "smtp_user", "email"),
        from_address=_require(email_raw, "from_address", "email"),
        reply_to=email_raw.get("reply_to"),
    )

    return Config(schedule=schedule, email=email)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Create `config.example.yaml`**

```yaml
schedule:
  source: xlsx                # xlsx | google_csv
  path: "schema.xlsx"
  # url: "https://docs.google.com/spreadsheets/d/<id>/export?format=csv"

  # Hur många dagar innan påminnelser skickas, om kalkylarket inte anger något.
  # Ett värde = bara påminnelse dagen innan. Två värden = förhandsbesked + påminnelse.
  default_lead_days: [1]

  timezone: "Europe/Stockholm"
  chore: "matchvärd"
  schedule_link: "https://docs.google.com/spreadsheets/d/<id>"

  # Nyckelöverlämning visas bara om förra tillfället låg inom så här många dagar.
  max_handoff_gap_days: 8

email:
  smtp_host: "smtp.fastmail.com"
  smtp_port: 587
  smtp_user: "robot@example.com"
  from_address: "robot@example.com"
  reply_to: "styrelsen@example.com"
```

- [ ] **Step 6: Create `.env.example`**

```bash
# SMTP-lösenord (app-lösenord från Fastmail)
SMTP_PASSWORD=
```

- [ ] **Step 7: Commit**

```bash
git add src/duty_mailer/config.py tests/test_config.py config.example.yaml .env.example
git commit -m "feat: add config loading and validation"
```

---

### Task 5: The `ScheduleSource` port and the xlsx adapter

**Files:**
- Create: `src/duty_mailer/sources/__init__.py`, `src/duty_mailer/sources/rows.py`, `src/duty_mailer/sources/xlsx.py`
- Test: `tests/test_rows.py`, `tests/test_xlsx_source.py`

**Interfaces:**
- Consumes: `Occurrence`, `Person` from `duty_mailer.models`; `ScheduleConfig` from `duty_mailer.config`
- Produces:
  - `ScheduleSource` Protocol with `fetch() -> list[Occurrence]`
  - `occurrences_from_rows(rows: Iterable[Mapping[str, Any]], *, default_lead_days: tuple[int, ...]) -> list[Occurrence]`
  - `parse_date(value: Any) -> date | None`
  - `XlsxSource(path: str | Path, *, default_lead_days: tuple[int, ...])`
  - `RosterError`

**The key design decision in this task.** Rather than writing separate "wide" and "tall" parsers, one algorithm handles both: for each row, take its date, then collect *every* email-bearing column in that row, and accumulate into a `dict[date, list[Person]]`. A wide row contributes several people at once; a tall row contributes one, and repeated dates merge. Neither layout is special-cased, which is why the spec can promise the sheet's eventual shape does not constrain the design.

`rows.py` is transport-agnostic — it sees dicts, not cells — so the CSV adapter in Task 6 reuses it wholesale. Only the "dicts out of a file" part differs between adapters.

- [ ] **Step 1: Write the failing test for row normalization**

Create `tests/test_rows.py`:

```python
from __future__ import annotations

from datetime import date, datetime

import pytest

from duty_mailer.sources.rows import RosterError, occurrences_from_rows, parse_date

DEFAULT = (1,)


def normalize(rows):
    return occurrences_from_rows(rows, default_lead_days=DEFAULT)


# --- parse_date -----------------------------------------------------------


def test_parse_date_accepts_iso_strings():
    assert parse_date("2026-09-19") == date(2026, 9, 19)


def test_parse_date_accepts_datetimes_from_excel():
    assert parse_date(datetime(2026, 9, 19, 0, 0)) == date(2026, 9, 19)


def test_parse_date_accepts_dates():
    assert parse_date(date(2026, 9, 19)) == date(2026, 9, 19)


def test_parse_date_ignores_blanks():
    assert parse_date(None) is None
    assert parse_date("   ") is None


def test_parse_date_rejects_gibberish():
    with pytest.raises(RosterError, match="datum"):
        parse_date("nästa lördag")


# --- layouts --------------------------------------------------------------


def test_wide_layout_gathers_every_email_column():
    occs = normalize([
        {"datum": "2026-09-19", "epost1": "a@x.se", "epost2": "b@x.se"},
    ])
    assert len(occs) == 1
    assert [p.email for p in occs[0].people] == ["a@x.se", "b@x.se"]


def test_tall_layout_merges_rows_sharing_a_date():
    occs = normalize([
        {"datum": "2026-09-19", "epost": "a@x.se"},
        {"datum": "2026-09-19", "epost": "b@x.se"},
    ])
    assert len(occs) == 1
    assert [p.email for p in occs[0].people] == ["a@x.se", "b@x.se"]


def test_ragged_wide_rows_are_fine():
    occs = normalize([
        {"datum": "2026-09-19", "epost1": "a@x.se", "epost2": "b@x.se"},
        {"datum": "2026-09-26", "epost1": "c@x.se", "epost2": None},
    ])
    assert [len(o.people) for o in occs] == [2, 1]


def test_names_are_paired_with_their_email_column():
    occs = normalize([
        {
            "datum": "2026-09-19",
            "epost1": "a@x.se", "namn1": "Anna",
            "epost2": "b@x.se", "namn2": "Björn",
        },
    ])
    assert [(p.email, p.name) for p in occs[0].people] == [
        ("a@x.se", "Anna"),
        ("b@x.se", "Björn"),
    ]


def test_results_are_sorted_by_date():
    occs = normalize([
        {"datum": "2026-09-26", "epost": "b@x.se"},
        {"datum": "2026-09-19", "epost": "a@x.se"},
    ])
    assert [o.due for o in occs] == [date(2026, 9, 19), date(2026, 9, 26)]


def test_duplicate_addresses_on_one_date_are_collapsed():
    occs = normalize([
        {"datum": "2026-09-19", "epost1": "a@x.se", "epost2": "a@x.se"},
    ])
    assert len(occs[0].people) == 1


def test_addresses_are_trimmed_and_lowercased():
    occs = normalize([{"datum": "2026-09-19", "epost": "  Anna@X.SE  "}])
    assert occs[0].people[0].email == "anna@x.se"


# --- optional columns -----------------------------------------------------


def test_lead_days_are_read_from_the_row():
    occs = normalize([{"datum": "2026-09-19", "epost": "a@x.se", "dagar_innan": "7,1"}])
    assert occs[0].lead_days == (7, 1)


def test_lead_days_accept_a_single_value():
    occs = normalize([{"datum": "2026-09-19", "epost": "a@x.se", "dagar_innan": "3"}])
    assert occs[0].lead_days == (3,)


def test_empty_lead_days_falls_back_to_the_default():
    occs = occurrences_from_rows(
        [{"datum": "2026-09-19", "epost": "a@x.se", "dagar_innan": ""}],
        default_lead_days=(7, 1),
    )
    assert occs[0].lead_days == (7, 1)


def test_key_location_is_read_when_present():
    occs = normalize([
        {"datum": "2026-09-19", "epost": "a@x.se", "nyckelplats": "hos Anna"},
    ])
    assert occs[0].key_location == "hos Anna"


def test_key_location_defaults_to_none():
    occs = normalize([{"datum": "2026-09-19", "epost": "a@x.se"}])
    assert occs[0].key_location is None


# --- tolerance and errors -------------------------------------------------


def test_header_casing_and_whitespace_are_ignored():
    occs = normalize([{"  Datum ": "2026-09-19", "EPOST1": "a@x.se"}])
    assert occs[0].due == date(2026, 9, 19)


def test_rows_without_a_date_are_skipped():
    occs = normalize([
        {"datum": None, "epost": "a@x.se"},
        {"datum": "2026-09-19", "epost": "b@x.se"},
    ])
    assert len(occs) == 1


def test_a_date_with_nobody_assigned_is_skipped():
    # A half-filled future row is normal in a real roster, not an error.
    occs = normalize([
        {"datum": "2026-09-19", "epost1": None},
        {"datum": "2026-09-26", "epost1": "a@x.se"},
    ])
    assert [o.due for o in occs] == [date(2026, 9, 26)]


def test_missing_date_column_is_an_error():
    with pytest.raises(RosterError, match="datum"):
        normalize([{"epost": "a@x.se"}])


def test_no_email_columns_at_all_is_an_error():
    with pytest.raises(RosterError, match="epost"):
        normalize([{"datum": "2026-09-19", "kommentar": "hej"}])


def test_an_invalid_lead_day_is_reported_with_its_date():
    with pytest.raises(RosterError, match="2026-09-19"):
        normalize([
            {"datum": "2026-09-19", "epost": "a@x.se", "dagar_innan": "14,7,1"},
        ])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_rows.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_mailer.sources'`

- [ ] **Step 3: Write the row normalizer**

Create `src/duty_mailer/sources/rows.py`:

```python
"""Tabular row -> Occurrence normalization.

Shared by every tabular adapter. Sees plain dicts, never cells or HTTP, so
adding a new tabular transport means writing "produce dicts" and nothing
else.

Wide and tall layouts are handled by one algorithm: each row contributes
however many people it happens to carry, keyed by its date. A wide row
contributes several at once; repeated dates in a tall sheet merge. Neither
shape is special-cased.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from ..models import Occurrence, Person

DATE_KEYS = ("datum", "date")
EMAIL_PREFIXES = ("epost", "e-post", "email")
NAME_PREFIXES = ("namn", "name")
LEAD_KEYS = ("dagar_innan", "lead_days")
KEY_LOCATION_KEYS = ("nyckelplats", "key_location")

_SUFFIX = re.compile(r"^(?P<prefix>[a-z\-_]+?)(?P<index>\d*)$")


class RosterError(Exception):
    """Raised when the roster cannot be read. Message is operator-facing."""


def _clean_headers(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k).strip().lower(): v for k, v in row.items() if k is not None}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_date(value: Any) -> date | None:
    """Parse a roster date cell. Blank means 'no date', not an error."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise RosterError(
            f"Kunde inte tolka datum: {text!r}. Använd formatet ÅÅÅÅ-MM-DD."
        ) from exc


def _parse_lead_days(value: Any, default: tuple[int, ...]) -> tuple[int, ...]:
    text = _text(value)
    if text is None:
        return default
    try:
        return tuple(int(part) for part in text.replace(" ", "").split(",") if part)
    except ValueError as exc:
        raise RosterError(
            f"Kunde inte tolka antal dagar innan: {text!r}. Använd t.ex. '7,1'."
        ) from exc


def _first(row: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _people_in_row(row: Mapping[str, Any]) -> list[Person]:
    """Every person this row carries, in column order.

    An email column may be bare ('epost') or numbered ('epost1'); a name
    column with the same suffix is paired with it.
    """
    found: list[tuple[int, Person]] = []
    for key, value in row.items():
        match = _SUFFIX.match(key)
        if match is None or match.group("prefix") not in EMAIL_PREFIXES:
            continue
        email = _text(value)
        if email is None:
            continue
        index = match.group("index")
        name = None
        for prefix in NAME_PREFIXES:
            name = _text(row.get(f"{prefix}{index}"))
            if name is not None:
                break
        # Sort numerically: lexicographically 'epost10' would precede 'epost2'.
        found.append((int(index) if index else 0, Person(email=email.lower(), name=name)))
    return [person for _, person in sorted(found, key=lambda pair: pair[0])]


def occurrences_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    default_lead_days: tuple[int, ...],
) -> list[Occurrence]:
    """Normalize tabular rows into sorted, validated occurrences."""
    people_by_date: dict[date, list[Person]] = {}
    extras: dict[date, tuple[Any, Any]] = {}
    saw_date_column = False
    saw_email_column = False

    for raw in rows:
        row = _clean_headers(raw)

        if any(key in row for key in DATE_KEYS):
            saw_date_column = True
        if any(
            (m := _SUFFIX.match(key)) and m.group("prefix") in EMAIL_PREFIXES
            for key in row
        ):
            saw_email_column = True

        due = parse_date(_first(row, DATE_KEYS))
        if due is None:
            continue

        people = people_by_date.setdefault(due, [])
        seen = {p.email for p in people}
        for person in _people_in_row(row):
            if person.email not in seen:
                seen.add(person.email)
                people.append(person)

        if due not in extras:
            extras[due] = (
                _first(row, LEAD_KEYS),
                _text(_first(row, KEY_LOCATION_KEYS)),
            )

    if not saw_date_column:
        raise RosterError(
            "Schemat saknar en datumkolumn (förväntade en kolumn som heter 'datum')."
        )
    if not saw_email_column:
        raise RosterError(
            "Schemat saknar e-postkolumner (förväntade 'epost', 'epost1', ...)."
        )

    occurrences: list[Occurrence] = []
    for due in sorted(people_by_date):
        people = people_by_date[due]
        if not people:
            # A future row with nobody assigned yet: normal, not an error.
            continue
        lead_raw, key_location = extras[due]
        try:
            occurrences.append(
                Occurrence(
                    due=due,
                    people=tuple(people),
                    lead_days=_parse_lead_days(lead_raw, default_lead_days),
                    key_location=key_location,
                )
            )
        except ValueError as exc:
            raise RosterError(str(exc)) from exc
    return occurrences
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rows.py -v`
Expected: PASS (23 tests)

- [ ] **Step 5: Write the failing test for the xlsx adapter**

Create `tests/test_xlsx_source.py`:

```python
from __future__ import annotations

from datetime import date, datetime

import pytest
from openpyxl import Workbook

from duty_mailer.sources.rows import RosterError
from duty_mailer.sources.xlsx import XlsxSource


def make_sheet(tmp_path, rows):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    path = tmp_path / "schema.xlsx"
    wb.save(path)
    return path


def test_reads_a_wide_sheet(tmp_path):
    path = make_sheet(tmp_path, [
        ["datum", "epost1", "namn1", "epost2", "namn2"],
        ["2026-09-19", "a@x.se", "Anna", "b@x.se", "Björn"],
        ["2026-09-26", "c@x.se", "Carina", None, None],
    ])
    occs = XlsxSource(path, default_lead_days=(1,)).fetch()
    assert [o.due for o in occs] == [date(2026, 9, 19), date(2026, 9, 26)]
    assert [len(o.people) for o in occs] == [2, 1]
    assert occs[0].people[0].name == "Anna"


def test_reads_native_excel_date_cells(tmp_path):
    path = make_sheet(tmp_path, [
        ["datum", "epost1"],
        [datetime(2026, 9, 19), "a@x.se"],
    ])
    occs = XlsxSource(path, default_lead_days=(1,)).fetch()
    assert occs[0].due == date(2026, 9, 19)


def test_reads_a_tall_sheet(tmp_path):
    path = make_sheet(tmp_path, [
        ["datum", "epost", "namn"],
        ["2026-09-19", "a@x.se", "Anna"],
        ["2026-09-19", "b@x.se", "Björn"],
    ])
    occs = XlsxSource(path, default_lead_days=(1,)).fetch()
    assert len(occs) == 1
    assert len(occs[0].people) == 2


def test_applies_the_default_lead_days(tmp_path):
    path = make_sheet(tmp_path, [["datum", "epost1"], ["2026-09-19", "a@x.se"]])
    occs = XlsxSource(path, default_lead_days=(7, 1)).fetch()
    assert occs[0].lead_days == (7, 1)


def test_fully_blank_rows_are_ignored(tmp_path):
    path = make_sheet(tmp_path, [
        ["datum", "epost1"],
        ["2026-09-19", "a@x.se"],
        [None, None],
    ])
    assert len(XlsxSource(path, default_lead_days=(1,)).fetch()) == 1


def test_a_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(RosterError, match="hittades inte"):
        XlsxSource(tmp_path / "nope.xlsx", default_lead_days=(1,)).fetch()


def test_an_empty_sheet_is_reported_clearly(tmp_path):
    path = make_sheet(tmp_path, [])
    with pytest.raises(RosterError, match="tomt"):
        XlsxSource(path, default_lead_days=(1,)).fetch()
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_xlsx_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_mailer.sources.xlsx'`

- [ ] **Step 7: Write the xlsx adapter and the port**

Create `src/duty_mailer/sources/xlsx.py`:

```python
"""Local .xlsx roster."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from ..models import Occurrence
from .rows import RosterError, occurrences_from_rows


class XlsxSource:
    """Reads the roster from the first worksheet of a local .xlsx file."""

    def __init__(self, path: str | Path, *, default_lead_days: tuple[int, ...]):
        self._path = Path(path)
        self._default_lead_days = default_lead_days

    def fetch(self) -> list[Occurrence]:
        if not self._path.is_file():
            raise RosterError(f"Schemafilen hittades inte: {self._path}")

        # read_only for large sheets; data_only so formula cells yield values.
        workbook = load_workbook(self._path, read_only=True, data_only=True)
        try:
            sheet = workbook.worksheets[0]
            rows = sheet.iter_rows(values_only=True)
            try:
                header = next(rows)
            except StopIteration:
                raise RosterError(f"Schemafilen är tomt: {self._path}") from None

            columns = [str(c).strip().lower() if c is not None else "" for c in header]
            if not any(columns):
                raise RosterError(f"Schemafilen är tomt: {self._path}")

            dicts = [
                {col: value for col, value in zip(columns, row) if col}
                for row in rows
                if any(value is not None for value in row)
            ]
        finally:
            workbook.close()

        return occurrences_from_rows(
            dicts, default_lead_days=self._default_lead_days
        )
```

Create `src/duty_mailer/sources/__init__.py`:

```python
"""Roster sources.

The rest of the application depends only on `ScheduleSource`. Nothing above
this package knows whether the roster is a spreadsheet, a CSV export, or an
API — which is what lets a 360Player adapter drop in later without touching
any other module.
"""

from __future__ import annotations

from typing import Protocol

from ..config import ScheduleConfig
from ..models import Occurrence
from .rows import RosterError

__all__ = ["RosterError", "ScheduleSource", "build_source"]


class ScheduleSource(Protocol):
    """Read-only access to the duty roster."""

    def fetch(self) -> list[Occurrence]:
        """All known occurrences. Never writes to the underlying source."""
        ...


def build_source(cfg: ScheduleConfig) -> ScheduleSource:
    """Construct the adapter named by the config."""
    if cfg.source == "xlsx":
        from .xlsx import XlsxSource

        assert cfg.path is not None  # guaranteed by config validation
        return XlsxSource(cfg.path, default_lead_days=cfg.default_lead_days)

    if cfg.source == "google_csv":
        from .google_csv import GoogleCsvSource

        assert cfg.url is not None  # guaranteed by config validation
        return GoogleCsvSource(cfg.url, default_lead_days=cfg.default_lead_days)

    raise RosterError(f"Okänd schemakälla: {cfg.source}")
```

`build_source` imports adapters lazily so a missing optional dependency for one adapter cannot break the other.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rows.py tests/test_xlsx_source.py -v`
Expected: PASS. `test_xlsx_source.py` will pass; `build_source` for `google_csv` is not exercised until Task 6.

- [ ] **Step 9: Commit**

```bash
git add src/duty_mailer/sources tests/test_rows.py tests/test_xlsx_source.py
git commit -m "feat: add ScheduleSource port and xlsx adapter"
```

---

### Task 6: Google Sheets CSV adapter

**Files:**
- Create: `src/duty_mailer/sources/google_csv.py`
- Test: `tests/test_google_csv_source.py`

**Interfaces:**
- Consumes: `occurrences_from_rows`, `RosterError` from `duty_mailer.sources.rows`
- Produces: `GoogleCsvSource(url: str, *, default_lead_days: tuple[int, ...], timeout: int = 30)`

All parsing is inherited from `rows.py`; this adapter's only job is turning an HTTP response into dicts. Tests fake the HTTP layer — no network access in the test suite.

Note the deployment question the spec leaves open: this adapter works with **no authentication at all** if the sheet is link-shared, but that exposes members' addresses to anyone with the URL. If a service account is chosen instead, it becomes a third adapter, not a change to this one.

- [ ] **Step 1: Write the failing test**

Create `tests/test_google_csv_source.py`:

```python
from __future__ import annotations

from datetime import date

import pytest
import requests

from duty_mailer.sources.google_csv import GoogleCsvSource
from duty_mailer.sources.rows import RosterError

CSV = "datum,epost1,namn1,epost2,namn2\n2026-09-19,a@x.se,Anna,b@x.se,Björn\n"

URL = "https://docs.google.com/spreadsheets/d/abc/export?format=csv"


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def test_parses_a_csv_export(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda url, timeout: FakeResponse(CSV)
    )
    occs = GoogleCsvSource(URL, default_lead_days=(1,)).fetch()
    assert [o.due for o in occs] == [date(2026, 9, 19)]
    assert [p.name for p in occs[0].people] == ["Anna", "Björn"]


def test_requests_the_configured_url(monkeypatch):
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        seen["timeout"] = timeout
        return FakeResponse(CSV)

    monkeypatch.setattr(requests, "get", fake_get)
    GoogleCsvSource(URL, default_lead_days=(1,), timeout=12).fetch()
    assert seen == {"url": URL, "timeout": 12}


def test_an_http_error_is_reported_clearly(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda url, timeout: FakeResponse("", status=404)
    )
    with pytest.raises(RosterError, match="Kunde inte hämta"):
        GoogleCsvSource(URL, default_lead_days=(1,)).fetch()


def test_a_network_failure_is_reported_clearly(monkeypatch):
    def boom(url, timeout):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(RosterError, match="Kunde inte hämta"):
        GoogleCsvSource(URL, default_lead_days=(1,)).fetch()


def test_a_login_page_instead_of_csv_is_reported_clearly(monkeypatch):
    # A sheet that is not link-shared returns an HTML sign-in page with 200.
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, timeout: FakeResponse("<!DOCTYPE html><html>Sign in</html>"),
    )
    with pytest.raises(RosterError, match="delad"):
        GoogleCsvSource(URL, default_lead_days=(1,)).fetch()
```

That last test earns its place: a sheet whose sharing was tightened returns HTTP 200 with an HTML login page, so without the check the failure would surface as a confusing "missing datum column" error rather than the real cause.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_google_csv_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_mailer.sources.google_csv'`

- [ ] **Step 3: Write the implementation**

Create `src/duty_mailer/sources/google_csv.py`:

```python
"""Roster read from a Google Sheets CSV export URL."""

from __future__ import annotations

import csv
import io

import requests

from ..models import Occurrence
from .rows import RosterError, occurrences_from_rows


class GoogleCsvSource:
    """Reads the roster from a published/link-shared Sheets CSV export."""

    def __init__(
        self,
        url: str,
        *,
        default_lead_days: tuple[int, ...],
        timeout: int = 30,
    ):
        self._url = url
        self._default_lead_days = default_lead_days
        self._timeout = timeout

    def fetch(self) -> list[Occurrence]:
        try:
            response = requests.get(self._url, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RosterError(f"Kunde inte hämta schemat: {exc}") from exc

        response.encoding = "utf-8"
        text = response.text

        if text.lstrip()[:9].lower().startswith("<!doctype") or text.lstrip().startswith(
            "<html"
        ):
            raise RosterError(
                "Fick en HTML-sida istället för CSV. Kontrollera att kalkylarket "
                "är delad med länk och att URL:en slutar med '/export?format=csv'."
            )

        rows = list(csv.DictReader(io.StringIO(text)))
        return occurrences_from_rows(
            rows, default_lead_days=self._default_lead_days
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_google_csv_source.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/duty_mailer/sources/google_csv.py tests/test_google_csv_source.py
git commit -m "feat: add Google Sheets CSV adapter"
```

---

### Task 7: SMTP sender

**Files:**
- Create: `src/duty_mailer/email_sender.py`
- Test: `tests/test_email_sender.py`

**Interfaces:**
- Consumes: `Message` from `duty_mailer.models`; `EmailConfig` from `duty_mailer.config`
- Produces: `send(message: Message, cfg: EmailConfig, *, password: str) -> None`, `SendError`

Adapted from `../charge-amps/src/charge_amps_hsb/email_sender.py`, simplified: `EmailMessage` rather than `MIMEMultipart`, since there are no attachments.

- [ ] **Step 1: Write the failing test**

Create `tests/test_email_sender.py`:

```python
from __future__ import annotations

import smtplib

import pytest

from duty_mailer import email_sender
from duty_mailer.config import EmailConfig
from duty_mailer.email_sender import SendError, send
from duty_mailer.models import Message

CFG = EmailConfig(
    smtp_host="smtp.example.com",
    smtp_port=587,
    smtp_user="robot@example.com",
    from_address="robot@example.com",
    reply_to="styrelsen@example.com",
)

MSG = Message(
    to=("a@x.se", "b@x.se"), subject="Påminnelse — matchvärd i morgon", body="Hej!"
)


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.started_tls = False
        self.login_args = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_connects_with_starttls_and_logs_in(smtp):
    send(MSG, CFG, password="hemligt")
    server = smtp.instances[0]
    assert (server.host, server.port) == ("smtp.example.com", 587)
    assert server.started_tls is True
    assert server.login_args == ("robot@example.com", "hemligt")


def test_sends_to_the_whole_group(smtp):
    send(MSG, CFG, password="hemligt")
    sent = smtp.instances[0].sent[0]
    assert sent["To"] == "a@x.se, b@x.se"
    assert sent["From"] == "robot@example.com"
    assert sent["Subject"] == "Påminnelse — matchvärd i morgon"
    assert sent["Reply-To"] == "styrelsen@example.com"
    assert sent.get_content().strip() == "Hej!"


def test_omits_reply_to_when_not_configured(smtp):
    cfg = EmailConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="robot@example.com",
        from_address="robot@example.com",
    )
    send(MSG, cfg, password="hemligt")
    assert smtp.instances[0].sent[0]["Reply-To"] is None


def test_rejects_an_empty_password():
    with pytest.raises(SendError, match="SMTP_PASSWORD"):
        send(MSG, CFG, password="")


def test_smtp_failures_are_wrapped(monkeypatch):
    def boom(host, port):
        raise smtplib.SMTPAuthenticationError(535, b"nope")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    with pytest.raises(SendError, match="Kunde inte skicka"):
        send(MSG, CFG, password="hemligt")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_email_sender.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duty_mailer.email_sender'`

- [ ] **Step 3: Write the implementation**

Create `src/duty_mailer/email_sender.py`:

```python
"""SMTP delivery.

Stdlib only, STARTTLS on port 587 — the same shape as the sender already in
production in ../charge-amps, minus the attachment handling.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import EmailConfig
from .models import Message


class SendError(Exception):
    """Raised when a message could not be delivered."""


def send(message: Message, cfg: EmailConfig, *, password: str) -> None:
    """Send one message to its whole group in a single SMTP transaction."""
    if not password:
        raise SendError(
            "SMTP-lösenord saknas: sätt miljövariabeln SMTP_PASSWORD."
        )

    email = EmailMessage()
    email["From"] = cfg.from_address
    email["To"] = ", ".join(message.to)
    email["Subject"] = message.subject
    if cfg.reply_to:
        email["Reply-To"] = cfg.reply_to
    email.set_content(message.body)

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
            server.starttls()
            server.login(cfg.smtp_user, password)
            server.send_message(email)
    except (smtplib.SMTPException, OSError) as exc:
        raise SendError(f"Kunde inte skicka till {email['To']}: {exc}") from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_email_sender.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/duty_mailer/email_sender.py tests/test_email_sender.py
git commit -m "feat: add SMTP sender"
```

---

### Task 8: CLI

**Files:**
- Create: `src/duty_mailer/__main__.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7
- Produces: `main(argv: list[str] | None = None) -> int`, `run(cfg: Config, *, today: date, dry_run: bool, password: str, source: ScheduleSource) -> int`

`run()` takes its collaborators as arguments so the whole pipeline is testable without touching the filesystem, the network, or SMTP. `main()` is the only place that reads `argv`, the environment, and the clock.

**Exit codes:** `0` success (including "nothing to send"), `1` a handled error (bad config, unreadable roster, send failure) reported in Swedish without a traceback.

**On partial send failure:** if one message fails, the remaining ones are still attempted and the process exits `1`. Abandoning the rest would silently punish groups that had nothing to do with the failure, and with no retry state (spec D2) a skipped message is never recovered.

- [ ] **Step 1: Write the failing test**

Create `tests/test_main.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from duty_mailer.__main__ import main, run
from duty_mailer.config import Config, EmailConfig, ScheduleConfig
from duty_mailer.email_sender import SendError
from duty_mailer.models import Occurrence, Person

CFG = Config(
    schedule=ScheduleConfig(
        source="xlsx",
        chore="matchvärd",
        path="schema.xlsx",
        default_lead_days=(7, 1),
        timezone="Europe/Stockholm",
        schedule_link="https://example.com/schema",
        max_handoff_gap_days=8,
    ),
    email=EmailConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="robot@example.com",
        from_address="robot@example.com",
    ),
)

LAST_WEEK = Occurrence(date(2026, 9, 12), (Person("a@x.se", "Anna"),), (7, 1))
THIS_WEEK = Occurrence(date(2026, 9, 19), (Person("b@x.se", "Björn"),), (7, 1))


class FakeSource:
    def __init__(self, occurrences):
        self._occurrences = occurrences

    def fetch(self):
        return list(self._occurrences)


@pytest.fixture
def sent(monkeypatch):
    box = []
    monkeypatch.setattr(
        "duty_mailer.__main__.send",
        lambda msg, cfg, password: box.append(msg),
    )
    return box


def test_sends_nothing_when_nothing_is_due(sent):
    code = run(
        CFG,
        today=date(2026, 9, 15),
        dry_run=False,
        password="x",
        source=FakeSource([LAST_WEEK, THIS_WEEK]),
    )
    assert code == 0
    assert sent == []


def test_sends_the_nudge_the_day_before(sent):
    code = run(
        CFG,
        today=date(2026, 9, 18),
        dry_run=False,
        password="x",
        source=FakeSource([LAST_WEEK, THIS_WEEK]),
    )
    assert code == 0
    assert [m.to for m in sent] == [("b@x.se",)]
    assert "i morgon" in sent[0].subject


def test_heads_up_includes_the_previous_group(sent):
    run(
        CFG,
        today=date(2026, 9, 12),
        dry_run=False,
        password="x",
        source=FakeSource([LAST_WEEK, THIS_WEEK]),
    )
    assert "Anna" in sent[0].body


def test_dry_run_sends_nothing(sent, capsys):
    code = run(
        CFG,
        today=date(2026, 9, 18),
        dry_run=True,
        password="x",
        source=FakeSource([LAST_WEEK, THIS_WEEK]),
    )
    assert code == 0
    assert sent == []
    assert "b@x.se" in capsys.readouterr().out


def test_a_failed_send_does_not_stop_the_others(monkeypatch, capsys):
    attempted = []

    def flaky(msg, cfg, password):
        attempted.append(msg.to)
        if msg.to == ("a@x.se",):
            raise SendError("nope")

    monkeypatch.setattr("duty_mailer.__main__.send", flaky)
    both = [
        Occurrence(date(2026, 9, 19), (Person("a@x.se"),), (1,)),
        Occurrence(date(2026, 9, 19), (Person("b@x.se"),), (1,)),
    ]
    # Two occurrences on the same date cannot come from one roster, so build
    # the source directly.
    code = run(
        CFG, today=date(2026, 9, 18), dry_run=False, password="x",
        source=FakeSource(both),
    )
    assert code == 1
    assert attempted == [("a@x.se",), ("b@x.se",)]


def test_main_reports_a_bad_config_without_a_traceback(tmp_path, capsys):
    code = main(["--config", str(tmp_path / "nope.yaml")])
    assert code == 1
    assert "hittades inte" in capsys.readouterr().err


def test_main_rejects_a_malformed_date(tmp_path):
    with pytest.raises(SystemExit):
        main(["--config", str(tmp_path / "nope.yaml"), "--date", "igår"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: FAIL — `ImportError: cannot import name 'main'`

- [ ] **Step 3: Write the implementation**

Create `src/duty_mailer/__main__.py`:

```python
"""CLI entry point.

`run()` receives its collaborators as arguments so the pipeline can be
tested without a filesystem, a network, or an SMTP server. `main()` is the
only place that reads argv, the environment, and the clock.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import dataclasses

from .config import Config, ConfigError, load_config
from .email_sender import SendError, send
from .scheduling import due_on, previous_occurrence, today_in
from .sources import RosterError, ScheduleSource, build_source
from .templates import render


def _with_sheet_override(cfg: Config, path: str) -> Config:
    """Config with the roster forced to a local xlsx file (--sheet)."""
    return Config(
        schedule=dataclasses.replace(cfg.schedule, source="xlsx", path=path),
        email=cfg.email,
    )


def run(
    cfg: Config,
    *,
    today: date,
    dry_run: bool,
    password: str,
    source: ScheduleSource,
) -> int:
    """Fetch, decide, render and send. Returns a process exit code."""
    occurrences = source.fetch()
    pending = due_on(occurrences, today)

    if not pending:
        print(f"{today}: inga påminnelser att skicka.")
        return 0

    failures = 0
    for occ, role in pending:
        previous = previous_occurrence(
            occurrences, occ, max_gap_days=cfg.schedule.max_handoff_gap_days
        )
        message = render(
            occ,
            role,
            previous,
            chore=cfg.schedule.chore,
            schedule_link=cfg.schedule.schedule_link,
        )

        if dry_run:
            print(f"--- {role.value} -> {', '.join(message.to)}")
            print(f"Ämne: {message.subject}")
            print(message.body)
            print()
            continue

        try:
            send(message, cfg.email, password=password)
            print(f"Skickade {role.value} till {', '.join(message.to)}")
        except SendError as exc:
            # Keep going: with no retry state, skipping the rest would
            # silently drop reminders for unrelated groups.
            print(f"FEL: {exc}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="duty_mailer",
        description="Skickar påminnelser om kommande sysslor.",
    )
    parser.add_argument(
        "--config", default="config.yaml", help="Sökväg till config.yaml"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Visa vad som skulle skickas, skicka inget",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Kör som om det vore detta datum (ÅÅÅÅ-MM-DD)",
    )
    parser.add_argument(
        "--sheet", help="Åsidosätt schedule.path från konfigurationen"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cfg = load_config(Path(args.config))
        if args.sheet:
            cfg = _with_sheet_override(cfg, args.sheet)
        today = args.date or today_in(cfg.schedule.timezone)
        source = build_source(cfg.schedule)
        password = os.environ.get("SMTP_PASSWORD", "")
        return run(
            cfg,
            today=today,
            dry_run=args.dry_run,
            password=password,
            source=source,
        )
    except (ConfigError, RosterError, SendError) as exc:
        print(f"FEL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: PASS, all tests from Tasks 1–8.

- [ ] **Step 6: Commit**

```bash
git add src/duty_mailer/__main__.py tests/test_main.py
git commit -m "feat: add CLI"
```

---

### Task 9: Scheduled deployment and operator docs

**Files:**
- Create: `.github/workflows/daily-reminders.yml`, `README.md`

**Interfaces:**
- Consumes: the `duty_mailer` CLI
- Produces: nothing consumed by other tasks

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/daily-reminders.yml`:

```yaml
name: Dagliga påminnelser

on:
  schedule:
    # 06:00 UTC = 08:00 svensk sommartid, 07:00 vintertid.
    - cron: "0 6 * * *"
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Visa vad som skulle skickas, skicka inget"
        type: boolean
        default: true
      date:
        description: "Kör som om det vore detta datum (ÅÅÅÅ-MM-DD)"
        type: string
        default: ""

jobs:
  remind:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Installera
        run: pip install --no-cache-dir .

      - name: Skicka påminnelser
        env:
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
        run: |
          python -m duty_mailer \
            ${{ inputs.dry_run && '--dry-run' || '' }} \
            ${{ inputs.date && format('--date {0}', inputs.date) || '' }}
```

Note `workflow_dispatch` defaults `dry_run` to true: a manual run is nearly always someone checking what would happen, and defaulting to sending real mail to real people is the wrong default for a button that is easy to press by accident.

- [ ] **Step 2: Verify the workflow parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/daily-reminders.yml'))" && echo OK`
Expected: `OK`

- [ ] **Step 3: Write `README.md`**

```markdown
# Påminnelser om sysslor

Skickar automatiskt e-postpåminnelser till de som står på tur enligt schemat.

## Hur det fungerar

Varje morgon kör ett schemalagt jobb på GitHub Actions:

1. Schemat läses (Excel-fil eller Google Kalkylark)
2. Jobbet räknar ut vilka tillfällen som är på gång
3. Ett mejl skickas till hela gruppen som står på tur

Två sorters mejl kan skickas per tillfälle:

- **Förhandsbesked** (t.ex. 7 dagar innan) — vilka som står på tur, och vem
  man hämtar nycklar hos om förra gruppen var veckan innan.
- **Påminnelse** (1 dag innan) — kort påminnelse om att det är er tur i morgon.

Hur många dagar innan styrs per rad i schemat (kolumnen `dagar_innan`, t.ex.
`7,1`). Är den tom används `default_lead_days` från `config.yaml`.

## Schemats format

| Kolumn | Krävs | Beskrivning |
|---|---|---|
| `datum` | ja | ÅÅÅÅ-MM-DD |
| `epost1`, `epost2`, … | ja | En kolumn per person, eller en `epost`-kolumn med en rad per person |
| `namn1`, `namn2`, … | nej | Namn som visas i mejlet |
| `dagar_innan` | nej | T.ex. `7,1`. Max två värden. |
| `nyckelplats` | nej | Var nyckeln finns efter passet |

Både "brett" format (en rad per datum, flera e-postkolumner) och "högt" format
(en rad per person, samma datum upprepat) fungerar.

## Köra manuellt

```bash
python -m duty_mailer --dry-run                  # visa, skicka inget
python -m duty_mailer --date 2026-09-18          # kör som ett visst datum
python -m duty_mailer --date 2026-09-18 --dry-run  # vanligaste kombinationen
python -m duty_mailer                            # skarpt läge
```

`--date` tillsammans med `--dry-run` är hur man kontrollerar vad som kommer att
skickas längre fram, utan att skicka något.

Man kan också köra jobbet från GitHub: **Actions → Dagliga påminnelser → Run
workflow** (torrkörning är förvald).

## Konfiguration

Kopiera `config.example.yaml` till `config.yaml` och fyll i. Filen innehåller
inga lösenord.

## Hemligheter

| Secret | Beskrivning |
|---|---|
| `SMTP_PASSWORD` | App-lösenord för e-postkontot |

Lagras som GitHub Secret (Settings → Secrets and variables → Actions). För lokal
körning, se `.env.example`.

## Att känna till

- **Inget skickas i efterhand.** Jobbet håller ingen historik; det jämför bara
  dagens datum mot schemat. Om en körning missas skickas det mejlet aldrig.
- **GitHub stänger av schemalagda jobb efter 60 dagars inaktivitet** i
  repot. En commit eller en manuell körning återaktiverar dem.
- **Schemat läses bara, aldrig skrivs till.**
```

- [ ] **Step 4: Run the whole suite one final time**

Run: `.venv/bin/pytest -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/daily-reminders.yml README.md
git commit -m "feat: add scheduled workflow and operator docs"
```

---

## Verification

After Task 9, verify end to end without sending anything:

- [ ] Build a small `schema.xlsx` by hand with three future dates and two or three addresses each
- [ ] `cp config.example.yaml config.yaml` and point `schedule.path` at it
- [ ] `python -m duty_mailer --dry-run --date <7 days before the second date>` — expect a `forhandsbesked` naming the first group as predecessor
- [ ] `python -m duty_mailer --dry-run --date <1 day before the second date>` — expect a `paminnelse` with no predecessor mentioned
- [ ] `python -m duty_mailer --dry-run --date <a date with nothing due>` — expect "inga påminnelser att skicka"
- [ ] With `SMTP_PASSWORD` set and a roster containing only your own address, run without `--dry-run` and confirm the mail arrives

## Open questions for the operator

Neither blocks implementation; both are deployment decisions from the spec.

1. **Google Sheets access model** — link-shared CSV (no auth, but the URL exposes members' addresses) vs. a service account (one more secret, private). The spec recommends the service account. If chosen, it is a new adapter alongside `google_csv.py`, not a change to it.
2. **360Player API** — feasibility unverified. If it works out it becomes `sources/player360.py` implementing the same `ScheduleSource` protocol, and no module outside `sources/` changes.
