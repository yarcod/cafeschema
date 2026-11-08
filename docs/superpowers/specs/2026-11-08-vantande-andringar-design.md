# Väntande ändringar and the Pass table — design

**Date:** 2026-11-08
**Status:** Implemented
**Package:** `web` (`duty_web`)
**Builds on:** `2026-10-22-admin-interface-design.md` (D1 one registry, two doors; D2 preview then apply; D4 places are data; D7 fair staffing; D9 security)

## Why

An ansvarig sat down to fill in F15's placeholder Pass (date, time, place, players, each different). Every edit led to its own Granska, and leaving Granska without Genomför threw the edit away, so the Pass looked untouched. Confirming one edit at a time is unusable, and bulk edits are the normal case.

Creating Pass is slow for the same reason. Nearly every café weekend has the same sessions, yet each one is typed in by hand.

## Goals

1. Edit many Pass in one table, directly in the row.
2. An edit is saved as **väntande** the moment it is made, survives leaving the page, and is visible on the Pass page and in Granska.
3. One Granska confirms the whole batch. Only that confirmation changes what parents see or sends any mail.
4. Create a café weekend from a date span in one step, for people and agents alike.

## Non-goals

- A settings page for editing the café pattern. It changes with a code update.
- Patterns other than the café weekend.
- Changing how Excel import, roster import or Platser work. They keep their own Granska and apply directly.
- Editing Pass on a phone as a designed experience. The admin is a computer workbench (D3). The table scrolls sideways on narrow screens and nothing more.

## Anti-goals

The work has failed, even if it functions, if:

- a parent, the reminder mailer or `/api/schedule` can see a väntande edit or a väntande Pass;
- anyone is mailed before Genomför;
- the admin turns into a client-side app (D1 rejected an SPA) — a little plain JavaScript for autosave is the limit;
- parent addresses appear in Granska, the staged data, API responses or logs (D9). Names and ids only.

## Decided with the user

**Table (mockup option A).** The Pass page is a table with one row per Pass, grouped by day, with "Datum ej satt" as a group of its own. Date, time, place and each player seat are edited in the row. Orange rows are väntande. Past Pass stay hidden behind "Visa tidigare", as today.

**Bar.** While anything is väntande, a bar stays at the bottom of the Pass page: "N väntande ändringar, inga mejl skickade", with **Granska**.

**Granska.** One page for the batch, grouped by day, one entry per Pass. Each entry can be undone on its own (**Ångra**), and **Släng alla väntande** discards the batch. **Tillbaka och ändra** returns to the table with everything intact. The button reads "Genomför N ändringar". The existing warnings (double bookings, cancelled swaps) and the "Skicka inga mejl om flyttade pass" switch apply to the whole batch, and the parents' phone preview stays.

**Who owns a batch.** One batch per team, shared by all its ansvariga and the club admins. It lasts until confirmed or discarded, with no expiry.

**Slumpa lediga.** Places players for one event at a time, never the rest of the season. It fills that event's empty seats with suggested names using the fair draw (D7). The names are väntande like any other edit, so you see who was picked before anyone is told.

**Placement mail.** Nobody is mailed for being placed on a Pass or for a placeholder getting its first date. That stays as it is today, and the user will ask parents for feedback before considering a "you've been placed" mail.

**Conflicts.** If a Pass changes by another route while it is väntande (an accepted swap, an Excel import), Granska flags that entry and it needs a fresh look. The rest of the batch can still be confirmed, and the flagged entry stays väntande.

**Agents.** Agents work on the same batch through the API, so what an agent stages appears in the table. Agents can also publish the batch, but a human should review it first. The publish action must carry a very clear code comment addressed to agents: it is strongly preferred that a person reviews the batch in Granska, and an agent may publish only when explicitly asked to and after getting approval for that publish.

**Café weekend.** All teams share one pattern:

| Day | Sessions |
|---|---|
| Friday | 17:30–19:30 |
| Saturday | 08:30–10:30, 10:30–12:30, 12:30–14:30, 14:30–16:30, 16:30–18:30 |
| Sunday | same as Saturday |

Two parents per session by default; three can be chosen for the whole weekend (finals and the like). Activity is "Cafépass". You pick a weekend by week number or with a date range, adjust the generated sessions if needed, and add them. They land as väntande rows with empty seats, 11 Pass for a full weekend. The place is picked per weekend and defaults to Café Arena B, nedre plan. Agents can read the pattern from `GET /admin/api` and ask for it by name.

The mockups are in `.superpowers/brainstorm/2771970-1794175033/content/` (`table-and-granska.html`, `cafe-weekend.html`, gitignored). Their session times and mail count are illustrative; this document wins.

## Builder's calls already made (by the designer, not the user)

**Storage: staged edits live outside `slots`.** Parent pages, `/api/schedule`, the mailer and swaps all read `slots` directly. Keeping staged edits and staged new Pass in their own table(s), scoped by team, means none of those need a filter and the first anti-goal holds by construction. Do not add a status column to `slots`.

**The batch runs through the existing machinery.** Confirming is one `Plan` built from the whole batch: one digest, one signed token, the existing `apply_plan` with its write lock, one transaction, then `after_commit` mail. The 30-minute token lifetime stays. It governs a Granska page, not the batch. If the batch changes during review (another ansvarig edits), Genomför gives the usual stale-plan page.

**Rules must see the batch, not just the database.** Double-booking warnings, the fair draw's load counts and "one shift a day", and overlap checks must count other väntande edits. `_preview`-style flushing or an overlay both work. Builder's choice.

**Slumpa stores concrete names.** The draw's result is saved as staged player ids, not re-rolled at Genomför.

**Other Pass-page actions stage too.** Ta bort becomes a staged removal (the row is struck through, and it is a − entry in Granska). The general "Lägg till pass" form stays for anything that isn't a café weekend and also stages its result. The separate Ändra, Sätt datum, Bemanna and Ändra spelare pages leave the Pass page.

**The other mail rules stay as they are**, applied once across the batch: a moved shift with a player keeps its mail and the switch, and cancelled swaps are always mailed.

**A staged edit stores only the fields it changes, each with the value it replaced.** A Pass is flagged in Granska only when something it staged no longer matches what it replaced: a swap that changes seat 1 does not disturb a staged new time. A flagged entry shows what changed and offers **Ångra** or **Behåll min ändring**, which keeps the staged values and takes the current ones as the new starting point. Any new edit to that field in the table does the same. If the seat or Pass no longer exists, only Ångra is offered.

**The same warning reaches agents through the API.** Agents using `GET /admin/api` don't read the source, so the publish action's entry there repeats the code comment's rule.

**What counts as an event for Slumpa.** A run of consecutive dates with the same activity, so a fri–sun café weekend or a cup is one event. Undated Pass form one event per activity. Each event has its own **Slumpa lediga** button on its group in the table, shown when it has empty seats, whether or not anything is väntande, so no per-row checkboxes are needed. A draw's names are marked as drawn. **Slumpa om** in Granska redraws those only and never touches names picked by hand. An event spanning two seasons is drawn one season at a time.

**The note stays editable**, in a small field on the row, since the Ändra page that holds it today goes away. A placeholder's note holds its weekday ("Lördagar") and is part of what groups seats into one Pass, so a note edit applies to the whole Pass and must keep its seats together. Setting a date leaves the note as it is.

**D1 holds.** Staging, undoing, discarding, drawing, creating a café weekend and confirming are registry actions, so `test_admin_parity` keeps passing.

## Left to the builder

- Table and column layout of the staged data.
- The shape of the new actions, and whether `slot.update`, `slot.delete`, `slots.assign` and `slots.create` are converted or replaced, as long as no path from the Pass page or the API edits slots while skipping the batch.
- How `scripts/admin_push.py` adapts, if at all.
- Autosave mechanics. It should be a small amount of plain JavaScript, with a save button for no-JS, CSRF on every post. The admin rate limiter is per request, so check that cell-by-cell saving can't trip it.
- Whether a row regroups under its new date immediately or on the next load. It must not jump while someone is typing in it.
- Showing the previous value of a väntande cell, if it fits.

## Constraints

- Follow the app's look: `style.css` and `admin.css` tokens, Fraunces, Manrope, the existing + ~ − signs. Väntande uses the existing `--warm` family.
- Swedish UI, sentence case.
- Places are picked from stations, never typed (D4).
- Team scoping on every staged read and write; another team's id is simply unknown.
- A Pass is several seat rows (`shift_key`). Date, time and place apply to the whole Pass; players are per seat.
- Schema changes go through `migrate.py`, which runs against the deployed SQLite database.
- Tests in `web/tests`, covering at minimum: nothing leaks to parent-facing reads or mail before Genomför; a batch survives leaving and returning; a partly conflicted batch; the pattern generates exactly the sessions above for a fri–sun span and only those weekdays for a wider or narrower span.

## Order of work

1. Väntande storage, table, bar and batch Granska.
2. Slumpa lediga inside the batch.
3. Café weekend, since it depends on väntande rows existing.
