# Spec: Cron schedules replace interval-only triggers

Intent: docs/tasks/H27-door-nouns-schedules/intent.md

Author: adam aubry (operator). Status: approved.

## Requirements

1. `src/sadana/cron.py` is a new, pure module: `parse(expr: str) -> Cron |
   CronError` accepts the five-field form (minute, hour, day-of-month,
   month, day-of-week) with `*`, comma lists, ranges (`a-b`) and steps
   (`*/n`, `a-b/n`); no named weekdays/months, no `L`/`W`/`#`. (Intent:
   Constraints — expressible recurrence forms.)
2. `next_after(cron: Cron, tz: str, after: float) -> float` returns the
   first occurrence strictly after `after`, computed in `tz` via stdlib
   `zoneinfo`, and always advances in real elapsed time (epoch seconds),
   never by re-deriving from a stale wall-clock label. (Intent: proposed
   outcome — "every Monday at 9am"; constraint — no dependency.)
3. A `schedules` table replaces `scheduled_triggers` as the thing the
   console reads and writes; `scheduled_triggers` is not migrated in place
   — every existing row is copied once, idempotently, into `schedules` on
   store open, and the old table is left in place, empty of meaning.
   (Intent: affected systems — nothing currently scheduled is dropped.)
4. An account using the console can, through the door: create a schedule
   (`name`, `cron`, `timezone?`, `plugin_id?`, `conversation_id?`,
   `trigger_text`), read one or list its own, update one, pause it, resume
   it, and remove it. (Intent: proposed outcome.)
5. A schedule is visible and actionable only to the account that created
   it; another account's schedule is `404`, never `403`. (Intent: affected
   users — per-account scoping.)
6. A paused schedule never fires. Resuming one recomputes its next
   occurrence from the moment of resume, not from whatever `next_run_at`
   was left showing while paused. (Intent: proposed outcome; constraint —
   no catch-up.)
7. A cron-based schedule that missed its window while the box was down
   re-arms for the next real future occurrence; it does not fire once to
   catch up. (Intent: constraints — no catch-up, matching the existing
   interval-trigger posture.)
8. Every write to a schedule (create, update, pause, resume, remove, and a
   tick's own post-fire advance) is recorded in the ledger. (Intent:
   constraints — nothing applied silently.)
9. Firing a schedule takes only that one conversation's own lock, never a
   process-wide one — unrelated conversations are unaffected while a
   schedule fires. (Intent: constraints.)
10. `door/capabilities.py` declares `schedules.write`, gating create,
    update, remove, pause and resume; list/get need no capability, only the
    existing scope check every verb already gets from `router.py`.
11. `nouns.md`'s `schedule` section is filled in, and `schedules` is added
    to the door's conformance test suite, matching every other served noun.

## Design

One new pure module (`cron.py`), one new table plus its accessors, one
new noun module, and a two-line addition to an already-running background
loop — nothing here is a new subsystem. The subsections below work through
the four design guidelines in order and then lay out where each piece of
code actually lives.

### Learning from the reference (guideline 1)

`docs/reference/hermes_core_blocks_kind.csv`'s `SCHEDULING` block
(`cron/jobs.py`, `cron/scheduler.py`, `plugins/cron_providers/chronos/`) is
the analogous prior art. Two things adopted, one declined.

**Adopted: recompute-on-resume from now, never from a stale stored value.**
`cron/jobs.py:2720` (`resume_job`) calls `compute_next_run(job["schedule"])`
fresh rather than reusing whatever `next_run_at` was left showing when the
job was paused. This is exactly requirement 6's mechanism, and this design
uses the same shape: `resume` always calls `cron.next_after(cron, tz,
ctx.clock())`, never reads back the row's stored `next_run_at`.

**Adopted (in spirit): tombstone-before-delete.** Not from `SCHEDULING`
itself but from this project's own `memory_store.delete_entry`
(`src/sadana/memory_store.py:194-209`) — the ledger's `deleted` row is
written first, inside the same transaction, because afterwards there is no
id left to name it by. `schedules.remove` (conversation_store's
`remove_schedule`) follows the identical two-step transaction.

**Declined: `croniter` as a runtime dependency.** `cron/jobs.py:52-58`
lazily imports `croniter` and raises `ValueError` at parse time if it's
missing; every cadence computation in that file goes through it. This
project's own rule for this exact work item is "no dependency for cron" —
not a preference, a constraint already fixed at the intent stage, and
separately consistent with `console_fit_plan.md`'s closed
three-dependency list. The five-field subset this door serves (no names,
no `L`/`W`/`#`) is small enough that a hand-rolled parser plus a
day-then-minute search (below) is well under a hundred lines, so declining
the dependency costs little. `croniter`'s own richer feature set (seconds
field, `L`/`W`/`#`, named months/weekdays) is exactly what's declined
alongside it — this door serves standard five-field cron only.

**Declined: JSON-file-plus-`fcntl` storage.** `cron/jobs.py` stores jobs in
`~/.hermes/cron/jobs.json` behind advisory file locking, with a parallel
"effective state" reducer (`_has_pause_marker`, `paused_at`, a `state`
column that can each say something different) to paper over concurrent
writers. This project already has a settled answer to concurrent writers —
H16's one-writer-connection-plus-`write_txn` model, already governing every
other table `conversation_store.py` owns — and introducing a second
storage/locking mechanism for one more table would reopen a question H16's
own CLAUDE.md entry already closed. `schedules` is one more SQLite table
behind the existing writer lock; state is the two-value `active`/`paused`
column the DDL already names, nothing else tracks it.

Nothing in `SCHEDULING` addresses per-account visibility or a wire-facing
CRUD/action grammar — hermes's cron jobs are single-tenant and CLI/JSON-only
— so requirements 4, 5 and 10 have no reference analogue; they follow this
door's own established noun shape (`door/nouns/approvals.py`,
`door/nouns/memory_entries.py`) instead.

### Where the new code lives (project-structure)

- `src/sadana/cron.py` — new, pure. No I/O, no import of `conversation_store`
  or anything in `sadana.door`.
- `src/sadana/conversation_store.py` — the `schedules` table DDL (given
  verbatim in this work item's own brief), a `ScheduleRow` dataclass, and:
  `create_schedule`, `get_schedule`, `list_schedules(account_key)`,
  `update_schedule`, `set_schedule_state` (pause/resume), `remove_schedule`,
  `due_schedules(now)` (unscoped — a tick fires every account's due rows),
  `advance_schedule` (the post-fire `next_run_at`/`last_run_at`/`last_state`
  write). Same file as `scheduled_triggers`'s own accessors — one I/O module
  per store, not one per table.
- `src/sadana/client_surface.py` — `_adopt_scheduled_triggers(conn)`, called
  from `open_runtime()` right beside `_adopt_scheduled_memories`, same
  try/except-and-log-and-continue posture (PERSONA-02's own precedent).
- `src/sadana/scheduling.py` — `tick()` gains a `now: float | None = None`
  parameter (`None` means "read the clock", the default in
  `run_tick_loop`); the due-trigger loop and the due-schedule loop both run
  inside one tick, each independently best-effort per requirement 8's
  existing per-item try/except.
- `src/sadana/door/nouns/schedules.py` — new, the six verbs (`list`, `get`,
  `create`, `update`, `remove`, `act`) plus `search_doc`, matching every
  other noun module's shape.
- `src/sadana/door/capabilities.py` — append `"schedules.write"` to
  `DECLARED` (already present in the closed `ALL` sixteen).
- `src/sadana/subcommands/door.py` — import `schedules` and add
  `"schedules": schedules` to `cmd_door_serve`'s `nouns={...}` dict, the one
  registration point every served noun already goes through.
- `docs/console/nouns.md` — fill in `## schedule`.
- `tests/contract/nouns/test_schedules.py` — new, alongside
  `test_approvals.py`/`test_memory_entries.py`.

Nothing here needs `plugin_id`/`conversation_id` resolution machinery that
doesn't already exist (see below), and nothing needs a new noun-specific
schema in `docs/console/grammar.json` — that file is noun-agnostic
(`ListResponse`/`Problem`/etc.), matching every other noun's own spec.

### `cron.py`'s shape

```
@dataclass(frozen=True)
class Cron:
    minute: frozenset[int] | None       # None means "*"
    hour: frozenset[int] | None
    day_of_month: frozenset[int] | None
    month: frozenset[int] | None
    day_of_week: frozenset[int] | None   # 0-6, 0 = Sunday

@dataclass(frozen=True)
class CronError:
    message: str

def parse(expr: str) -> Cron | CronError: ...
def next_after(cron: Cron, tz: str, after: float) -> float: ...
```

`parse` returns a value rather than raising, unlike `door/filter.py`'s own
`_SyntaxError`-and-raise convention. That's a deliberate difference, not an
inconsistency: `schedules.create` has to validate **two** independent
fields (`cron` and `timezone`) and report both as separate `errors[]`
entries in one `400`, and a returned `Cron | CronError` composes with a
second, unrelated `zoneinfo` check without either one being inside the
other's `try`/`except`.

**Day-of-week convention.** Standard cron: `0`-`6`, `0` = Sunday. No `7`-as-
Sunday alias — nothing in this work item's grammar asks for it, and adding
it would be an unrequested extension of a spec that explicitly excludes the
other common vixie-cron extensions (`L`/`W`/`#`, names).

**Day-field OR semantics.** When both `day_of_month` and `day_of_week` are
restricted (neither is bare `*`), standard five-field cron matches a
candidate day that satisfies *either* field, not both — this is not an
extension, it's what "five-field cron" has meant since V7 `cron(1)`, and
silently doing AND instead would be a correctness bug, not a
simplification. Implemented as: a day is a candidate if
`day_of_month is None or day_of_month_matches`, evaluated with `OR` rather
than `AND` specifically when both are non-`None`.

**`next_after`'s search.** Two nested loops, not one flat minute-by-minute
scan over the whole search window: an outer loop advances one calendar day
at a time (checking month/day-of-month/day-of-week) so a yearly schedule
doesn't cost a year of per-minute checks, and only on a day that already
matches does an inner loop pick the smallest `(hour, minute)` pair `>` the
starting point (or `>=` on the first day only) that satisfies both fields.
Every candidate is converted to a `zoneinfo`-aware `datetime` and compared
by its `.timestamp()`, never by wall-clock label — this is what makes a DST
transition self-correcting rather than a special case: a wall-clock time
that repeats during a fall-back is still strictly increasing in epoch
seconds, so the search can never produce two fires for what looks like "the
same" 1:30am, and a wall-clock time that doesn't exist during a spring-
forward still produces *some* valid, strictly-later timestamp from
`zoneinfo`'s own (undefined-but-total) resolution of it, so the search
still terminates and advances instead of raising. The `docs/tasks/…`
required DST test exercises a fall-back (an hour that occurs twice) and
asserts the returned timestamp is strictly greater than `after` and that a
second call starting from the result never returns the same instant again.

**Bounded search.** A syntactically valid but calendrically impossible
expression (`day_of_month = {31}, month = {2}` — February 31st) never
matches. The outer day loop is capped at four years (a full leap cycle);
past that, `next_after` raises `ValueError` naming the expression. `parse`
deliberately does not reject calendar-impossible day/month combinations —
real five-field cron doesn't either, and rejecting one class of unreachable
expression while accepting others it can't detect (`day_of_week` values
that combine with a rare `day_of_month` set to never coincide, for
instance) would be inconsistent validation rather than real safety.
`# ponytail: four-year ceiling on next_after's search catches an
unsatisfiable expression without hanging; raise, don't loop forever`.

### `plugin_id`/`conversation_id`: names, not pointers

This door has no `plugins` noun yet (H24) and so has never minted a
plugin-facing id the console could hold — and `door/nouns/memory_entries.py`
already answers the equivalent question for conversations: its own
`conversation_id` field on the wire is the conversation's `key`, rendered
directly, not a resolved `conv_...` ledger id (`memory_entries.py:63`). This
work item follows that same, already-established convention rather than
inventing a second one:

- `conversation_id` (create/update body) is the conversation's own `key`.
  Validated with the existing `conversation_store.exists(conn, key)`; an
  unknown value is `400 VALIDATION`, `errors: [{"field": "conversation_id",
  "code": "unknown_conversation"}]`. Stored as `conversation_key`, rendered
  back unchanged.
- `plugin_id` (create/update body) is the plugin's own registered `name`.
  Validated against `plugin_manifest.discover_plugins()` (every plugin this
  box can currently run); an unknown value is `400 VALIDATION`,
  `errors: [{"field": "plugin_id", "code": "unknown_plugin"}]`. Stored as
  `plugin`, rendered back unchanged.

Both are optional; a schedule with neither fires into a fresh conversation
each time (unchanged from `scheduled_triggers`' own behavior today).

### `cron` vs `interval_seconds`: which one the door ever sets

The create/update body this work item's own brief specifies is `{name,
cron, timezone?, plugin_id?, conversation_id?, trigger_text}` —
`interval_seconds` is not a field either verb accepts. It exists in the
table purely to carry forward what a migrated `scheduled_triggers` row
already had, and `scheduling.tick`'s firing rule reflects that asymmetry
directly: **a row with `cron IS NOT NULL` fires by `cron.next_after`; a row
with `cron IS NULL` fires by `now + interval_seconds`, exactly as
`scheduled_triggers` does today.** Updating a migrated row through the door
sets `cron` (the only field `update` ever touches) and, from that write
onward, the row fires as a cron schedule — `interval_seconds` is left on
the row but is no longer consulted once `cron` is non-null. No migration or
cleanup step needs to null it out; the firing rule already makes it inert.

### `scheduling.tick`'s two loops

```
async def tick(runtime: Runtime, *, now: float | None = None) -> int:
    now = now if now is not None else time.time()
    ... existing due_triggers loop, unchanged ...
    for row in conversation_store.due_schedules(runtime.connections.reader(), now=now):
        if row.state != "active":
            continue  # due_schedules already filters this; defensive, not load-bearing
        event = MessageEvent(platform="schedule", chat_id=row.name, thread_id=row.conversation_key, text=row.trigger_text)
        try:
            ok, exit_reason = await gateway_dispatch.handle_inbound(runtime, event, account=row.account_key)
        except Exception:
            logger.warning(...)
            continue
        next_run_at = (
            cron.next_after(parsed_cron, row.timezone, now)
            if row.cron is not None
            else now + row.interval_seconds
        )
        conversation_store.advance_schedule(
            conn, id=row.id, next_run_at=next_run_at, last_run_at=now,
            last_state=_map_exit_reason(exit_reason),
        )
```

**Correction, added at Deploy after a cold review caught this:** the
pseudocode above and the paragraph that followed it were written assuming
H20 already provides a mapping from the harness's eight-value `exit_reason`
down to the console's five (wire.md §6) for this work item to reuse. It
does not exist — there is no `docs/tasks/H20-*` directory and no such
function anywhere in `src/sadana/`, confirmed at Deploy. `handle_inbound`
itself exposes only `(ok: bool, text: str)`, not a raw `exit_reason` at
all, so there was never a value here to map. The shipped code sets
`last_state = "completed" if ok else "failed"` — a real, two-value stand-in
that loses exactly the distinction wire.md §6 says matters (a
`budget_exhausted`, an `interrupted`, and a genuine `provider_failed` turn
all render identically as `"failed"`) — commented in `scheduling.py` as a
placeholder for whenever H20 lands a real mapping this can switch to. This
is not a rejected alternative; it is an assumption that turned out to be
false, left uncorrected until Deploy caught it.

**The `now` parameter satisfies the required test — "a cron schedule fires
once per match under a fake clock" — without faking the environment.**
`testing-conventions` bans patching a module-level clock; `tick(runtime,
now=1_700_000_000.0)` is the same "environment as data" shape
`door/router.py`'s own `ctx.clock: Callable[[], float]` already uses, just
threaded as a value instead of a callable, because `tick` is called once per
invocation rather than many times per request the way a `DoorContext` is
reused across requests.

**Why one `tick()`, not a second entry point.** `subcommands/gateway.py`
needs zero changes: it already calls `scheduling.run_tick_loop`, which
already calls `tick(runtime)` with no `now`, so the new schedule-firing
loop is reachable the moment `tick` grows it — the least of the three
step-costs (guideline 3) is "existing step gets one more thing to do,"
against the alternative of adding a second background thread and a second
call site in `gateway.py` for what is, from the daemon's perspective, the
same recurring "fire everything that's due" concern. (Checked
`subcommands/gateway.py` directly — the intent-stage brief's mention of
"trigger verbs" moving there does not correspond to anything in the current
file: there is no CLI verb for creating a trigger anywhere in the
codebase today, only the tick-loop thread launch. Nothing there needs a
line changed.)

### State inventory (guideline 4 — minimise mutable state)

| State | Why it can't be derived |
| --- | --- |
| `schedules` row itself (name, cron, timezone, plugin, conversation_key, account_key, trigger_text) | The account's own stated intent — nothing else produces it. |
| `next_run_at` | Cached rather than recomputed on every read/tick, for the same reason `scheduled_triggers` already caches it: recomputing `next_after` on every `due_schedules` poll would mean parsing and searching every active schedule's cron expression every 30 seconds instead of once per fire. |
| `last_run_at` / `last_state` | Observability the intent explicitly asks for ("see when it last ran, what happened") — no other table records a schedule's own fire history distinctly from the conversation it drove, which may have long since diverged (a schedule can retarget its `conversation_id` after firing into it once). |
| `state` (active/paused) | Not derivable from `next_run_at` — a paused schedule keeps whatever `next_run_at` it had when paused, specifically so a bug can't make a stale timestamp look due the moment it's resumed before `resume` recomputes it. |
| `version` | Existing optimistic-concurrency convention, not new. |

Nothing here is state this design chose to add speculatively — every row is
one of the fields requirement 4 already names, and `next_run_at` mirrors
what `scheduled_triggers` already caches for the identical reason.

### Guideline 2 — where this sits relative to the plugin seam

`PLUGIN-SYSTEM` (H24) is a real, already-planned block, but it hasn't
landed — there is no `plugins` noun, no plugin-facing id, no registry
seam to plug a scheduling backend into today. This work item is squarely
in the "don't foreclose it" case, not the "build the seam" case: it adds
one more table behind the one storage mechanism this project already has,
and resolving `plugin_id` by name (rather than inventing a
not-yet-real plugin id scheme) is itself the choice that keeps H24's
eventual `plugins` noun free to define ids however it decides to, without
this work item having pre-committed to a shape.

## Interface

**Wire body (`create`/`update`, both):** `name` (string, required on
create), `cron` (string, required on create — the only recurrence field the
door ever writes), `timezone` (string, optional, default `"UTC"`),
`plugin_id` (string, optional), `conversation_id` (string, optional),
`trigger_text` (string, required on create).

**Rendered fields (`get`/`list`/action responses):** the standard fields
(`id`, `created_at`, `updated_at`, `state`, `tags`, `harness_id`, `version`,
`name`) plus `cron`, `timezone`, `interval_seconds`, `trigger_text`,
`plugin_id`, `conversation_id`, `next_run_at` (ISO), `last_run_at` (ISO or
`null`), `last_state` (or `null`).

**Actions:** `pause` (`active` → `paused`, capability `schedules.write`),
`resume` (`paused` → `active`, capability `schedules.write`, recomputes
`next_run_at` from `ctx.clock()`).

**Errors:**
- `400 VALIDATION`, `errors: [{"field": "cron", "code": "invalid_cron"}]`
  and/or `{"field": "timezone", "code": "invalid_timezone"}` — both
  reported together when both are bad, never just the first one found.
- `400 VALIDATION`, `errors: [{"field": "plugin_id", "code":
  "unknown_plugin"}]` / `{"field": "conversation_id", "code":
  "unknown_conversation"}`.
- `409 CONFLICT` — a `create` whose `name` collides with an existing
  schedule (any account: `name` stays a box-wide unique natural key, the
  same namespace `scheduled_triggers.name` already occupies today — see
  Concerns).
- `404 NOT_FOUND` — unknown id, or an id belonging to a different account.
- `412 PRECONDITION_FAILED` — missing or stale `If-Match` on
  `update`/`remove`/`pause`/`resume`.
- `501 HARNESS_CAPABILITY_MISSING` — `create`/`update`/`remove`/`pause`/
  `resume` when `schedules.write` isn't declared.

**Filterable:** `state`, `plugin_id`, `created_at`. **Orderable:**
`created_at` (default `created_at desc`, matching every other noun with no
stated override).

## Acceptance criteria

- [ ] `cron.parse` and `cron.next_after` pass unit tests for `*`, lists,
      ranges, steps, on every one of the five fields, plus one DST
      transition.
- [ ] Opening the store twice against the same file copies every
      `scheduled_triggers` row into `schedules` exactly once (second open is
      a no-op); a copied row carries `account_key = memory.owner_account()`,
      `cron = NULL`, and its original `interval_seconds`.
- [ ] A `paused` schedule does not fire across a `tick()` even when
      `next_run_at <= now`.
- [ ] A `cron`-based schedule fires exactly once per matching instant under
      an injected `now`, and its `next_run_at` advances to the following
      match, never re-firing the same instant on a second `tick()` at the
      same `now`.
- [ ] `create` with a syntactically invalid `cron` returns `400` with a
      non-empty `errors[]` naming `"cron"`; combined with a bad `timezone`,
      both field errors are present in one response.
- [ ] `GET`/`update`/`remove`/`pause`/`resume` against another account's
      schedule id return `404`.
- [ ] `nouns.md`'s `## schedule` section is filled in with fields, states,
      actions, filterable/orderable, `search_doc`, and any divergence from
      the console's own prompt.
- [ ] `schedules` is registered in `subcommands/door.py`'s `nouns={...}` and
      exercised by `tests/contract/nouns/test_schedules.py`.
- [ ] `make verify` ends `VERIFY OK`.
- [ ] A curl transcript: create → get (shows `next_run_at`) → pause →
      resume, pasted as Deploy-stage evidence.

## Non-goals

- Seconds-resolution cron, named weekdays/months, `L`/`W`/`#`, and any other
  extension beyond standard five-field cron.
- A CLI verb for creating/listing schedules — this work item is the door
  (HTTP) surface only; nothing in `subcommands/` currently offers this for
  `scheduled_triggers` either, so there is no existing CLI parity to keep.
- Deleting or otherwise touching `scheduled_triggers` beyond the one-time
  copy — it is explicitly left in place, inert.
- A generic scheduling backend/registry seam for a second scheduling
  provider (chronos-style) — no second provider exists to register
  (guideline 2).

## Rejected alternatives

- **`croniter` dependency** — declined; see "Learning from the reference"
  above. Cost of writing five-field parse + search ourselves: well under a
  hundred lines, no new dependency, no wider feature surface than this
  door actually serves.
- **JSON-file-plus-`fcntl` job storage** (hermes's own mechanism) —
  declined; H16 already settled concurrent-writer handling for this
  project, and a second mechanism for one more table would reopen that
  question for no benefit.
- **Resolving `plugin_id`/`conversation_id` through a new ledger-id lookup**
  (mirroring `agents.py`'s `template_id`, which is a real minted/derived
  id) — declined in favor of following `memory_entries.py`'s existing
  "the `_id` field on the wire is actually the natural key" convention,
  since no plugin-facing id exists yet for `plugin_id` to resolve *from*,
  and inventing one now would be exactly the kind of premature, unconsumed
  seam guideline 2 warns against.
- **A second background thread/call site for schedule-firing**, parallel
  to `run_tick_loop` — declined; `tick()` growing a second due-query is the
  cheaper of the three step-costs (guideline 3), and `gateway.py`'s call
  site needs no change either way.
- **Per-account `name` uniqueness** instead of the given schema's box-wide
  `UNIQUE NOT NULL` — considered because the intent's own interview
  broadened "affected users" to multiple accounts, which is exactly the
  situation where a global namespace usually surprises someone. Declined
  because the table's own DDL (given, not up for revision in this stage)
  already fixes it box-wide, and `scheduled_triggers.name` already occupies
  exactly this same global namespace today — this work item does not
  regress anything by keeping it, and re-scoping the column is a schema
  change outside what this work item was asked to build. Flagged instead
  under Concerns.

## Concerns

- **`name`'s box-wide uniqueness, now that multiple accounts can create
  schedules,** means one account can take a name another account wanted —
  a `409 CONFLICT` a second account will see with no visibility into who
  holds it (existence leaks nothing per wire.md, but the collision itself
  is confusing without that context). This is inherited from the given
  schema and from `scheduled_triggers`' own precedent, not introduced here;
  worth a human decision at Deploy about whether a follow-up work item
  should scope it per-account, now that the intent has confirmed multiple
  accounts are real users of this noun.
- **`remove`'s capability gate is inferred, not stated.** The brief says
  "create and update also behind `schedules.write`" and lists `pause`/
  `resume` as actions (which get `ActionSpec.capability` for free); it does
  not say whether `remove` needs the capability too. This design gates it
  the same as create/update, on the reasoning that it's an equally
  mutating verb — flagging the inference rather than presenting it as
  given.
- **`next_after`'s four-year search ceiling** is a judgment call with no
  stated requirement behind it (the brief asks for correctness on "every
  operator" and one DST case, not for a specific timeout policy). A
  calendrically-impossible expression is rare enough in practice that
  raising instead of hanging seems like the obviously right default, but
  it is a default this spec is choosing, not one the intent dictated.
- **Testing conventions vs. an async tick function under `make test`.**
  `tick()` is `async def`; its unit tests already run it via
  `asyncio.run(tick(...))` today (`tests/unit/test_scheduling.py`), so this
  is an existing, not new, tension — noted because `testing-conventions`
  doesn't speak to async test functions directly and this work item
  extends that same pattern rather than resolving an ambiguity in it.
- No policy conflict was found between `testing-conventions` and this
  design: the one place a fake clock is genuinely needed (`tick`'s due-scan)
  is satisfied by an injected value, not a patched module attribute, so
  the "never fake the environment" rule and the "prove cron fires exactly
  once" requirement do not pull against each other.
