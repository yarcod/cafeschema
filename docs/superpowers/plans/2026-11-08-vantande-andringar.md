# Väntande ändringar and the Pass table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Edits on the admin Pass page are saved as väntande in an editable table, confirmed together in one Granska, and café weekends are created in one step.

**Architecture:** Väntande edits live in a new `pending_passes` table, so nothing that reads `slots` (parent pages, `/api/schedule`, the mailer, swaps) changes. Every operation is a registry action. The HTML door applies staging actions at once by running `dry_run` then `apply_plan` in one request, and the API door keeps its two-call contract. `pending.publish` builds one `Plan` from the whole batch and goes through the existing token, digest, write lock and `after_commit` mail.

**Tech Stack:** Python 3.11, Flask, SQLAlchemy on SQLite, Jinja, Flask-WTF CSRF, plain JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-11-08-vantande-andringar-design.md`

## Global Constraints

- A parent, the reminder mailer or `/api/schedule` must never see a väntande edit or a väntande Pass. Do not add a status column to `slots`.
- Nobody is mailed before Genomför. Nobody is mailed for being placed on a Pass or for a placeholder's first date.
- No parent address in Granska, staged data, API responses or logs. Names and ids only.
- No client-side app. Plain JavaScript for autosave only, with a save button for no-JS, CSRF on every post.
- Swedish UI, sentence case. Use the tokens in `style.css` and `admin.css`, and the `--warm` family for väntande.
- Places are picked from stations, never typed.
- Team scoping on every staged read and write. Another team's id is simply unknown.
- Bar copy: "N väntande ändringar, inga mejl skickade" ("1 väntande ändring, inga mejl skickade" for one).
- Run tests from `web/`: `uv run pytest`. The suite had 699 passing tests before this work.
- Work on branch `feat/vantande-andringar`. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. An Excel import deletes a seat that is väntande: the import succeeds, Granska offers only Ångra for that Pass, and the rest of the batch publishes (Task 4).
2. A staged player no longer exists or has left the team: that Pass is flagged and publish does not crash (Task 4).
3. An edit is changed back to its original value: the row stops being väntande and leaves no empty entry in Granska (Task 2).
4. Genomför is submitted twice, by back button or double click: the second gets the stale page and no second mail goes out (Task 4).
5. Two ansvariga have the same row open: saving one cell leaves the other person's staged values alone, so autosave posts only the changed cell (Task 2 and Task 5).

---

### Task 1: Staged data and the effective schedule

**Files:**
- Modify: `web/src/duty_web/models.py`
- Create: `web/src/duty_web/admin/pending.py`
- Modify: `web/tests/conftest.py`
- Test: `web/tests/test_pending.py`, `web/tests/test_migrate_admin.py`

**Interfaces:**
- Produces, in `models.py`:
  ```python
  class PendingPass(Base):  # __tablename__ = "pending_passes"
      id: int; team_id: int  # FK teams.id
      kind: str              # PENDING_EDIT "edit" | PENDING_NEW "new" | PENDING_REMOVE "remove"
      duty_name: str | None  # "new" only
      fields: dict           # JSON: name -> {"from": value, "to": value}
      seats: list            # JSON: [{"slot_id": int|None, "from": int|None, "to": int|None, "drawn": bool}]
  ```
  Field names are `date` (ISO string or None), `start_time` and `end_time` ("HH:MM"), `station_id` (int or None), `note` (str or None). An edit or removal lists every seat of its Pass. Slot and player ids stay inside JSON with no foreign key, because `PRAGMA foreign_keys=ON` would otherwise make an Excel import's slot delete fail. Always assign a new dict or list to `fields` and `seats`, since JSON columns don't track mutation.
- Produces, in `admin/pending.py`:
  ```python
  @dataclass
  class SeatView:  # the same attribute names as Slot, so agenda.shift_key and group_shifts accept it
      ref: int; slot_id: int | None; entry_id: int | None
      date: date | None; start_time: time; end_time: time
      duty_name: str; venue: str; station: str; station_id: int | None
      note: str | None; player_id: int | None; child_name: str | None
      drawn: bool = False

  @dataclass(frozen=True)
  class Conflict:
      missing: bool; fields: tuple[str, ...]; seat_ids: tuple[int, ...]

  def team_entries(session, team_id: int) -> list[PendingPass]
  def pending_count(session, team_id: int) -> int
  def encode_slot(slot: Slot) -> dict  # the five field values for one live seat
  def conflict_of(entry, live: dict[int, Slot], player_ids: set[int]) -> Conflict | None
  def effective_seats(session, team_id: int, *, skip_conflicted: bool = False) -> list[SeatView]
  def entry_for_seats(entries, slot_ids) -> PendingPass | None
  def new_pass(team_id, activity, day, start, end, station_id, seats: int) -> PendingPass
  def edit_entry(team_id, slots: list[Slot]) -> PendingPass  # empty "edit" holding the Pass's seats
  def events_of(seats) -> dict[str, list[SeatView]]
  ```
  A new seat's `ref` is `-(entry.id * 1000 + index + 1)`; a live seat's `ref` is its `slot_id`. An event key is `"{duty_name}|{first ISO date}"` for a run of consecutive dates with one activity, and `"{duty_name}|"` for undated seats.
- Produces, in `tests/conftest.py`: `run_action(app, session_factory, person, name, *, apply=True, slug="f14-bla", **fields) -> Plan`, moved from `test_action_slot_edit._run`.

`conflict_of` rules: `missing` is true if any seat's `slot_id` no longer exists, or a seat's `to` player is not in `player_ids`. A field conflicts when the first live seat's encoded value differs from its `from`. A seat conflicts when its `to != from` and the live `player_id` differs from `from`, or when `kind == "remove"` and the live `player_id` differs from `from`. Entries of kind `new` can only be `missing` through their players.

`effective_seats` is live seats with no entry, plus each edit entry's overlaid seats, plus new entries' seats, and leaves out removals. With `skip_conflicted=True`, a conflicted entry counts as its live seats.

- [ ] **Step 1: Create the branch and write the failing tests**

```bash
git switch -c feat/vantande-andringar
```

In `test_pending.py`, using `seeded` and direct `PendingPass` rows:

```python
def test_a_swap_on_another_seat_does_not_conflict_with_a_staged_time(...):
    # entry stages start_time on seats A, B; A's player then changes live
    assert conflict_of(entry, live, players) is None

def test_a_staged_field_changed_elsewhere_conflicts(...):
    assert conflict_of(entry, live, players).fields == ("start_time",)

def test_a_deleted_seat_is_missing(...):
    assert conflict_of(entry, live_without_seat, players).missing is True

def test_a_staged_player_who_left_is_missing(...):
    assert conflict_of(entry, live, set()).missing is True

def test_effective_seats_overlay_edits_add_new_and_drop_removed(...):
    # dates, players and counts match the staged values; slots table is unchanged

def test_a_conflicted_entry_counts_as_live_when_skipped(...):

def test_events_split_on_a_gap_and_on_activity():
    # fri, sat, sun Cafépass -> one key "Cafépass|2026-11-20"
    # same Cafépass on 2026-11-28 -> a second key; a cup on the same fri -> its own key
    # undated -> "Cafépass|"

def test_other_teams_entries_are_never_loaded(...):

def test_deleting_a_staged_slot_is_not_refused_by_the_database(...):
```

In `test_migrate_admin.py`: `test_migrate_creates_pending_passes_on_an_existing_database` asserts the table exists after `migrate()` on a database created without it.

- [ ] **Step 2: Verify they fail**

Run: `uv run pytest tests/test_pending.py -v`
Expected: FAIL on importing `duty_web.admin.pending`.

- [ ] **Step 3: Implement the model and `admin/pending.py`**

No `NEW_COLUMNS` entry is needed, because `init_db` creates new tables.

- [ ] **Step 4: Verify they pass**

Run: `uv run pytest tests/test_pending.py tests/test_migrate_admin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/duty_web/models.py web/src/duty_web/admin/pending.py web/tests
git commit -m "feat(web): väntande pass sparas utanför slots"
```

---

### Task 2: Staging actions that apply at once

**Files:**
- Modify: `web/src/duty_web/admin/actions/base.py`, `web/src/duty_web/admin/actions/__init__.py`, `web/src/duty_web/admin/ratelimit.py`, `web/src/duty_web/routes/admin_routes.py`
- Create: `web/src/duty_web/admin/actions/pending_actions.py`
- Test: `web/tests/test_action_pending.py`, `web/tests/test_admin_parity.py`, `web/tests/conftest.py`

**Interfaces:**
- Consumes: Task 1's `pending` module.
- Produces, on `Action`: `immediate: ClassVar[bool] = False` ("Writes only väntande data, so the HTML door applies it without a Granska") and `review_template: ClassVar[str] = "admin/granska.html"`.
- Produces, actions all with `immediate = True`, `template = "admin/actions/generic.html"`, `done_endpoint = "admin.pass_tab"`:

  | Action | Input fields |
  |---|---|
  | `pending.edit` | `slot_ids: list[int] = []`, `entry_id: int \| None`, `day`, `start`, `end`, `note: str \| None`, `station_id: int \| None`, `seats: dict = {}` |
  | `pending.remove` | `slot_ids: list[int] = []`, `entry_id: int \| None` |
  | `pending.undo` | `entry_id: int` |
  | `pending.keep` | `entry_id: int` |
  | `pending.discard` | `confirm: bool` (required) |

- Produces: `pending_limiter = RateLimiter(600, 60)` in `ratelimit.py`, also reset by `_fresh_rate_limits`.
- Produces: the HTML response contract. With header `X-Autosave: 1`: `200 {"pending": n, "row": {"pending": bool, "changed": [names]}}`, `400 {"errors": {...}}` or `409 {"error": code}`. Without it: a redirect to `pass_tab`, or to `admin.granska_batch` when the form has `next=granska` (with `tyst=1` kept). `changed` uses `date`, `start_time`, `end_time`, `station_id`, `note` and `seat-<key>`.

`pending.edit` rules:
- `seats` keys are the live `slot_id` as a string, or `new-<index>` for a new Pass. A value of `""` means Ledigt.
- `parse` keeps `note=""` as "clear the note", reads `seat-<key>` form fields into `seats`, and leaves a field out when the form didn't send it.
- A blank `day`, `start`, `end` or `station_id` means "back to the live value".
- With `slot_ids`, the seats must share one `shift_key`, or `ValidationError({"slot_ids": "Välj platser från ett och samma pass"})`. Neither `slot_ids` nor `entry_id` gives `ValidationError({"slot_ids": "Välj ett pass"})`.
- A posted value equal to the current staged `to` changes nothing and does not rebase. A different value sets `to` and takes `from` from the live Pass now, which also clears a conflict on that field. An unstaged value equal to live is ignored.
- A field or seat whose `to` equals its `from` is dropped. An `edit` entry with nothing left is deleted.
- End time must be after start time, using effective values: `"Sluttiden måste vara efter starttiden"`. A player must be on the team: `"Spelaren finns inte i laget"`. One player twice on a Pass: `"Spelaren står redan på passet"`. Editing a `remove` entry: `"Passet är markerat för borttagning"`.

`pending.remove` on a live Pass creates a `remove` entry listing its seats, replacing any `edit` entry. On a `new` entry it deletes the entry. `pending.undo` deletes one entry. `pending.keep` sets each conflicted `from` to the live value and drops anything now equal, and raises `ValidationError({"entry_id": "Passet finns inte längre"})` when `missing`. `pending.discard` deletes the team's entries, and raises `ValidationError({"batch": "Inget väntande att slänga"})` when empty.

Every plan's `basis` has `slot_basis` for touched seats plus `[entry.id, entry.kind, entry.fields, entry.seats]`.

In `do_action`, POST:

```python
if action.immediate:
    plan, token = dry_run(session, ctx, action, inp, secret_key=key)
    apply_plan(session, ctx, token, secret_key=key, via="webb")
    return _staged_response(session, ctx, action, inp)
```

Immediate actions use `pending_limiter`; everything else keeps `admin_limiter`.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_edit_is_saved_as_vantande_and_the_pass_is_untouched(app, session_factory, seeded, admins):
    run_action(app, session_factory, admins["ansvarig"], "pending.edit",
               slot_ids=[seat_id], day="2026-11-20", start="17:30", end="19:30")
    assert session.get(Slot, seat_id).date is None
    assert entry.fields["date"] == {"from": None, "to": "2026-11-20"}

def test_changing_a_value_back_leaves_no_entry(...):  # Review Focus 3
    assert pending_count(session, team_id) == 0

def test_saving_one_cell_leaves_other_staged_values_alone(...):  # Review Focus 5
    # stage date and time, then post only note: date and time keep their `to`

def test_a_new_edit_rebases_only_that_field(...):
    # live start changes; re-posting the staged end leaves start conflicted

def test_keep_takes_live_values_as_the_new_starting_point(...):
def test_keep_refuses_a_missing_seat(...):
def test_undo_and_discard_remove_entries(...):
def test_remove_of_a_new_pass_deletes_the_entry(...):
def test_one_player_twice_on_a_pass_is_refused(...):
def test_another_teams_slot_is_unknown(...):

def test_html_door_applies_at_once_and_answers_autosave(client, admins, csrf_token, ...):
    login(client, admins["ansvarig"])
    r = client.post("/admin/f14-bla/do/pending.edit", headers={"X-Autosave": "1"},
                    data={"csrf_token": csrf_token(), "slot_ids": [seat_id], "note": "Lördagar"})
    assert r.status_code == 200 and r.get_json()["pending"] == 1
    assert r.get_json()["row"] == {"pending": True, "changed": ["note"]}

def test_api_door_keeps_dry_run_then_token(client, api_keys, ...):
    # dry_run writes nothing; the second call with plan_token creates the entry

def test_autosaving_many_cells_does_not_hit_the_form_limit(client, ...):
    # 100 posts in a row all answer 200
```

Add the five names to `test_the_registry_has_the_v1_actions`.

- [ ] **Step 2: Verify they fail**

Run: `uv run pytest tests/test_action_pending.py tests/test_admin_parity.py -v`
Expected: FAIL with unknown action or missing registry names.

- [ ] **Step 3: Implement the attributes, limiter, actions and route branch**

- [ ] **Step 4: Verify they pass**

Run: `uv run pytest tests/test_action_pending.py tests/test_admin_parity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat(web): ändringar sparas som väntande direkt"
```

---

### Task 3: Nothing leaks before Genomför

**Files:**
- Test: `web/tests/test_pending_isolation.py`

**Interfaces:**
- Consumes: Task 2's actions.

This task is tests only and pins the first anti-goal before publish exists.

- [ ] **Step 1: Write the tests**

```python
def test_parents_see_no_vantande_edit_or_pass(client, admins, seeded, ...):
    # stage a new date and a pending.remove; snapshot /api/schedule, the team page and the
    # parent's own page before and after; responses are identical

def test_staging_sends_no_mail(monkeypatch, ...):
    # replace slot_ops.send_shift_moved and send_swap_cancelled; stage on a seat with a pending swap
    assert sent == []

def test_staging_leaves_pending_swaps_alone(...):
    assert swap.status == "pending"
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_pending_isolation.py -v`
Expected: PASS. A failure means staging reached `slots`, so fix Task 2.

- [ ] **Step 3: Commit**

```bash
git add web/tests/test_pending_isolation.py
git commit -m "test(web): väntande syns inte för föräldrar och mejlas inte"
```

---

### Task 4: One Granska for the batch

**Files:**
- Modify: `web/src/duty_web/admin/actions/pending_actions.py`, `web/src/duty_web/admin/actions/base.py`, `web/src/duty_web/routes/admin_routes.py`, `web/src/duty_web/routes/admin_api.py`
- Create: `web/src/duty_web/templates/admin/granska_vantande.html`, `web/src/duty_web/templates/admin/_plan_notes.html`
- Modify: `web/src/duty_web/templates/admin/granska.html`, `web/src/duty_web/static/admin.css`
- Test: `web/tests/test_action_pending_publish.py`

**Interfaces:**
- Consumes: Task 1 and 2.
- Produces: `Plan.review: list[dict] = field(default_factory=list, repr=False, compare=False)`, each `{"entry_id", "day", "op", "summary", "detail", "conflict": str | None, "missing": bool, "drawn": bool}`.
- Produces: `Action.agent_note: ClassVar[str] = ""`, returned as `"agent_note"` for each action in `GET /admin/api`.
- Produces: action `pending.publish`, `Input(notify: bool = True)`, `immediate = False`, `review_template = "admin/granska_vantande.html"`, `done_endpoint = "admin.pass_tab"`.
- Produces: `GET /admin/<slug>/granska`, endpoint `admin.granska_batch`. It runs `dry_run` on `pending.publish` with `notify=not request.args.get("tyst")` and renders through `_granska`. An empty batch redirects to `pass_tab`.

`_granska` renders `action.review_template`. The `{% for warning %}`, notes and mail-switch blocks move to `_plan_notes.html`, included by both Granska templates.

`pending.publish` rules:
- No entries: `ValidationError({"batch": "Inget väntande att genomföra"})`.
- Sort entries with `slot_order` semantics. One `Change` per non-conflicted Pass: `update` for edit, `add` for new, `delete` for remove. Summary is the shift label after the change. Detail lists the differences, in the wording `slot.update` uses today ("datum satt: …", "tid … → …", "station … → …", "… → Ledigt"), and for a new Pass "N platser".
- Conflicted entries go only into `review`, with `conflict` naming what changed, such as "Ändrat sedan det lades in: tid, spelare". Add `plan.warnings.append(f"{n} pass behöver ses över och följer inte med.")`.
- Warnings use `effective_seats(skip_conflicted=True)`. Double booking: `f"{name} har redan ett pass {day_label(when)}."`, once per player and day. Exempt: `f"{name} är undantagen (ledarfamilj)."`. New-Pass overlap: `f"Laget har redan ett pass som överlappar: {label}"`.
- Notes keep today's staffing summary for players gaining seats: `f"{name}: {before} → {after} pass"` and `f"Laget: i snitt {mean} pass per spelare, spridning {width}."`.
- Mail: call `plan_shift_notices` with every edited seat and its `Slot` field dict, then `plan_swap_cancellations(changed=..., deleted=..., mailed=...)`, then append the action's own write.
- Writes: `setattr` on live seats, using `owner_fields` for players; `date` and the times decoded; a station change also sets `station` and `venue`. New Pass become `Slot(origin=ORIGIN_ADMIN, ...)`. Removals delete seats. Published entries are deleted; conflicted ones stay.
- `basis` covers every entry and every live seat it touches, so a batch change during review is a stale plan.
- `preview_dates` and `preview_undated` are filled for the phone preview.

The publish class carries this comment, exactly:

```python
# ─────────────────────────────────────────────────────────────────────────
# AGENTS: READ THIS BEFORE CALLING pending.publish.
#
# Publishing changes what parents see and can send them mail. It is hugely
# preferable that a human reviews the batch in Granska and presses Genomför.
#
# You may publish ONLY when you have been explicitly asked to publish AND
# you have received approval for this specific publish. Staging changes
# with the pending.* actions and leaving them for a person is the default.
# ─────────────────────────────────────────────────────────────────────────
```

`agent_note` for this action: "Låt helst en människa granska och genomföra. Agenter får bara genomföra på uttrycklig begäran och efter godkännande av just den här publiceringen."

`granska_vantande.html`: title "Granska väntande ändringar", the `.diff-sum` pills, `_plan_notes.html`, entries grouped by `day` under a heading per day ("Datum ej satt" last), each with **Ångra** (a form posting `pending.undo` with `next=granska` and `tyst` kept). A conflicted entry shows its text and **Behåll min ändring** (`pending.keep`), unless `missing`. Then "Genomför N ändringar" (N = `plan.changes|length`), the phone preview, **Tillbaka och ändra** to `pass_tab`, and **Släng alla väntande** (`pending.discard`, `confirm=1`). Reuse `.diff-row` and the `+ ~ −` signs.

- [ ] **Step 1: Write the failing tests**

```python
def test_publish_applies_the_whole_batch_in_one_go(...):
    # three edits and a new Pass; slots updated; pending_passes empty

def test_a_moved_shift_with_a_player_is_mailed_once_and_the_switch_mutes_it(mails, ...):
def test_placing_a_player_and_a_first_date_send_no_mail(mails, ...):
def test_a_cancelled_swap_is_mailed_even_when_muted(mails, ...):

def test_a_conflicted_pass_stays_vantande_and_the_rest_publishes(...):
    assert remaining == [conflicted.id]

def test_an_excel_deleted_seat_offers_only_undo_and_the_rest_publishes(client, ...):  # Review Focus 1
    page = client.get("/admin/f14-bla/granska").get_data(as_text=True)
    assert "Ångra" in page and "Behåll min ändring" not in page

def test_a_staged_player_who_left_is_flagged_not_a_crash(...):  # Review Focus 2

def test_double_booking_counts_other_vantande_edits(...):
    assert "Ida Ny har redan ett pass" in " ".join(plan.warnings)

def test_a_change_during_review_gives_a_stale_plan(...):
    with pytest.raises(PlanTokenError) as err: ...
    assert err.value.code == "stale_plan"

def test_genomfor_twice_is_stale_and_mails_once(client, mails, ...):  # Review Focus 4
    assert second.status_code == 409 and len(mails) == 1

def test_granska_keeps_mute_after_undo(client, ...):
def test_empty_granska_goes_back_to_pass(client, ...):
def test_granska_has_names_and_never_addresses(client, ...):

def test_discovery_warns_agents_about_publishing(client, api_keys):
    body = client.get("/admin/api", headers=bearer(api_keys["club"])).get_json()
    assert "uttrycklig begäran" in body["actions"]["pending.publish"]["agent_note"]

def test_the_source_warns_agents():
    assert "AGENTS: READ THIS BEFORE CALLING pending.publish" in Path(pending_actions.__file__).read_text(encoding="utf-8")
```

Add `pending.publish` to the registry test.

- [ ] **Step 2: Verify they fail**

Run: `uv run pytest tests/test_action_pending_publish.py -v`
Expected: FAIL with unknown action.

- [ ] **Step 3: Implement `pending.publish`, `Plan.review`, the route, the template and `agent_note`**

- [ ] **Step 4: Verify they pass**

Run: `uv run pytest tests/test_action_pending_publish.py tests/test_admin_parity.py tests/test_admin_html.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat(web): en Granska för alla väntande ändringar"
```

---

### Task 5: The Pass table and autosave

**Files:**
- Create: `web/src/duty_web/admin/pass_rows.py`, `web/src/duty_web/static/admin_pass.js`
- Modify: `web/src/duty_web/routes/admin_routes.py`, `web/src/duty_web/templates/admin/pass.html`, `web/src/duty_web/static/admin.css`, `web/src/duty_web/admin/actions/slot_edit.py` (delete), `web/src/duty_web/admin/actions/__init__.py`
- Delete: `web/src/duty_web/templates/admin/actions/slot_update.html`, `web/src/duty_web/templates/admin/actions/slot_delete.html`, `web/tests/test_action_slot_edit.py`
- Test: `web/tests/test_admin_pass.py` (rewrite), `web/tests/test_admin_parity.py`

**Interfaces:**
- Consumes: Task 1, 2, 4.
- Produces:
  ```python
  @dataclass
  class SeatCell:
      key: str; player_id: int | None; changed: bool; drawn: bool
      swapping: bool; hint: str | None   # hint is the schedule's spelling on an open seat

  @dataclass
  class PassRow:
      entry_id: int | None; slot_ids: list[int]; status: str   # "live" | "edit" | "new" | "remove"
      conflict: bool; date: date | None; start: str; end: str
      duty_name: str; station_id: int | None; station: str; note: str | None
      seats: list[SeatCell]; changed: set[str]; open_seats: int

  def pass_view(session, ctx, *, include_past: bool) -> list[dict]
  # [{"key": event key, "title": str, "open_seats": int,
  #   "days": [{"date": date | None, "rows": [PassRow]}]}]
  ```
  Dated events come first in date order, then undated. Rows are entry rows plus `group_shifts` of live seats with no entry. A row sits under its effective date. Past rows are left out unless `include_past`. An event with nothing to show is left out.

`pass_tab` passes `events=pass_view(...)`, `pending=pending_count(...)`, `players`, `stations`, `open_seats` and keeps `include_past`. The `Klart` notice shows only for `klart` values from non-immediate actions.

`pass.html`:
- Keep the header, `+ Lägg till pass`, Importera Excel, mall and "Visa tidigare".
- A `<table class="pass-table">` with one `<tbody>` per event, and a heading row per day ("Datum ej satt" for undated). The Slumpa button slot is added in Task 6.
- Each row has `<form id="row-…">` in its first cell, and its inputs use `form="row-…"`, as table rows can't contain forms. The form carries the CSRF token and hidden `slot_ids` or `entry_id`.
- Cells: `<input type="date" name="day">`, `start`, `end` as `.admin-input.time`, `<select name="station_id">` with "Välj plats", `seat-<key>` selects with "Ledigt" and team players (exempt ones suffixed " (ledarfamilj)"), a small `note` input, the "Byte väntar" tag where `swapping`, and **Ta bort** (`pending.remove`) or **Ångra** (`pending.undo`) as a `<button form=…>` with `formaction`.
- `<noscript><button class="btn btn-sm">Spara</button></noscript>` per row.
- Orange means `tr.pend`; a changed cell gets `.cell-ch`. A removed row is `tr.gone` with struck-through text and disabled inputs. A flagged row shows "Behöver ses över".
- A `.pending-bar`, sticky at the bottom, when `pending > 0`, linking to `admin.granska_batch` with a "Granska" primary button.

`admin_pass.js`, loaded with `defer` from `pass.html`: on `change` of a row input, POST `FormData` with `csrf_token`, `slot_ids` or `entry_id`, and only the changed field, with header `X-Autosave: 1`. On 200 update the row's `pend` class, the changed cells' `.cell-ch` from `row.changed`, and the bar's count and copy, adding the bar if absent. On 400 show the first error in a `role="alert"` element in the row. On 409 show "Något ändrades samtidigt. Ladda om sidan." Never move a row.

Delete `slot.update` and `slot.delete`, and remove them from the registry test. Before deleting `slot_edit.py`, confirm Task 4 covers its behaviours by comparing test names with `test_action_slot_edit.py`.

- [ ] **Step 1: Write the failing tests** (replace the old staffing and link tests)

```python
def test_every_pass_is_a_row_with_editable_cells(client, admins, seeded):
    assert 'name="day"' in page and 'name="station_id"' in page and "seat-" in page

def test_a_vantande_edit_shows_on_the_pass_page_after_coming_back(client, ...):
    assert 'class="pass-row pend"' in page and "1 väntande ändring, inga mejl skickade" in page

def test_rows_show_the_staged_value_under_the_staged_date(...):
def test_a_removed_row_is_struck_through(...):
def test_a_new_pass_is_a_row_before_it_exists_in_slots(...):
def test_no_bar_when_nothing_is_vantande(...):
def test_past_rows_stay_hidden_until_asked_for(...):
def test_the_table_has_names_and_never_addresses(...):
def test_the_old_edit_pages_are_gone(client, admins):
    assert client.get("/admin/f14-bla/do/slot.update").status_code == 404
def test_another_ansvarig_sees_the_same_batch(client, admins, ...):
def test_rows_post_without_javascript(client, ...):
    # whole-row POST with no X-Autosave redirects to the Pass page
```

- [ ] **Step 2: Verify they fail**

Run: `uv run pytest tests/test_admin_pass.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `pass_rows.py`, the route, the template, CSS and script, and delete the old action**

- [ ] **Step 4: Verify the full suite passes**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: See it working**

Start the app with `just web-run` against a database with a team and undated Pass, then use the `run` or `claude-in-chrome` skill. Change a date, reload to confirm it stayed, confirm the row is orange and the bar counts it, and open Granska. Check that a wide table doesn't break the page at phone width, and check keyboard focus and dark mode.

- [ ] **Step 6: Commit**

```bash
git add -A web/src web/tests
git commit -m "feat(web): Pass blir en redigerbar tabell med väntande rader"
```

---

### Task 6: Slumpa lediga per event

**Files:**
- Create: `web/src/duty_web/admin/draw.py`
- Modify: `web/src/duty_web/admin/actions/pending_actions.py`, `web/src/duty_web/templates/admin/pass.html`, `web/src/duty_web/templates/admin/granska_vantande.html`, `web/src/duty_web/routes/admin_routes.py`, `web/src/duty_web/admin/actions/__init__.py`
- Delete: `web/src/duty_web/admin/actions/slots_assign.py`, `web/src/duty_web/templates/admin/actions/slots_assign.html`, `web/tests/test_action_slots_assign.py`
- Test: `web/tests/test_action_pending_draw.py`, `web/tests/test_admin_parity.py`

**Interfaces:**
- Consumes: `effective_seats`, `events_of`, `edit_entry`, `staffing.assign`, `staffing.season_of`.
- Produces: `draw_seats(empty: list[SeatView], everything: list[SeatView], players: dict[int, Player], *, seed: int, season: tuple[date, date]) -> list[Filled]`, with `Seat.slot_id` holding `SeatView.ref`. It moves today's load, held-days and occupied counting from `slots_assign.plan` and applies them to effective seats.
- Produces: action `pending.draw`, `immediate = True`, `template = generic`, `Input(event: str | None = None, seed: int | None = None, redraw: bool = False)`.

Rules:
- `event` is required unless `redraw`: `ValidationError({"event": "Välj ett evenemang"})`. An unknown key gives `"Okänt evenemang"`.
- A draw fills empty effective seats in that event only, dated today or later or undated, and skips conflicted entries and removals.
- A multi-season event draws the season of its first dated seat. Pressing again draws the next.
- Without a `seed`, `parse` picks one, so `dry_run` and `apply_plan` agree. An undated event uses `season_of(date.today())`.
- A draw on a live Pass with no entry creates one with `edit_entry`. Each drawn seat gets `to=player_id, drawn=True`.
- `redraw` clears every `drawn` seat in the event, or in the whole batch when `event` is None, then draws them again. A seat picked by hand has `drawn: False` and is never touched. Manually changing a drawn seat in `pending.edit` sets `drawn` to False.
- Seats nobody can fill stay Ledigt, and the reason is a plan warning as today.

`pass.html`: each event's heading has a `Slumpa lediga` form when `event.open_seats > 0`, whether or not anything is väntande. `granska_vantande.html`: **Slumpa om** posts `pending.draw` with `redraw=1`, `next=granska`, shown when any `review` entry has `drawn`. Remove `slots.assign` from the registry test.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_draw_fills_only_that_event(...):
    # two weekends with empty seats; only the drawn weekend's entries have names

def test_drawn_names_are_vantande_and_slots_stay_empty(...):
def test_the_fewest_shifts_goes_first_counting_vantande_placements(...):
def test_one_shift_a_day_counts_other_vantande_edits(...):
def test_a_new_pass_can_be_drawn_before_it_exists_in_slots(...):
def test_same_seed_same_result_between_dry_run_and_apply(...):
def test_redraw_keeps_names_picked_by_hand(...):
def test_editing_a_drawn_seat_makes_it_hand_picked(...):
def test_an_event_across_seasons_is_drawn_one_season_at_a_time(...):
    # 2027-07-31 and 2027-08-01 Cafépass: only the July seats are filled by the first press
def test_exempt_and_unreachable_players_are_not_drawn(...):
def test_the_button_shows_without_vantande_edits(client, ...):
    assert "Slumpa lediga" in page
```

- [ ] **Step 2: Verify they fail**

Run: `uv run pytest tests/test_action_pending_draw.py -v`
Expected: FAIL with unknown action.

- [ ] **Step 3: Implement `draw.py` and `pending.draw`, update the templates, delete `slots.assign`**

- [ ] **Step 4: Verify the full suite passes**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A web/src web/tests
git commit -m "feat(web): Slumpa lediga per evenemang, som väntande"
```

---

### Task 7: Lägg till pass stages its result

**Files:**
- Modify: `web/src/duty_web/admin/actions/slots_create.py`, `web/src/duty_web/templates/admin/actions/slots_create.html`
- Test: `web/tests/test_action_slots_create.py`

**Interfaces:**
- Consumes: `pending.new_pass`.

`CreateSlots`: `immediate = True`. The write adds one `new_pass(...)` per shift instead of `Slot` rows. The note becomes `f"{total} lediga platser väntar på att bemannas."` (singular "1 ledig plats väntar på att bemannas."). The submit button reads "Lägg till som väntande". All validation stays. `_overlaps` stays for the API's dry-run, and publish warns again against the batch.

- [ ] **Step 1: Update the tests**

```python
def test_create_stages_new_passes_and_writes_no_slots(app, session_factory, ...):
    assert session.query(Slot).count() == before
    assert [e.kind for e in entries] == ["new", "new"]
    assert entries[0].seats == [{"slot_id": None, "from": None, "to": None, "drawn": False}] * 2

def test_the_form_goes_back_to_the_table(client, ...):
    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/f14-bla/pass")
```

Rewrite the existing assertions that read `Slot` after apply so they read `PendingPass`, or publish first where they check `Slot` values.

- [ ] **Step 2: Verify they fail**

Run: `uv run pytest tests/test_action_slots_create.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the change**

- [ ] **Step 4: Verify they pass**

Run: `uv run pytest tests/test_action_slots_create.py tests/test_admin_parity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat(web): Lägg till pass blir väntande"
```

---

### Task 8: Café weekend

**Files:**
- Modify: `web/src/duty_web/duties.py`, `web/src/duty_web/routes/admin_api.py`, `web/src/duty_web/admin/actions/__init__.py`, `web/src/duty_web/templates/admin/pass.html`
- Create: `web/src/duty_web/admin/actions/slots_pattern.py`, `web/src/duty_web/templates/admin/actions/slots_pattern.html`
- Test: `web/tests/test_action_slots_pattern.py`, `web/tests/test_admin_parity.py`, `web/tests/test_duties.py`

**Interfaces:**
- Consumes: `pending.new_pass`, `_station`, `_days_from` and `MAX_DAYS` from `slots_create`.
- Produces, in `duties.py`:
  ```python
  @dataclass(frozen=True)
  class WeekendPattern:
      name: str; title: str; activity: str
      sessions: dict[int, tuple[tuple[str, str], ...]]  # weekday (Mon=0) -> (start, end)
      seats: int; seat_choices: tuple[int, ...]

  CAFE_SATURDAY = (("08:30", "10:30"), ("10:30", "12:30"), ("12:30", "14:30"),
                   ("14:30", "16:30"), ("16:30", "18:30"))
  PATTERNS = {"cafe_weekend": WeekendPattern(
      name="cafe_weekend", title="Cafépass, helg", activity="Cafépass",
      sessions={4: (("17:30", "19:30"),), 5: CAFE_SATURDAY, 6: CAFE_SATURDAY},
      seats=2, seat_choices=(2, 3),
  )}

  def pattern_sessions(pattern: WeekendPattern, days: list[date]) -> list[tuple[date, str, str]]
  ```
- Produces: action `slots.from_pattern`, `title = "Lägg till cafépass"`, `immediate = True`, `Input(days: list[str], pattern: str = "cafe_weekend", station_id: int | None = None, seats: int = 2, rows: list[dict] = [])`. Each row is `{"day", "start", "end"}`. `parse` builds `days` as `_days_from` does.
- Produces: `GET /admin/api` gains `"patterns": {name: {"title", "activity", "sessions": {"fre": [["17:30","19:30"]], ...}, "seats", "seat_choices"}}`.

Rules:
- Empty `rows` uses `pattern_sessions`; given `rows` replace it, for adjusted sessions. Validate clocks and end after start with the messages in `slots_create`.
- A span with no matching weekday: `ValidationError({"days": "Inga fredagar, lördagar eller söndagar i de valda dagarna"})`. Unknown pattern: `"Okänt mönster"`. `seats` outside `seat_choices`: `"Välj 2 eller 3 föräldrar per pass"`.
- With no `station_id`, use the station matching `profile_for("Cafépass")`. If none exists: `ValidationError({"station_id": "Välj en plats"})`.

Form, with GET params `date_from`, `date_to`: chips for the next four weekends labelled "v 47" (ISO week) as links setting the two dates, plus "Nästa helg". Date inputs "Från" and "Till". Station select with the default chosen. "Föräldrar per pass" radios 2 and 3. Three day columns of editable rows, each with start, end and a "Ta bort" checkbox, named `row-<i>-day/start/end/skip`. The button copy is "Lägg till N pass som väntande". The Pass page gets "+ Lägg till cafépass" as its primary button, beside "+ Lägg till pass", which becomes secondary.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_fri_sun_span_generates_exactly_eleven_sessions():
    got = pattern_sessions(PATTERNS["cafe_weekend"], [date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22)])
    assert len(got) == 11
    assert got[0] == (date(2026, 11, 20), "17:30", "19:30")
    assert [(s, e) for d, s, e in got if d == date(2026, 11, 21)] == [
        ("08:30", "10:30"), ("10:30", "12:30"), ("12:30", "14:30"), ("14:30", "16:30"), ("16:30", "18:30")]

def test_a_wider_span_only_uses_fri_sat_sun():
    # Monday to Sunday of v47 still gives 11

def test_a_sat_sun_span_has_no_friday():
    # 10 sessions

def test_two_weekends_in_one_span_make_two_sets():
    # 22

def test_the_action_stages_two_seats_per_pass_at_the_default_cafe(...):
    assert {e.fields["station_id"]["to"] for e in entries} == {arena_b.id}
    assert all(len(e.seats) == 2 for e in entries)
    assert session.query(Slot).count() == before

def test_three_seats_applies_to_the_whole_weekend(...):
def test_four_seats_is_refused(...):
def test_adjusted_rows_replace_the_pattern(...):
def test_a_span_with_no_matching_day_is_refused(...):
def test_all_teams_get_the_same_pattern(...):
def test_discovery_describes_the_pattern(client, api_keys):
    pattern = client.get("/admin/api", headers=bearer(api_keys["club"])).get_json()["patterns"]["cafe_weekend"]
    assert pattern["activity"] == "Cafépass" and pattern["seats"] == 2

def test_an_agent_can_ask_for_it_by_name(client, api_keys, ...):
    # POST /admin/api/slots.from_pattern with team, date_from, date_to, dry_run true gives 11 add changes
```

Add `slots.from_pattern` to the registry test.

- [ ] **Step 2: Verify they fail**

Run: `uv run pytest tests/test_action_slots_pattern.py tests/test_duties.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the pattern, the action, discovery and the form**

- [ ] **Step 4: Verify they pass**

Run: `uv run pytest tests/test_action_slots_pattern.py tests/test_duties.py tests/test_admin_parity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A web/src web/tests
git commit -m "feat(web): skapa en cafépass-helg i ett steg"
```

---

### Task 9: Docs and end-to-end check

**Files:**
- Modify: `web/README.md`
- Modify: `docs/superpowers/specs/2026-11-08-vantande-andringar-design.md` (status line)

- [ ] **Step 1: Update `web/README.md`**

Replace the "Bemanna valda platser" paragraph and the `seed` note with: the table, väntande, the one Granska, Slumpa per evenemang and "Lägg till cafépass". In the agent section, state that staging uses `pending.*` and that `pending.publish` is for a human by default, repeating the rule in the code comment.

- [ ] **Step 2: Check the scripts still make sense**

Run: `uv run pytest tests/test_admin_push.py -v`
Expected: PASS. `scripts/admin_push.py` only drives `roster.import` and `schedule.import`, which are unchanged. Change it only if this fails.

- [ ] **Step 3: Run the full suites**

Run: `cd .. && just test-all`
Expected: both suites PASS.

- [ ] **Step 4: Walk through the real flow in a browser**

With `just web-run`: add a café weekend for v47 as väntande, check the 11 rows, press Slumpa lediga on that event only, change one player by hand, undo one Pass in Granska, press Slumpa om and confirm the hand-picked name stayed, then Genomför. Confirm the parent view shows the Pass only after Genomför.

- [ ] **Step 5: Mark the spec implemented and commit**

Set the spec's status line to `Implemented`.

```bash
git add web/README.md docs/superpowers/specs/2026-11-08-vantande-andringar-design.md
git commit -m "docs(web): väntande ändringar och Pass-tabellen"
```
