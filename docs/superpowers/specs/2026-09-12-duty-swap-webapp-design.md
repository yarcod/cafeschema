# Duty swap web app — design

**Date:** 2026-09-12
**Status:** Approved design, not yet implemented
**Repo:** `360-mailer`
**Package:** `web` (new, sibling to `duty_mailer`)

## Context

`duty_mailer` (already built, spec at `docs/superpowers/specs/2026-09-01-duty-reminder-mailer-design.md`) sends reminder emails for a recurring arena-hosting duty roster. It reads a read-only `ScheduleSource` — currently an offline `.xlsx` file — and never writes back to it (spec D5).

That design was deliberately kept minimal, but two real needs have since surfaced:

1. Parents want to **swap duty slots** with each other, which needs a place to negotiate that a spreadsheet can't provide.
2. The roster should be presented somewhere nicer than an Excel file for parents to check.

This plan designs that: a small public web app where parents log in (passwordless, via emailed magic link) to see their duties and propose swaps with other parents. The xlsx becomes a **one-time seed** for the season, not an ongoing source — after seeding, the web app's own database is authoritative, and `duty_mailer` gains one new adapter to read from it instead of the file. `duty_mailer` itself is otherwise untouched.

Real example row from the (not-yet-final) xlsx, columns separated by `\`:

```
2026 \ Arena värdskap \ Wallenstam arena \ Cafe \ 3 \ 16-Jan \ Fredag \ 18:00-21:00 \ Alva Exempel \ Hämta nyckel helgen innan
```

Read as: year, duty name, venue, **station** (free text, e.g. "Cafe"), **week number** (redundant with date, kept for spreadsheet readability — not used in logic), date, weekday (also redundant), **time range**, assigned person, free-text note. The atomic, swappable unit is one row: a specific station, at a specific venue, on a specific date and time range, held by one person.

A visual mockup of the parent-facing UI (login, "Mina pass", "Hela laget" calendar, "Byten" swap requests, light/dark/auto theme) was reviewed and approved during brainstorming: https://claude.ai/code/artifact/77655be4-c25e-4473-9281-9ab71715299a

## Decisions

### D1 — Hosting: one Flask app on Fly.io

Frontend and backend together, not a static GitHub Pages site. GH Pages can't itself verify a login, send email, or safely store swap state — anything checked only in client JS is forgeable. Once a real backend exists anyway, splitting a separate static frontend off from it just adds deployment complexity (two hosts, CORS) for nothing. Same deployable shape as `../charge-amps`' config editor.

### D2 — Auth: magic link only

Via `itsdangerous` (the Pallets/Flask team's own library, documented by Flask itself for exactly this — signed, time-limited email-confirmation tokens) + `Flask-Login` for sessions + `Flask-WTF` for CSRF. Proven, narrowly-scoped, widely-audited libraries — not a hand-rolled auth scheme. No numeric code: a link avoids an entire class of brute-force/rate-limit problems a typed code would need to defend against.

### D3 — Xlsx role: one-time seed only

Imported once to populate the database; from then on, the web app's database is the source of truth, including for `duty_mailer`.

### D4 — Swap scope: any slot for any slot

A Cafe duty can swap for an Entré duty — most real trades are about wanting a different weekend, not a different job. Can restrict later if some stations turn out to need specific know-how.

### D5 — Swap model: direct propose-to-person

Pick your slot, pick who to swap with, they accept or decline. Simple state machine, clear ownership at each step. Entry point is from "my duties": pick your own slot, then pick another open slot to propose swapping it for — either from a short in-line list of candidates, or by browsing the full team calendar and proposing against any day's slot from there. Multiple candidate slots can be selected and sent as separate proposals in one action; selecting a candidate never sends by itself — a candidate slot is checked, not fired, and a separate "Skicka förslag" action confirms and sends whichever are checked.

### D6 — Two views, different jobs

- **Mina pass** (home) — the logged-in parent's own upcoming duties, as a chronological list. The same underlying data as `duty_mailer`'s reminder queries — one Slot table, two consumers.
- **Hela laget** — a calendar view of the whole team's schedule, for browsing and picking swap targets.

### D7 — No admin UI for v1

Post-seed fixes (a typo'd email, a slot needing manual reassignment outside a normal swap) go through `fly ssh console` + a direct `sqlite3` edit on the volume, not a web UI. Should be rare, and avoids building a whole editing surface for something used occasionally by one technical person.

## Data model

```
Team          id, name, venue                    (e.g. "F14 Blå", "Wallenstam arena")
Person        id, name, email (unique)
Slot          id, team_id, date, start_time, end_time, station, duty_name, venue, note,
              person_id (nullable while unfilled)
SwapRequest   id, proposer_slot_id, target_slot_id,
              status (pending | accepted | declined | cancelled | expired),
              created_at, resolved_at
LoginToken    token_hash, email, expires_at, consumed_at
```

**Forward-compatibility note:** a parent with kids on more than one team needs their duties and swap options to span teams, so `Slot` carries a `team_id` from the start even though v1 seeds exactly one team. This costs nothing now (v1's import just always writes the one team's id) but avoids a schema migration later — `Person` stays keyed by email regardless of team count, so "Mina pass" naturally aggregates across teams by querying `Slot` for that `person_id` with no join changes. **Not built in v1:** a team switcher/filter on "Hela laget" (needed once a parent is actually on 2+ teams, since browsing another team's full calendar to find a swap partner should probably be scoped to teams you share a slot with) and swap-target validity across teams (does a Cafe slot on Team A ever make sense to swap against a slot on Team B? — undecided, revisit if/when a second team is actually seeded).

- Accepting a `SwapRequest` swaps the two slots' `person_id` in one transaction; declining just changes status.
- Pending requests auto-expire after 7 days (configurable) so they don't linger indefinitely.
- No `lead_days` column on `Slot` — `duty_mailer`'s config-level default lead time applies uniformly now; per-row lead times were a spreadsheet-era feature that doesn't carry forward.
- Storage: **SQLite on a Fly.io volume**, same pattern as `charge-amps`. Plenty for this write volume (a handful of parents, occasional swaps); no need for a managed Postgres instance.
- Parent-facing pages show **names**, never raw email addresses — the app mediates contact via its own notification emails (reusing `duty_mailer`'s Fastmail SMTP + Swedish templates), so nobody's address is exposed on-page.

## duty_mailer integration

The Flask app exposes one small **read-only, API-key-authenticated** endpoint — `GET /api/schedule` — separate entirely from the public parent-facing magic-link auth. `duty_mailer` gains a new adapter, `WebAppApiSource(url, api_key)`, implementing the existing `ScheduleSource` protocol (same shape as `GoogleCsvSource`). The API key is a Fly secret on the web app's side and a GitHub Secret on `duty_mailer`'s side.

This is exactly the extensibility the original `ScheduleSource` port was built for: **no change above `sources/`** in `duty_mailer` — `models.py`, `scheduling.py`, `templates.py`, `email_sender.py`, `__main__.py` are all untouched. Because swaps write directly into the same Slot table this endpoint reads, an accepted swap is reflected in the next day's reminder automatically — no special-casing needed.

## Security baseline

- HTTPS enforced (Fly.io, same as `charge-amps`).
- Magic-link tokens: single-use, short-lived (~20 min), signed with `itsdangerous`.
- Session cookies: `HttpOnly`, `Secure`, `SameSite=Lax`; a long-lived session (~30-90 days) after one successful login — this is a chore roster, not a bank, so re-verifying by email every visit would be poor UX for no real security gain.
- Light rate limit on magic-link requests (e.g. one per email per 60s) to stop someone spamming another parent's inbox.
- CSRF protection (`Flask-WTF`) on every state-changing request (propose/accept/decline swap).

## Email delivery volume

Reuses `duty_mailer`'s existing Fastmail SMTP credentials (D3 in the `duty_mailer` spec) for both magic-link emails and swap-notification emails, on top of the reminder emails already sent. At ~35 parents, expected volume is small: magic links are personal (one recipient) and infrequent — sessions last 30-90 days, so even a full-team login burst on launch day is on the order of 35 emails, not a sustained rate. Swap notifications are similarly one-off, tied to actual swap activity. This stays well under typical shared-SMTP daily/hourly caps, but the exact current Fastmail limit isn't verified here — worth a quick check of Fastmail's own account limits page before relying on it in production, since providers change these over time.

## Repo structure

A new sibling directory in this repo, `web/`, with its own `pyproject.toml`, `Dockerfile`, and `fly.toml` — independently deployable, not imported by `duty_mailer` (they talk over the HTTP API above, not a Python import), mirroring how `charge-amps` is laid out as a self-contained deployable unit.

## Seeding the database

The xlsx seeds the season **once**, before any parent has ever logged in — no swaps exist yet at that point, so there's no live state to preserve during import.

**Open gap: the xlsx has no email column.** The example row has a full name ("Alva Exempel") but nothing to log that person in with or send a magic link to. Two ways to close this:

- **Add an e-post column to the xlsx (recommended).** Simplest, no ambiguity — one row fully specifies one assignment.
- **Maintain a separate name→email roster and join by name during import.** Worse: typos and formatting differences between two independently-maintained lists make name-matching unreliable, and a mismatch would silently assign the wrong person to a slot.

**Mechanism:** an `/admin/import` HTTP endpoint, but bound to `127.0.0.1` only (Flask's server listens on localhost inside the Fly VM, not on the public interface the rest of the app uses). Reaching it requires `fly ssh console` to get a shell on the machine, then tunnelling — either `fly proxy` from a local machine forwarding to the app's internal address, or simply `curl localhost:PORT/admin/import` from inside the SSH session itself. Either way, the endpoint is unreachable from the public internet; only someone with `fly ssh console` access (i.e. Fly org membership) can ever hit it. This keeps the convenience of `curl -F file=@schema.xlsx` over a raw CLI command, without adding standing *public* attack surface — same reasoning as skipping an admin UI, just satisfied by network binding instead of by not having a route at all. The endpoint wipes and reimports the `Person`/`Slot` tables — safe, since it only ever runs before parents start using the app — reading the uploaded file via `openpyxl`, looking up-or-creating a `Person` by email, and creating one `Slot` per row.

## Deliberately not built

- **Open marketplace / "propose-to-person + open release" swap models** — dropped in favor of direct propose-to-person only, for v1.
- **Admin UI** — dropped in favor of `fly ssh console` + direct `sqlite3` edits for the rare post-seed fix.
- **A publicly-reachable seed/import endpoint** — the import route exists but is bound to localhost only, reachable exclusively via `fly ssh console`/`fly proxy`, so it carries no public attack surface despite being an HTTP endpoint.
- **A team switcher on "Hela laget" and cross-team swap rules** — deferred until a parent is actually on two teams (see Data model forward-compatibility note).

## Next step

Hand this off to `superpowers:writing-plans` for a full task-by-task implementation plan (mirroring how `duty_mailer` itself was planned and built).

During actual frontend implementation (not just the review mockup), consult `/frontend-design:frontend-design` for a final pass on contrast and color choices — the mockup surfaced at least one instance of low-contrast text (white-on-white) that needs catching properly before this ships, not just eyeballed.
