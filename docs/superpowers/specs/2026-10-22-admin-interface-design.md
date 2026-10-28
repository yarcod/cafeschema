# Admin interface — design

**Date:** 2026-10-22
**Status:** Approved design; plans 1 and 2 implemented
**Repo:** `360-mailer`
**Package:** `web` (`duty_web`)

## Context

Today every change to a team's schedule goes through the terminal: the coach's
Excel is rebuilt with `just schedule-build` (with `data/name_fixes.json` and a
contact list), then pushed with `just schedule-push`, which **replaces every
slot of the team and drops its swaps**. Rosters, documents and duty profiles
(venue, café, instruction) follow the same path, and the venue/café/instruction
mapping is hardcoded in `web/src/duty_web/duties.py`.

That works for one person with a shell. It doesn't work for the
*föräldraansvariga* of each team, and it isn't something an AI agent can
drive cheaply. This spec designs an admin interface that:

1. lets a human add schedules when they become available — a whole Excel from
   the coach, a single weekend cup picked on a calendar, or a single shift —
   and staff them fairly;
2. looks and feels like the parent app it sits on, so an admin already knows
   how to read it;
3. gives an AI agent the same capabilities through a JSON API, with no way for
   the two to drift apart.

**360Player is the club's main platform; this app is auxiliary.** People and
their roles come from 360Player's exports and are never re-entered here.

The visual design was iterated in the brainstorming companion and approved as
"direction C — the workbench" (final screen: `.superpowers/brainstorm/258988-1792699143/content/final.html`,
gitignored), with two later changes recorded below: no manual coach form, and
leaders detected from the 360Player export.

## Decisions

### D1 — One action registry, two doors

Every admin capability is one `Action` class, defined once. A generic HTML
route and a generic JSON route both run off it. A feature exists only as a
registry entry, so it cannot exist for humans and not for agents, or vice
versa. A test enforces it (see *Testing*). Rejected: hand-written HTML and API
routes with a parity test (each feature written twice; the test only catches a
missing route, not diverging behaviour), and an API-first SPA (breaks the app's
server-rendered, almost JS-free style).

### D2 — Preview, then apply, for every write

Every write action is `plan()` (pure, computes a list of changes, writes
nothing) followed by `apply()` (writes exactly that plan). Humans see the plan
as the **Granska** screen; agents get it from `dry_run: true`. This mirrors
`duty_mailer --dry-run` and is what makes an agent safe to hand the keys to.

### D3 — The workbench (direction C)

Admin lives at `/admin/<team>/`, a wider frame for a computer, built from the
parent app's own tokens and components. Its left column is what you change;
its right column is the parent app's view *after* the change. It's reached via
a ✎ next to ⚙ in the parent app's topbar, shown only to admins. Parents see
no change.

### D4 — Places are data

`duties.py`'s hardcoded `DUTY_PROFILES` become two tables, `venues` and
`stations`. A station belongs to one venue and links at most one instruction.
Stations are picked from a list, never typed freely, and names are compared
after normalization, so "cafe", "Cafe" and "Café" can never become three
places with three different instruction links. Venues and stations are shared
by the whole club (Wallenstam arena is the same building for F15 and P13).
The migration seeds venues, stations and documents only on its first run, while
the tables are empty. Later runs, and every schedule push, only link slots
without a station to stations that already exist; they never create or rename
a place and never touch a document, so an unlink or delete in the admin sticks.

### D5 — Leaders come from 360Player, and their families are exempt

360Player's members CSV export (UI → Truppen → Exportera; columns `Förnamn,
Efternamn, E-post, Telefonnummer, Föräldrars namn, Barnens namn`) lists a
leader twice: once as a parent (with `Barnens namn`) and once as a membership
row with neither `Föräldrars namn` nor `Barnens namn`. A row of the second
kind whose email belongs to a parent row is a **leader**. In F15 this finds
exactly the team's 8 leaders (both 360Player roles, *Ledare* and
*Administratör*, are used for real leaders there). Duties belong to the
player, so a player with any leader parent is **exempt** from shifts. A wrong
detection is fixed in 360Player, not here. Nobody enters coaches by hand.

### D6 — No live 360Player integration

360Player's non-public API works with plain `curl` given the browser's real
headers (verified 2026-10-22; the earlier `426` was a missing
`x-client-version`, not TLS fingerprinting). But it would need a person's own
360Player session token, there's no per-user OAuth, and the members endpoint
doesn't link guardians to children. So the roster is imported from the CSV
export, at team setup and whenever it needs refreshing. No 360Player
credential is ever stored by this app.

### D7 — Fair random staffing

"Randomly assign" means fair first, random second: whoever has the fewest
shifts this season goes first, and chance only decides among equals. Same
seed, same result, so a plan can be reviewed and then applied unchanged.
Ties are broken in this order: fewest shifts this season, then a sibling
already on the shift, then no shift the week before or after, then chance.
Fair first means a sibling goes first only among equally loaded players.

### D8 — Two roles, scoped per team, trust follows permission

*Klubbadmin* (the repo owner to begin with) sees every team. *Ansvarig* is
granted per team, sees only that team, and can make other people in that
team's roster ansvarig. This isn't mission-critical software, and handing out
the permission is a statement of trust. *Ledare* (D5) gives no admin rights.

### D9 — Security best practice by default, always

This app is not critical, but it holds parents' names and addresses and acts
in their name. Every choice defaults to established best practice: no
plaintext secrets anywhere, least privilege, nothing revealed to someone who
isn't entitled to it. See *Security*, which is normative.

## Data model

New tables:

| Table | Columns | Notes |
|---|---|---|
| `venues` | `id`, `name`, `name_key` (unique) | |
| `stations` | `id`, `venue_id`, `name`, `name_key`, `document` (nullable filename) | unique `(venue_id, name_key)` |
| `team_roles` | `person_id`, `team_id`, `role` | `role ∈ {leader, ansvarig}`; PK all three |
| `club_admins` | `person_id` (PK) | |
| `api_keys` | `id`, `person_id`, `prefix`, `key_hash` (unique), `created_at`, `expires_at`, `last_used_at`, `revoked_at` | see *Security* |

Changes to existing tables:

- `slots.station_id` (FK, nullable during migration, then required). `slots.venue`
  and `slots.station` stay as denormalized text, rewritten from the station on
  every write, so `/api/schedule`, `duty_mailer` and the parent templates keep
  working untouched.
- `players.exempt` (bool, default false), recomputed by every roster import.

`name_key = normalize(name)`: Unicode NFKD, accents stripped, casefolded,
whitespace collapsed. One function, used by every lookup and uniqueness
check. The display name is whatever was entered first.

`RENAMED_DUTIES` stays: it maps the coach's Excel activity names, not places.

## Actions

```python
class CreateSlots(Action):
    name = "slots.create"         # same name in UI, API, tests and logs
    title = "Lägg till pass"
    scope = "team"                # "team" | "club"
    Input = CreateSlotsInput      # dataclass; types + validation
    def plan(self, ctx, inp) -> Plan: ...      # pure
    def apply(self, ctx, plan) -> Result: ...  # one transaction
```

`Plan = list[Change]`, `Change(op: add|update|delete, kind, before, after,
warnings)`.

### The two doors

- **API.** `POST /admin/api/<name>` with JSON. `dry_run: true` returns
  `{changes, warnings, plan_token}`. `dry_run: false` with `plan_token` applies.
  The token is an HMAC (keyed from `SECRET_KEY`) over the action name, team,
  person and the canonical plan, and it expires after 30 minutes. If `plan()`
  run against the current data no longer produces the same plan, the call gets
  `409 stale_plan`. A replayed token gets the same `409`: the first apply
  changed the data, so the plan no longer matches. No used tokens are stored.
- **HTML.** `GET /admin/<team>/<name>` renders the action's form. `POST`
  renders **Granska** from the same `plan()`. **Genomför** posts the same
  `plan_token` through the same apply path. Forms are hand-written Jinja per
  action (the calendar and staffing UIs can't be generated). Validation and
  logic live only in the action.
- **Discovery.** `GET /admin/api` lists the teams and actions available to the
  caller, with field schemas generated from each `Input`.
- **Reads.** `GET /admin/api/{slots,venues,roster,documents}` are plain reads.
  They have no plan.

### v1 actions

| Action | Scope | Purpose |
|---|---|---|
| `slots.create` | team | days × shift rows → empty seats |
| `slots.assign` | team | fair staffing of seats (D7) |
| `slot.update` | team | edit one shift's seats (date, time, station, note; one seat's player) |
| `slot.delete` | team | remove one shift's seats |
| `schedule.import` | team | coach's Excel → diff |
| `roster.import` | team | 360Player members CSV → diff, leaders, exempt |
| `venue.create` | club | |
| `station.create` | club | rejects near-duplicates (`409 near_duplicate` + suggestion) |
| `station.link_document` | club | link an uploaded instruction |
| `team_admins.add` / `team_admins.remove` | team | ansvariga |
| *(not actions)* | self | API key create/revoke are settings pages under ⚙, not registry actions, so a leaked key can't mint keys |

## Flows

### Lägg till pass — `slots.create`

Input: `team, days[], activity, rows[{start, end, station_id, seats, on_days[]}]`.
Creates one `Slot` per seat, unassigned. Validates end after start and that the
station exists. Warns (doesn't block) when the team already has an overlapping
shift at the same station. The UI picks days with two date fields and shortcuts (*Den här helgen*,
*Nästa helg*, *I dag*), and each row can be limited to some weekdays. A JS
range picker can be added later on top of the same form.

### Fair staffing — `slots.assign`

Input: `slot_ids[], locked{slot_id: player_id}, seed`.

- **Candidates:** the team's players who are not `exempt` and have at least one
  parent with an email.
- **Load:** shifts per player in the season (1 Aug–31 Jul around the shifts'
  dates), counting undated shifts and the ones being planned.
- **Seats** are filled in date order. Each takes the eligible candidate with
  the lowest load, where:
  - a candidate who already has a shift that day is not eligible (hard rule);
  - a shift the weekend before or after adds a penalty (soft rule);
  - among equally loaded candidates, a sibling of someone already on the
    shift goes first;
  - remaining ties are broken by `random.Random(seed)`.
- **Locked seats** are never touched.
- **Unfillable seats** stay *Ledigt* with a reason ("alla kvarvarande har
  redan pass den dagen").
- The plan reports each affected player's load before → after, plus the
  team's mean and spread.

Only empty seats are filled: a filled seat in the selection is left alone,
and `locked` pins a player to an empty one. Without a `seed`, one is chosen
and carried in the plan token.

A lock keeps the draw's rules: it's refused when its player already has a
shift that day (another lock included) or is already on that shift. A
selection is staffed in one season; dated seats from two seasons are
refused, and an all-undated selection carries its season in the plan token.

### Excel import — `schedule.import`

Replaces today's delete-and-reinsert `import_schedule` with a diff. The
coach's file is translated in-app (the logic of `scripts/build_seed_xlsx.py`
moves into `duty_web`). Each row is matched against existing slots on
*(date, start, end, activity, station, name)*, where the name is the one the schedule gave the seat (`slots.child_name`), never its current owner:

- identical key → unchanged; the slot keeps its id and its swaps;
- same date, time, activity and station, different name → `update`;
- an undated slot that now has a date (same activity, time and player) →
  `update`, so it keeps its id;
- left over in the file → `add`; left over in the database → `delete`, with a
  warning per pending swap (the swap is cancelled and both parties get the
  usual mail, after commit).

Unknown venue or station names stop the import with "Okänd station 'X' på
'Y': lägg till den under Platser eller rätta filen". It never creates a
near-duplicate. A schedule name that matches no player becomes a pick list in
Granska ("Vem är 'Elin Exempel'? [välj spelare]"), replacing
`data/name_fixes.json`.

Matching on the schedule's name is what keeps swaps: an accepted swap
changes a seat's owner, never its name, so the same file imported again
finds the seat unchanged. The import changes a player only when the file
changes the name.

Rows are paired by identity before anything else: the same shift (an
undated one told apart by its weekday note) with the same name, then with
a name that means the same player (a respelling, which changes only the
record), then an undated seat the file dates (on a fitting weekday), and
only then the same shift with another name. One rule then reconciles
every pair, whatever pass made it.

Besides those four passes there is a "placed" pass: when an ansvarig
corrects a saved place answer, seats at the previously saved station move to
the new one (same id, owner kept, pending swaps cancelled) instead of being
deleted and re-added.

What it may touch: activities that appear in the file, in the seasons its
dated rows fall in (1 August – 31 July), plus undated slots. A slot dated
before today, or one made under Pass (`slots.origin = 'admin'`), may match
an identical row but is never changed or removed, and a past row with no
match isn't added. Granska states this boundary.

A changed name cancels that seat's pending swaps (mail to both families
after commit); a newly dated undated slot keeps its swaps. An activity with
no known place becomes a pick list in Granska ("Var hålls 'X'?") and is
imported without a place until answered; a place the file *names* that
doesn't exist still stops the import. A name that isn't on the roster is
imported Ledigt, with the file's spelling, until it's answered. Answers are
saved (`name_aliases`, `activity_stations`), shown preselected at the next
import, and changed there; correcting a saved name moves the seats it gave.
When an import changes nothing, an answer given in Granska is not saved, and
Granska says so ("Svaret om 'X' sparas inte: inget pass ändras av det.").

The file is checked before it's read: a date that is neither a date, blank
nor "Datum ej satt …" stops the import with its row; so does an unpacked
size over 20 MB, more than 100 archive members, or a cell over 200
characters. Only the first 10 sheets and 30 columns are read.

### Roster import — `roster.import`

Input: the 360Player members CSV (D5).

- **Additive, as today:** adds players, parents and links, and updates names.
  Players come only from player rows. People who have ties to other teams are
  linked to this team but never renamed.
- **Roles:** detects leaders, rewrites the team's `team_roles(leader)` rows and
  recomputes `players.exempt`.
- **Missing people:** people absent from the file are not removed. They're
  listed as a warning ("finns inte längre i 360Player · 2 kommande pass").
- **Plan:** new families, renamed people, leaders found, newly exempt players,
  and exempt players who still hold upcoming shifts (with a link to restaff
  them).

The CSV is parsed in memory and never written to disk. The phone-number
column is ignored. Uploads are limited to 2 MB per request and 1 MB per file.

### Places

`venue.create`, `station.create`, `station.link_document`. A station whose
normalized name matches an existing one at the same venue is refused with the
existing one as suggestion. Linking offers only the shared root documents (a
station is club-wide, so a team's private file would be broken for the other
teams); a file of the same name in a team's folder overrides the shared one for
that team. It ranks the ones whose filename contains the venue's name
first. Only a klubbadmin can delete a venue or station, and only when no slot
uses it.

## Permissions

- **Klubbadmin:** `club_admins`. Seeded from the `CLUB_ADMINS` setting
  (comma-separated emails) **only while the table is empty**. After that the
  database is authoritative, so an environment variable can't silently grant
  rights later. An address that matches no person is logged as a warning
  and skipped.
- **Ansvarig:** `team_roles(role=ansvarig)`. Granted by a klubbadmin or by an
  ansvarig of the same team. Only people in that team's roster can be picked.
  Only a klubbadmin can remove the last ansvarig of a team. A new ansvarig gets
  a mail saying so.
- **One check, both doors:** `AdminContext(person, team)` is built before any
  action. If the caller has no admin role in that team, the response is `404`,
  never `403`, so other teams' existence isn't revealed. That's the same rule
  the documents routes already follow. `scope="club"` actions need klubbadmin
  or ansvarig in any team, except deletion, which is klubbadmin only.

## Security

Normative. Where this section and another disagree, this one wins.

**API keys**

- **Generation:** `secrets.token_urlsafe(32)` (256 bits), prefixed `cs_` plus a
  short public identifier (`prefix`) so a key can be recognized in the UI
  without revealing it.
- **Never stored in plaintext.** Not in the database, logs, error reports,
  session, cookies, URLs or mail. The database stores only
  `key_hash = SHA-256(key)`. A 256-bit random key needs no slow hash, and this
  is the scheme used by GitHub and Stripe. Lookup is by hash; the comparison is
  `hmac.compare_digest`.
- **Shown once:** the full key is shown on the page that created it, with
  `Cache-Control: no-store`, and never again. A lost key is revoked and a new
  one created.
- **Only the owner sees it.** Nobody else can view or create a key for
  someone else, including a klubbadmin. A klubbadmin can *revoke* any key, and
  sees only its `prefix`, owner, creation date and last use.
- **Accepted only** as `Authorization: Bearer` on `/admin/api/*`. It's never
  accepted as a query parameter, cookie or form field, or on any other route.
  A key-shaped value anywhere but the Bearer header is refused with `400`
  and redacted from the access log.
- **Lifetime:** expires after 180 days by default. Revoked on demand.
  `last_used_at` is shown to its owner.
- **No standing rights:** permissions are resolved on every call, so an
  ansvarig who is removed locks their agent out on the next request.
- **Logs:** the request logger redacts the `Authorization` header. Only
  `prefix` may appear in logs.

**Elsewhere**

- **CSRF:** every admin HTML POST uses the existing Flask-WTF protection. API
  routes ignore session cookies entirely and require a Bearer key, so CSRF
  doesn't apply to them.
- **Rate limiting:** per key on the API, and per person on admin POSTs.
- **Uploads:** `.xlsx`/`.csv` only, size-capped, parsed in memory, never
  persisted.
- **No 360Player credential is ever stored** (D6).
- **Privacy:** admin responses and logs never contain other parents' email
  addresses. The one exception is `roster.list` for admins of that team. Logs
  record action, team and change count, never names or addresses. This extends
  the existing no-address rule for mail and Actions logs.
- **Plan basis:** every plan's digest also covers a hidden basis: the current values of each row its writes touch, and the swaps on them, so a row changed between review and Genomför gives 409 even when Granska's text reads the same. Genomför holds SQLite's write lock (BEGIN IMMEDIATE) from its re-plan to its commit, so the check and the write can't be split by another writer. Only lock errors become 409 "busy".
- **Plan tokens** are HMAC-signed and short-lived (see *The two doors*), so a
  plan can't be forged or replayed into another team.

## UI

The final mockup is the reference. Summary:

- **Entry:** ✎ next to ⚙ in the parent topbar, for anyone with an admin role.
- **Frame:** `/admin/<team>/` has tabs **Pass · Platser · Lag & ansvariga**.
  The team switcher only lists the caller's teams, and is hidden when there's
  one. A new `base_admin.html` provides a wider frame with the same tokens,
  fonts and components as `style.css`.
- **Pass:** the team's list in the *Hela laget* style with *Ändra* per shift
  (side panel), *Sätt datum* on undated shifts, and two entries: **+ Lägg till
  pass** (Dagar & pass → Bemanna → Granska) and **Importera Excel** (→
  Granska). A shift is one card per date, time, activity and station (and, undated, its note), so two stations at one venue at the same time are two cards. **Ändra** and **Ta bort** carry the shift's seats; each seat's **Ändra spelare** carries only that seat.
- **Platser:** venues → stations → instruction (✓ linked / ? missing), plus
  uploaded documents not linked anywhere.
- **Lag & ansvariga:** Ansvariga, Ledare (from 360Player, with exempt
  children), Föräldrar. Has **Uppdatera från 360Player** (CSV → Granska).
- **Granska:** one template for all actions. The diff (`+` new, `~` changed,
  `−` removed) is on the left. The right shows a phone preview rendered with
  the parent app's own templates against the planned state, so the preview
  can't drift from what parents will see.
- **Agent hints:** each form shows its agent twin (`agent → slots.create`).
- **JS** only for range picking, the station combobox and seat locking. Every
  form also works as a plain POST.
- **Mobile:** columns stack. Usable on a phone, designed for a computer.

## Rollout

Each step deploys on its own.

1. **Migration:** new tables, `slots.station_id`, `players.exempt`.
   Venues/stations from `DUTY_PROFILES`, existing slots linked, `CLUB_ADMINS`
   seeded. Rehearsed against `just db-pull` first, like `migrate_multi_team.py`.
2. **Read path from the database:** `profile_for()` / `slot_document_url`.
   Parent views and `/api/schedule` are verified identical before and after
   (snapshot test).
3. **Registry, permissions, API keys and ✎,** with no actions yet.
4. **Actions,** in order of value: `roster.import` → places →
   `slots.create` + `slots.assign` → `slot.update`/`slot.delete` →
   `schedule.import` → `team_admins`.
5. **Retire the old path:** once `schedule.import` has replaced a real
   `schedule-push`, the `just *-push` recipes call the API with the owner's key
   (read from 1Password at call time, never written to disk), and
   `admin_app.py` is removed. This is plan 3: it waits until a real import has been done with `schedule.import`.

## Testing

- **Pure core:**
  - `normalize()`;
  - each action's `plan()`;
  - fair staffing with a fixed seed (exact result), plus properties across many
    seeds: never two shifts a day, never an exempt player, spread ≤ 1 when
    capacity allows, siblings together when possible;
  - the Excel diff: unchanged keeps id and swaps, undated → dated is an update,
    unknown station stops it;
  - leader detection, against a synthetic CSV of the real export's shape,
    never the real file.
- **Parity:** the registry test requires every action to have a form template,
  a responding API route and a `plan()` test. One test per action runs the same
  input through both doors and requires the same plan.
- **Permissions matrix:** every action × {parent, leader, ansvarig F15, ansvarig
  P13, klubbadmin, each one's API key} → expected 200/404, including "removed
  ansvarig's key is refused on the next call".
- **Plan tokens:** stale → 409, tampered → 400, replayed → 409, another
  team's token → 404.
- **Key handling:**
  - the stored row contains no plaintext;
  - the key never appears in logs, including a forced error;
  - creation responses carry `no-store`;
  - query-string and cookie keys are refused;
  - an expired or revoked key is refused.
- **Migration:** run against a production copy. The parent-view and
  `/api/schedule` snapshots must be identical before and after.
- **Privacy:** no admin response or log line contains another parent's email
  address, except `roster.list`.

## Error handling

- `apply()` is one transaction: all of the plan or none of it.
- Validation errors are per field:
  `400 {"errors": {"rows[2].end": "Sluttiden måste vara efter starttiden"}}`.
  In HTML they appear next to the field.
- Mail (cancelled swaps, new ansvarig) is sent after commit. A failed mail is
  logged and doesn't roll back the change. Granska said beforehand who would
  get mail.

## Deliberately not built

- Live 360Player sync (D6).
- Creating teams and uploading documents in the UI (`just team-add` and
  `just docs-push` stay). Documents are *linked* in the UI.
- History, audit trail and undo. Granska before every write is the safeguard
  for now.
- Saved shift templates between events.
- Mail to parents when shifts are created. Reminders go out as usual before
  each shift.
- Manual coach entry, and per-person duty toggles. Whether assistant leaders
  should also be exempt is still open. If not, it becomes a rule on the leader
  detection, not a data-entry field.

## Next step

Implementation plan via the writing-plans skill.
