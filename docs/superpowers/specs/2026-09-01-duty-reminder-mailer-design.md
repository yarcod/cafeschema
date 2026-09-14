# Duty reminder mailer — design

**Date:** 2026-09-01
**Status:** Approved design, not yet implemented
**Repo:** `360-mailer`
**Package:** `duty_mailer`

## Problem

A recurring duty (e.g. matchvärd/kiosk on match weekends) is scheduled ahead of
time, currently in a spreadsheet. Each occurrence is handled by a group of 2–8
people. Nobody is reminded today; people forget whose turn it is.

Send them email reminders ahead of each occurrence, automatically.

## Scope

Send up to two emails per occurrence, to the whole assigned group, on a daily
schedule. Read the roster from wherever it happens to live. Nothing else.

Explicitly out of scope: see [Deliberately not built](#deliberately-not-built).

## Decisions

Each of these was settled during brainstorming; the reasoning is recorded so a
later reader knows which constraints are load-bearing.

### D1 — Up to two reminders per occurrence, with distinct content

Not an escalating series of identical nags. Two different messages:

| Role | Typical lead | Purpose |
|---|---|---|
| `FORHANDSBESKED` | 7 days | Heads-up. Who is on duty, and — when applicable — where the keys are / who to ask. |
| `PAMINNELSE` | 1 day | Short nudge. It is your turn tomorrow. |

Lead times are configurable per occurrence, defaulting to `[1]` (nudge only) when
the source supplies nothing. At most two per occurrence.

### D2 — Stateless date math, no persistence

The job sends when `due - today == offset`. There is no sent-log, no database, no
write-back to the source.

**Accepted consequence:** a missed run is a silently missed email with no
catch-up, and a double run sends duplicates. This was chosen deliberately —
recipients currently get no reminders at all, so the downside of an occasional
miss is small, and avoiding state also avoids ever needing write access to the
roster.

This makes **scheduler reliability the system's reliability**. See D3.

### D3 — GitHub Actions cron + Fastmail SMTP

Reuses the pattern already running in production in `../charge-amps`
(`monthly-report.yml`): scheduled workflow, secrets in GitHub Secrets, stdlib
`smtplib` with STARTTLS.

GitHub's scheduled runs can be delayed and are occasionally dropped, which is the
one failure mode D2 cannot absorb. Accepted: the same tradeoff is already taken
in `charge-amps` for a monthly regulatory report, where a miss matters more than
a missed duty reminder.

Note: GitHub disables scheduled workflows after 60 days of repository inactivity.
A commit — or a manual `workflow_dispatch` — resets that.

### D4 — One email per occurrence, whole group in `To:`

Not individual emails. The group can see each other and reply-all to coordinate,
which is precisely what the heads-up email exists to enable. It is also a single
SMTP transaction per occurrence rather than up to eight, which matters when there
is no retry state (D2) — a partial failure mid-loop would otherwise leave some
people mailed and some not, permanently.

Cost: no personalized greeting, and members see each other's addresses. The
group is instead named in the body.

### D5 — The source is read-only, always

The roster is never written to. A future idea — participants updating a "where
the key is now" field after their shift, which the next email reads back —
*keeps* this property: humans do the writing, the system only reads. No design
should trade this away for convenience.

### D6 — Swedish user-facing text

All email copy, the README, and CLI output are Swedish. Code, docstrings, and
identifiers are English. Matches `charge-amps`.

## Architecture

```
GitHub Actions (cron, daily)
        │
        ▼
  python -m duty_mailer
        │
        ├── ScheduleSource.fetch()      ──►  list[Occurrence]      (I/O, read-only)
        ├── due_on(occurrences, today)  ──►  [(Occurrence, Role)]  (pure)
        ├── render(occ, role, previous) ──►  Message               (pure)
        └── send(message)               ──►  SMTP                  (I/O)
```

Two I/O edges, both behind seams. Everything between them is pure functions over
plain data, which is where the logic and the tests live.

### Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `models.py` | `Person`, `Occurrence`, `Role`. Plain data. | — |
| `sources/` | `ScheduleSource` port + adapters | `models` |
| `scheduling.py` | Which occurrences are due today, in which role; predecessor lookup | `models` |
| `templates.py` | Render a `Message` (Swedish) | `models` |
| `email_sender.py` | SMTP delivery | — |
| `config.py` | YAML + env loading, validation | — |
| `__main__.py` | CLI wiring | all |

Nothing above `sources/` may know about rows, cells, columns, sheets, or HTTP.

## Data model

```python
@dataclass(frozen=True)
class Person:
    email: str
    name: str | None = None
    external_id: str | None = None   # e.g. 360Player member id, if a source has one

@dataclass(frozen=True)
class Occurrence:
    due: date
    people: tuple[Person, ...]           # expected 2–8
    lead_days: tuple[int, ...] = (1,)    # from source, else config default
    key_location: str | None = None      # reserved; read but unused for now
```

`Occurrence.due` is a **local calendar date**, never a datetime.

## The `ScheduleSource` port

```python
class ScheduleSource(Protocol):
    def fetch(self) -> list[Occurrence]: ...
```

One method, no arguments, returns fully-formed domain objects. Adapters own all
knowledge of their transport and shape; parsing, normalization, and validation
happen inside the adapter so that a malformed roster fails at the boundary rather
than halfway through rendering.

### Planned adapters

- **`XlsxSource(path)`** — `openpyxl` over a local file. Built first; makes the
  whole pipeline testable and runnable before any hosted roster exists.
- **`GoogleCsvSource(url)`** — `requests` against a Sheets CSV export URL.
- **`Player360Source(...)`** — if 360Player exposes a usable API, this may
  replace the spreadsheet entirely. Its feasibility is unverified and must be
  checked before it is planned in.

Adding an adapter must require no change above `sources/`. That constraint is the
main reason this port exists, given the roster's home is genuinely undecided.

### Assumed spreadsheet columns

The real sheet does not exist yet. These names are **provisional** and expected
to change; the adapter maps them to the domain model and is the only place that
must change when they do.

| Column | Required | Notes |
|---|---|---|
| `datum` | yes | ISO `YYYY-MM-DD` preferred; Excel date cells accepted |
| `epost1..epostN` | yes | Wide layout, ragged rows allowed |
| `namn1..namnN` | no | Paired with the email columns |
| `dagar_innan` | no | e.g. `7,1`. Empty ⇒ config default |
| `nyckelplats` | no | Read into `key_location`, unused for now |

The adapter normalizes both **wide** (one row per date, several email columns) and
**tall** (several rows sharing a date) layouts into one `Occurrence` per date, so
the sheet's eventual shape does not constrain the design.

## Scheduling rules

```python
for occ in occurrences:
    for offset in occ.lead_days:
        if (occ.due - today).days == offset:
            emit(occ, role_for(offset, occ.lead_days))
```

**`today` is computed in `Europe/Stockholm`**, not UTC. The roster holds local
dates; deriving "today" from UTC would misfire near midnight and across DST
boundaries.

### Role assignment

By role, not by literal offset value, so `(7, 1)`, `(14, 2)` and `(3,)` all behave
sensibly:

- one offset → `PAMINNELSE`
- two offsets → larger is `FORHANDSBESKED`, smaller is `PAMINNELSE`
- three or more → rejected at parse time with an error naming the offending
  occurrence (D1 caps this at two)

### Predecessor lookup

The heads-up email may reference the previous group (keys, questions). The
predecessor is the occurrence with the greatest `due` strictly before this one.

It is included **only when the two are adjacent** — `gap <= max_handoff_gap_days`
(default 8). A key handoff is meaningless across a season break. The first
occurrence in the roster, and the first after any gap, simply omit the section
rather than rendering an empty one.

## Email templates

Sketch only — to be reworked once the real roster and duty wording exist.

**Förhandsbesked**

> **Ämne:** Er tur snart — {chore} {date}
>
> Hej! Ni står på tur för {chore} {date}.
> Denna gång: Anna Svensson, Björn Ek, Carina Lund.
> *(om föregående tillfälle var intilliggande)* Nycklarna finns hos förra gruppen: Anna Svensson (anna@…).
> Schema: {schedule_link}

**Påminnelse**

> **Ämne:** Påminnelse — {chore} i morgon
>
> Hej! I morgon, {date}, är det er tur: Anna, Björn, Carina.
> Schema: {schedule_link}

Plain text. People without a `name` are shown by email address.

## Configuration

`config.yaml` is committed (it holds no secrets). `SMTP_PASSWORD` is a GitHub
Secret. Unlike `charge-amps`, there is no Fly.io volume and no web config editor —
nothing here needs runtime-editable config.

```yaml
schedule:
  source: xlsx                  # xlsx | google_csv
  path: "schema.xlsx"
  # url: "https://docs.google.com/.../export?format=csv"
  default_lead_days: [1]
  timezone: "Europe/Stockholm"
  chore: "matchvärd"
  sheet_link: "https://docs.google.com/spreadsheets/d/..."
  max_handoff_gap_days: 8

email:
  smtp_host: "smtp.fastmail.com"
  smtp_port: 587
  smtp_user: "..."
  from_address: "..."
  reply_to: "..."
```

### Open: Google Sheets access model

If the roster lands in Google Sheets, two options, decided when the sheet exists:

- **Link-shared CSV export** — no authentication at all, one HTTP GET. But the
  URL exposes every member's address to anyone holding it.
- **Service account** — sheet shared privately to it, one extra GitHub Secret.

Recommendation: **service account**, since these are members' personal addresses.
Moot if `Player360Source` lands instead.

## CLI

```bash
python -m duty_mailer --dry-run            # print, send nothing
python -m duty_mailer --date 2026-09-18    # simulate a given day
python -m duty_mailer --sheet other.xlsx   # override source path
python -m duty_mailer                      # live
```

`--date` is load-bearing, not a convenience: because the system is stateless
(D2), replaying dates is the only way to answer "what will go out next month?"
without sending anything. Combined with `--dry-run` it walks a whole season
safely.

## Testing

pytest, mirroring `charge-amps`. TDD — tests precede implementation.

- `test_scheduling.py` — offset matching; role assignment for 1/2/3+ offsets;
  predecessor adjacency at, on, and beyond `max_handoff_gap_days`; first
  occurrence with no predecessor; DST boundaries; timezone correctness near
  midnight.
- `test_sources.py` — wide and tall layouts; ragged rows; empty `dagar_innan`;
  malformed and Excel-native dates; duplicate dates; missing required columns.
- `test_templates.py` — both roles; predecessor present and absent; people with
  and without names.
- `test_config.py` — defaults, validation errors.

SMTP and HTTP are not exercised live; both sit behind seams and are faked.

## Deliberately not built

No database or sent-log. No retries. No web UI or Fly.io deployment. No write
access to the roster. No per-person personalization. No unsubscribe. No
timezone support beyond a single configured zone. The key-location feedback loop
is *designed for* (D5, `key_location`) but not implemented.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| GitHub drops a scheduled run | A reminder is silently never sent | Accepted (D2/D3). `--date` replay allows manual recovery. |
| Workflow auto-disabled after 60 days idle | All reminders stop, silently | Note in README; any commit resets it. |
| Roster edited into an unparseable state | Run fails | Adapter validates at the boundary and fails loudly; workflow failure is visible in Actions. |
| 360Player has no usable API | `Player360Source` is impossible | Spreadsheet adapters are built first and remain sufficient on their own. |
| Assumed column names are all wrong | Adapter rewrite | Contained to `sources/` by construction; nothing above it changes. |

## Next step

Implementation plan via the `writing-plans` skill.
