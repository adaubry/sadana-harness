# Spec: Scheduled and resumable triggers

Intent: docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers/intent.md

## Requirements

1. A named trigger, one-shot or recurring, can be registered and, once its
   time arrives, starts or continues a conversation with no inbound message
   from anywhere — satisfies intent's "start on its own at a chosen time."
2. Nothing happens because of a registered trigger until it is actually due —
   no schedule, no background activity beyond what already runs today.
3. A plugin's declared graph can reach a node that ends the current run
   without finishing it, keeping everything already produced (trace,
   artifacts) rather than discarding it — satisfies intent's "hand off... and
   pick back up automatically... with everything it had already done still
   intact."
4. Once paused, a run resumes later through the same communication path the
   process already receives from the outside world — no second channel type
   is added to receive the resuming answer.
5. A trigger due while the daemon was not running does not fire retroactively
   on restart; only firings from process-start onward occur (intent's
   Constraints, "no backfill").
6. An inbound delivery that does not match a real outstanding pause causes no
   error and no crash (intent's Constraints, "discarded... not... an error");
   see `## Concerns` for exactly what "discarded" means here.
7. A paused run has no built-in expiry — nothing marks it failed purely for
   having waited a long time (intent's Constraints, "waits for as long as it
   takes").
8. The scheduler and the wait-node resume mechanism are two independent
   pieces, each justified and buildable on its own — no shared interface
   papering over the fact that one starts something new and the other
   resumes something already running (intent's Constraints).
9. A resumed node still receives only what CLAUDE.md already promises every
   node: its immediate predecessor's output (for a `wait` node, the resuming
   event's payload standing in for that), never the run's accumulated
   history.

## Design

**Policy conformance**

- **testing-conventions**: loaded and applied — see `## Acceptance criteria`
  and the Interface functions below; every new function is either pure (unit
  tested with no I/O) or an I/O function tested against a tmp-path SQLite
  file, matching `conversation_store.py`'s own existing test split. No test
  reads source text or the network.
- **project-structure / reference-lookup**: no skill by either name exists
  under `.claude/skills/` (checked — the six skills present are audit,
  build, deploy, design, plan, testing-conventions). CLAUDE.md's own "Layout
  and ownership informations" and "The reference corpus" sections already
  cover this ground and are applied throughout this document instead: new
  code follows this project's one-I/O-module-per-store convention, and every
  reference-corpus decision below is recorded as adopted or declined with a
  named reason, never silently copied.
- No security, brand, or UX skill exists in this repo; none applies to a
  backend scheduling/resume mechanism with no user-facing surface.

**1. Learn from the reference before proposing**

Read `../hermes-agent/cron/scheduler.py` (8,277 lines) and `cron/jobs.py`
(4,454 lines), both `production-code` in the SCHEDULING block
(`docs/reference/hermes_core_blocks_kind.csv`).

**Adopted**: the one-line idea the scheduler's own docstring states —
`tick()` checks for due jobs and runs them, called periodically from a
background thread, one tick at a time. That shape is this design's entire
scheduling mechanism.

**Declined, by name**:

- `cron/jobs.py`'s own JSON-file store (`~/.hermes/cron/jobs.json`) with
  hand-rolled cross-process advisory file locking (`fcntl`/`msvcrt`,
  `_jobs_lock()`). sadana already has exactly this problem solved —
  `conversation_store.py`'s SQLite file, WAL journal mode, and single-writer
  posture. A second, JSON-file persistence mechanism next to the SQLite one
  would be two ways to durably store one project's state for no reason
  `conversation_store.py` doesn't already cover; the two new tables this
  spec adds live in the same file, opened through the same `open_store()`.
- Cron-expression parsing (`parse_schedule()`, an optional `croniter`
  dependency plus a hand-rolled natural-language `_natural_every_to_cron`
  layer). No dependency in this project's `pyproject.toml` provides this,
  and the concrete need named in the interview ("Monday morning") is fully
  expressible as an anchor timestamp plus a repeat interval — see `## Design
  #2` below. Declined specifically because it is a second, heavier
  mechanism for a scenario an anchor-plus-interval already catches; not
  declined because cron syntax is bad. Reopens cleanly later as a second
  `schedule_kind`, once a real caller needs a expression cron syntax alone
  can't express (e.g. "the first weekday of the month").
- `incidents.py`, `monitor.py`, `suggestion_catalog.py`,
  `blueprint_catalog.py`, `lifecycle_guard.py`'s inflight-job sweeping,
  `scheduler_provider.py`'s pluggable-provider registry (chronos and
  friends): all assume a multi-tenant, multi-provider, long-running
  production deployment sadana's phase-1 backbone doesn't have. Named
  individually rather than waved away as "the rest of the file": each is a
  real, working answer to a scenario (alerting on repeated failure,
  detecting drift, offering schedule suggestions, running against a second
  scheduler backend) that has no real caller here yet — CLAUDE.md's own
  registry rule for the last one, ordinary phase-1 scope discipline for the
  rest.

No prior art in the reference corpus addresses the `wait`-node/resume half
directly — `plugin_blueprint.md §7.2` is this project's own design material,
not hermes's; hermes has no DAG-shaped plugin runtime to compare against.
Searched `gateway/`, `agent/`, and `cron/` for a correlation-id/resume
mechanism (`correlation_id`, `resume_run`, `pending_wait`, `callback_id`);
nothing analogous turned up outside two unrelated hits
(`agent/subagent_lifecycle.py`, `agent/plugin_stream_hooks.py`, neither a
durable-pause mechanism). Recorded as looked-for, not found, rather than
left silent.

**2. Where this sits relative to the plugin seam**

The plugin seam (PLUGINS block: manifest, graph walker, `DagResult`) already
exists and has two real work items running D3/D4/E1/F1/G1 through it — this
is deep inside "after the seam," not before it. Guideline 2's caveat (don't
foreclose a seam that doesn't exist yet) doesn't apply; the ordinary rule
does: growth should be an addition to what's there, not a rewrite of it. Every
change below is additive to an existing function's signature or an existing
dataclass's field set, never a replacement — traced through each change below.

**3. The scheduling half**

**Schedule shape**: an anchor wall-clock timestamp (`next_run_at`, seconds
since epoch — not the monotonic clock `WallClockBudget` uses, since a
schedule has to survive a process restart and mean the same calendar moment
across it) plus an optional `interval_seconds`. `None` interval = fires once.
"Every Monday at 9am" is `next_run_at` = the next such moment,
`interval_seconds` = `604800`. This catches the concrete scenario named in
`intent.md` with no new dependency and no cron-expression parser (§1 above).

**What fires**: firing a trigger is nothing but delivering a synthetic
inbound message through the exact bridge `channel_webhook.py` already uses.
A new `MessageEvent(platform="schedule", chat_id=<trigger name>, thread_id=None,
text=<trigger's own stored text>)` reaches `gateway_dispatch.handle_inbound()`
completely unchanged from GATEWAY-DAEMON-01's own code path — no second
bridge, no new turn-loop entry point. `platform` distinguishes it from a
webhook-sourced message the same way `MessageEvent.platform` already
distinguishes adapters generally (`gateway.py`'s own docstring: "kept as a
real field... so a second adapter later needs no change here" — this is
exactly that second adapter, and it needed none). The trigger's own `name`
becomes the session key (`schedule:<name>`), so a recurring trigger's
firings share one growing conversation, the same posture every other session
key in this codebase already has; a fresh conversation per firing was
considered and declined — see `## Rejected alternatives`.

**New module, `src/sadana/scheduling.py`** (I/O: the clock, `conn`; CLAUDE.md's
I/O-module rule — its few pure helpers stay in the same file rather than a
second one, for the reason given in `## Rejected alternatives`):

- `_is_due(next_run_at: float, now: float) -> bool` — pure.
- `_advance(now: float, interval_seconds: float) -> float` — pure; always
  `now + interval_seconds`, never `next_run_at + interval_seconds`, so a gap
  longer than one interval (the daemon was down) never produces a backlog of
  already-past `next_run_at` values that would fire on the very next tick —
  this is requirement 5's actual mechanism, not a comment promising it.
- `async def tick(conn, *, plugin_set, persona, provider, model, record_turn, record_plugin_run) -> int`
  — queries `conversation_store.due_triggers(conn, now=time.time())`, and for
  each one: builds the synthetic event, awaits `gateway_dispatch.handle_inbound()`,
  then either `conversation_store.delete_scheduled_trigger()` (one-shot) or
  `conversation_store.advance_scheduled_trigger()` (recurring). Returns the
  count fired, for the proof script and tests to assert against — never
  raises: a failure firing one trigger is caught, logged, and the loop moves
  to the next (CLAUDE.md: "An observability/recording write's failure is
  caught and logged, never raised to the caller of the run it is
  observing" — the same posture, applied to a tick's own per-trigger work,
  not just a recording write).
- `def run_tick_loop(conn, *, interval_seconds, **tick_kwargs) -> None` —
  `while True: asyncio.run(tick(conn, **tick_kwargs)); time.sleep(interval_seconds)`.
  Started as a `daemon=True` thread by `cmd_gateway_run`, so it needs no
  coordination with `gateway_daemon.run()`'s own SIGTERM/lock/shutdown
  sequence at all: a daemon thread dies with the process, and requirement 5's
  own "best-effort, no catch-up" posture already makes an abrupt mid-tick
  kill an accepted, harmless outcome rather than a new failure mode to
  guard against. `gateway_daemon.py` itself is untouched — its own docstring
  states it "owns none of the turn-handling logic," and this keeps that true.

**`subcommands/gateway.py`**: `cmd_gateway_run` gains one line before
`return gateway_daemon.run(...)`:
`threading.Thread(target=scheduling.run_tick_loop, kwargs={...}, daemon=True).start()`.

**Config**: `SADANA_SCHEDULING_TICK_SECONDS` (`config.env_int`, default
`30`) — a behavior/threshold, so config not `.env`, matching every other
tunable this project already exposes this way.

**4. The wait-node half**

**`plugins.py`**: `DagResult` gains one new optional field,
`paused_node: str | None = None`. Every existing construction of a
`DagResult` (dispatch's own "no installed plugin" case, `run_graph`'s
`result()`/`failed()` helpers, every test and eval task) keeps compiling and
behaving identically — `paused_node` defaults to `None`, so the type is a
pure addition, not a new outcome union. `DagResult`'s existing two-state
shape (terminal vs. `failed_node` set) already only needs one more optional
field to become three-state, not the closed-outcome-union treatment
CLAUDE.md reserves for "branches [that] carry meaningfully different data" —
a paused result's `text`/`artifacts`/`trace` mean exactly what a terminal
result's do; only `paused_node` is genuinely new information.

A new frozen dataclass, `ResumeState` (`plugins.py`): `node: str`,
`value: object`, `trace: tuple[NodeTrace, ...]`, `artifacts: tuple[Artifact, ...]`.
Never stores the plugin's directory or manifest object — both are re-derived
fresh at resume time from a single `plugin_manifest.validate()` call against
`pause.plugin`'s own directory (not `discover_plugins()`, which would
re-validate every *other* installed plugin too, for a lookup that only ever
wants one name — a self-check efficiency finding, fixed before shipping),
the same "never cached... re-validated per call would be pure waste, not
extra safety" posture `InstalledPlugin`'s own docstring already states for
every other reader of an installed plugin, and this project's own names-not-pointers
rule (a plugin's on-disk location can move independently of anything
referencing it).

**`plugin_manifest.py`**: `run_graph()` gains one new optional parameter,
`resume: plugins.ResumeState | None = None`. When given, the walk's setup
(currently `trace = []`, `artifacts = []`, `value = arguments`,
`current = entry.start`) is replaced by `trace = list(resume.trace)`,
`artifacts = list(resume.artifacts) + [NodeTrace-for-the-wait-node-itself]`,
`value = resume.value`, `current = by_name[resume.node].next`. Everything
after that point is the existing `while True` loop, completely unchanged —
the walker does not know or care whether it started fresh or resumed; it
only ever sees "a node name and a value to feed it," which is what
`plugin_blueprint.md §7.2` predicted a suspend/resume addition would cost
("a schema addition... rather than a rewrite").

The `wait` branch (currently, with `each`, an unconditional
`return failed(node, f"{node.kind} steps are not runnable yet")`) splits:
`each` keeps that exact behavior; `wait` instead returns
`DagResult(plugin=manifest.name, entry=entry.tool, text=f"{manifest.name}'s {node.name!r} step is waiting for an external answer.", artifacts=tuple(artifacts), trace=tuple(trace), failed_node=None, paused_node=node.name)`
— note this happens *before* appending a trace entry for the wait node
itself, matching the walker's existing rule that a node only gets a trace
entry once it has actually run; the resume path (above) is what appends that
entry, once the wait node's "run" (receiving its answer) actually happens.

**Getting a session identity into a `call` node that starts the external
job**: a `call` node's body needs to tell the external system where to call
back — sadana's own correlation key for that is the conversation's session
key, exactly the string `channel_webhook.py` already keys conversations by.
`plugin_dispatch.py`'s `dispatch()` closure already has exactly one precedent
for handing a node body identity the model never controls and never sees:
`_sadana_memory_ctx`, merged into `call_arguments` last so it always wins.
This spec adds a second reserved key, `_sadana_session_key`, merged the same
way, holding `conversation.key` (already in scope inside `build_dispatch`'s
closure — no new parameter needed there). A `call` node's body reads
`value["_sadana_session_key"]` the same way it would read any other
argument, and includes it in whatever payload it sends the external system
as the field that system should echo back as `chat_id` in its callback POST.
**This is the second occurrence of this exact pattern** — see the note to
the user below the `## Concerns` section about promoting it into CLAUDE.md.

**Resuming a paused run — `plugin_dispatch.py`, new function
`resume_paused_run(conn, conversation_key, payload_text, *, approve=plugin_manifest._default_approve) -> plugins.DagResult`**:
loads the pause row, re-resolves the plugin fresh by name via
`plugin_manifest.validate()` on its own directory (not `discover_plugins()`
— see above), finds the matching `Entry` by the stored `entry` tool
name, and calls `run_graph(..., resume=ResumeState(...), ask=_unsupported_ask, approve=approve)`.
`_unsupported_ask` is a module-level `AskFn` that always returns `None` —
see `## Non-goals` for why a resumed walk cannot yet reach a real `ask`
node, and why that's an honest, named scope cut rather than a silent gap.
On return, deletes the `plugin_pauses` row unconditionally: a terminal
`DagResult` means the run is over (succeeded or failed), and a second
`paused_node`-bearing `DagResult` (the walk hit a *second* `wait` node)
means a fresh pause row is written in its place by the same function — never
both a delete and a write racing, since this all happens inside the
`_conn_lock`-serialized call `handle_inbound` already makes.

**`gateway_dispatch.py`**: `handle_inbound()` gains one branch at its very
top, before today's load-or-create: `pause = conversation_store.load_pause(conn, key)`.
If `pause is None`, every line of existing behavior is unchanged — this is
regression-tested explicitly (see `## Acceptance criteria`). If a pause
exists, the model is never called: `resume_paused_run()` runs instead of
`take_turn_and_reconcile()`, its `DagResult.text` is appended to the
conversation's message history as one new message (via `conversation.append`
— CLAUDE.md's own "message history mutated only through
conversation.append/repair/compact" rule, applied to a message no model
turn produced), and the conversation is persisted exactly as the normal path
already does. `ok` in the returned `(bool, str)` is `pause_result.failed_node is None`.

**Persisting the *first* pause** (a design gap this document's first draft
left unaddressed, caught by `scripts/prove_gateway_wait_e2e.py` during
build — see `review.md` for the full account, including a self-check
correction to the fix's own first shape). First fix: wrap `dispatch` with
`plugin_dispatch.capturing_dispatch()` in `gateway_dispatch.py`, then scan
the captured results for a pause once the turn was over. A self-check pass
(altitude review) pushed back on this as one layer too high: `build_dispatch()`
already takes four optional injected callbacks (`persist`, `record_turn`,
`record_plugin_run`, `memory_context`) for exactly this shape of side
effect, called from inside `dispatch()` at the point `run_graph`'s real
result is already in scope — `record_plugin_run` reads that same result one
line away. The shipped fix adds a fifth, `persist_pause`, awaited by
`dispatch()` itself the moment a result comes back with `paused_node` set
(right after `record_plugin_run`), defaulting to a no-op like every other
callback here. `gateway_dispatch.handle_inbound()` passes a closure over
its own `conn`/`key`, calling the new
`conversation_store.save_pause_from_result()` (which both this closure and
`resume_paused_run()`'s own re-pause branch now share, rather than each
unpacking a `DagResult`'s fields into `save_pause()`'s arguments by hand).
This deletes the capture-and-replay indirection entirely: the pause is
written the moment it's known, by the code that already knows it, using
the extension-point pattern this module already established for every
other turn-shaped side effect.

**5. Minimise mutable state — the full inventory**

Two new tables in `conversation_store.py`'s existing `_SCHEMA`, added the
same way the file already documents adding one (`CREATE TABLE IF NOT EXISTS`;
no guarded `ALTER TABLE` needed since these are new tables, not new columns
on an existing one — `_migrate_columns()`'s guard is for the latter case
only):

```sql
CREATE TABLE IF NOT EXISTS scheduled_triggers (
    name              TEXT PRIMARY KEY,
    trigger_text      TEXT NOT NULL,
    next_run_at       REAL NOT NULL,
    interval_seconds  REAL
);

CREATE TABLE IF NOT EXISTS plugin_pauses (
    conversation_key  TEXT PRIMARY KEY REFERENCES conversations(key),
    plugin            TEXT NOT NULL,
    entry             TEXT NOT NULL,
    node              TEXT NOT NULL,
    trace_json        TEXT NOT NULL,
    artifacts_json    TEXT NOT NULL
);
```

Every field justified against "why can this not be derived":

- `scheduled_triggers.name` — the natural key a person or a plugin's setup
  code addresses this by (CLAUDE.md: names, not pointers, backed by a real
  constraint — the `PRIMARY KEY` itself).
- `next_run_at`/`interval_seconds` — genuinely can't be derived; a schedule
  is exactly this project's example of "external, real-world state that must
  be stored because nothing else remembers it."
- `plugin_pauses.conversation_key` as `PRIMARY KEY` — not a synthetic id:
  it both names the one conversation this pause belongs to and, by being a
  primary key, is what enforces "at most one outstanding pause per
  conversation" as a real database constraint rather than an unenforced
  comment (Non-goals, below).
- `plugin`/`entry`/`node` — three names, not paths or object references;
  re-resolved fresh at resume time (§4 above), matching this project's own
  rule for `SkillRef`/`InstalledPlugin`.
- `trace_json`/`artifacts_json` — not derivable: they are the actual record
  of what already happened before the pause, needed to produce a faithful
  final `DagResult` once the run completes. Stored as JSON text, the same
  choice `conversations.tool_surface_json` already made for a tuple of
  small, no state of its own.

`DagResult.paused_node` and `ResumeState` (§4) are values, not stored state —
constructed fresh on every call, matching `NodeTrace`/`Artifact`'s own
existing posture.

## Interface

- `conversation_store.upsert_scheduled_trigger(conn, *, name: str, trigger_text: str, next_run_at: float, interval_seconds: float | None) -> None`
- `conversation_store.due_triggers(conn, *, now: float) -> tuple[ScheduledTrigger, ...]`
- `conversation_store.advance_scheduled_trigger(conn, *, name: str, next_run_at: float) -> None`
- `conversation_store.delete_scheduled_trigger(conn, *, name: str) -> None`
- `conversation_store.save_pause(conn, *, conversation_key: str, plugin: str, entry: str, node: str, trace: tuple[plugins.NodeTrace, ...], artifacts: tuple[plugins.Artifact, ...]) -> None`
  — upsert (`ON CONFLICT(conversation_key) DO UPDATE`), matching `save()`'s
  own shape, so a walk that pauses a second time overwrites cleanly.
- `conversation_store.save_pause_from_result(conn, *, conversation_key: str, result: plugins.DagResult) -> None`
  — unpacks a `paused_node`-bearing `DagResult` into `save_pause()`'s own
  arguments; the one place that mapping is defined, shared by both callers
  that can produce a pause (added during self-check, §4 below).
- `conversation_store.load_pause(conn, *, conversation_key: str) -> Pause | None`
- `conversation_store.delete_pause(conn, *, conversation_key: str) -> None`
- `plugins.DagResult.paused_node: str | None = None` (new field)
- `plugins.ResumeState` (new frozen dataclass, §4)
- `plugin_manifest.run_graph(..., resume: plugins.ResumeState | None = None) -> plugins.DagResult` (extended, not replaced)
- `plugin_dispatch.build_dispatch(..., persist_pause: Callable[[plugins.DagResult], Awaitable[None]] = _noop_persist_pause)`
  — extended, not replaced; a fifth optional injected callback alongside
  `persist`/`record_turn`/`record_plugin_run`/`memory_context`, added during
  self-check (§4 below).
- `plugin_dispatch.resume_paused_run(conn, conversation_key, payload_text, *, approve=...) -> plugins.DagResult`
- `scheduling.tick(conn, *, plugin_set, persona, provider, model, record_turn=..., record_plugin_run=...) -> int`
- `scheduling.run_tick_loop(conn, *, interval_seconds, **tick_kwargs) -> None`

Errors: none of the new functions raise for an expected outcome. A
resume whose plugin or entry no longer resolves produces a `DagResult` with
`failed_node` set (the same shape any other `run_graph` failure already
takes), not an exception — consistent with the rest of `plugin_manifest.py`.

## Acceptance criteria

- [ ] A registered one-shot trigger past `next_run_at` fires exactly once
      (via `scheduling.tick`), with no inbound webhook call involved, and is
      gone from `scheduled_triggers` afterward.
- [ ] A recurring trigger fires and its `next_run_at` advances to
      `now + interval_seconds` at fire time, not `next_run_at + interval_seconds`
      — proving requirement 5 directly (simulate a tick long after the
      original `next_run_at` and assert exactly one firing, not a backlog).
- [ ] A trigger not yet due does not fire on a tick.
- [ ] A `wait` node reached for the first time returns a `DagResult` with
      `paused_node` set, `failed_node` unset, non-empty `text`, and a row in
      `plugin_pauses`; the turn that triggered it still ends
      `ExitReason.COMPLETED` with no change anywhere in `conversation.py`.
- [ ] An inbound event on the same session key later resumes the paused run
      at the saved node without invoking the model; on a terminal result the
      `plugin_pauses` row is gone and the conversation's history gained one
      new message carrying the result text.
- [ ] A resumed walk that hits a second `wait` node pauses again cleanly (a
      fresh `plugin_pauses` row, old one replaced, not duplicated).
- [ ] `handle_inbound()` behavior for a conversation key with no pause row is
      byte-for-byte the same as before this work item (an explicit
      regression test, not just "no changes intended").
- [ ] `resume_paused_run()` for a plugin or entry that no longer resolves
      returns a `failed_node`-set `DagResult` and clears the pause row,
      never raises.
- [ ] `scripts/prove_gateway_scheduling_e2e.py` and
      `scripts/prove_gateway_wait_e2e.py` (or one combined script if the two
      halves are small enough together) each exercise their real mechanism
      live against the running daemon — CLAUDE.md's rule to prove a block's
      first real external round trip outside `make test`.

## Non-goals

- A CLI surface to register/list/remove a scheduled trigger. This spec ships
  the mechanism (`upsert_scheduled_trigger` and friends) and proves it via a
  script; a friendly `sadana schedule ...` command is CLI-SHELL's own future
  work, the same relationship GATEWAY-DAEMON-01 had to CLI-SHELL-05.
- A resumed walk reaching an `ask` node. `resume_paused_run`'s `ask`
  callback always returns `None` (a clean, named failure — never a crash),
  because supporting it for real means threading a full `Conversation` and
  `run_child` through the resume path (duplicating `build_dispatch`'s own
  `ask` closure) for a scenario the interview's concrete example never
  named. Revisit the moment a real plugin needs judgement after a pause.
- More than one outstanding `plugin_pauses` row per conversation at a time.
  Enforced as a real constraint (`PRIMARY KEY`), not just documented.
- Cron-expression scheduling syntax (§1). Anchor-plus-interval only.
- Catch-up/backfill for a missed schedule (intent's own Constraints).
- A timeout/expiry on a paused wait (intent's own Constraints).
- Approval-gating a resumed run's remaining `call` nodes differently from
  how `approve` already gates every other `call` node — SAFETY doesn't
  exist yet; this spec passes `approve` straight through unchanged.
- A new channel adapter. Resume traffic arrives on the existing
  `/webhook` route, same wire format `channel_webhook.py` already parses.

## Rejected alternatives

- **A fresh conversation per scheduled firing**, instead of one growing
  conversation per trigger name. Rejected: every other session key in this
  codebase already means "one persistent conversation," and a recurring
  trigger's own plugin can already tell fresh from continued by reading its
  own prior turns if it needs to — inventing a second session-identity rule
  just for schedules would be a new bet (guideline 2) for a distinction
  nothing in the interview asked for.
- **A `DagResult | DagPaused` closed outcome union**, instead of one new
  optional field on `DagResult`. Rejected per CLAUDE.md's own line on this:
  "a result whose branches differ only by one field... doesn't need this
  shape." A paused result's `text`/`trace`/`artifacts` mean the same thing a
  terminal one's do; only the resume position is new. The union would also
  have rippled `run_graph`'s return type through every existing caller
  (`dispatch()`, tests, eval tasks, prove scripts) for no behavioral gain
  the optional field doesn't already deliver.
- **Extending `build_dispatch()`'s return tuple** (dispatch, tracker, ask) so
  a resume path could reuse its existing `ask` closure, instead of the
  Non-goal above. Rejected as the costlier bet: a breaking signature change
  to an already-multi-callered function, to support a code path
  (`ask` after `wait`) nothing concrete needs yet. Revisit together if/when
  the `ask`-after-`wait` Non-goal is lifted.
- **A separate pure-logic file for `scheduling.py`'s two one-line helpers**
  (`_is_due`, `_advance`), split from the I/O `tick`/`run_tick_loop`
  functions. Rejected: CLAUDE.md's I/O-module rule exists to keep a large,
  block-spanning pure module (like `conversation.py`, five work items deep)
  free of disk/network/clock code; it isn't a rule against two one-line pure
  helpers living beside the ten lines of I/O they exist to serve, and
  `conversation_store.py` already sets this precedent
  (`_conversation_row()` is pure, in the same file as every I/O function
  around it).
- **A structured resume-request field distinguishing a genuine wait-callback
  from an ordinary chat message**, to make requirement 6's "discarded" literal.
  Considered and declined — see `## Concerns`.

## Concerns

**Requirement 6 ("discarded, not an error") is satisfied in spirit, not
literally, and this is a real, named gap, not an oversight.** Once a pause
row is already gone (the run already completed, or — with no timeout — this
can now only happen via a second, duplicate callback from the external
system itself), an inbound event for that same session key has no marker
distinguishing "a stale resume delivery" from "an ordinary new chat
message," because both arrive through the identical webhook wire format.
This design's actual behavior in that case is to fall through to ordinary
handling — the model sees the text as a new user message. That is not
literally "discarded"; it's the least-harm fallback available without adding
a discriminator field to the wire format (which the interview's own
"reuses whatever the system can already receive" framing argues against).
Flagging this explicitly rather than silently declaring the requirement met.

**This is one of the largest work items in the project's history**, spanning
five existing files (`plugins.py`, `plugin_manifest.py`, `plugin_dispatch.py`,
`gateway_dispatch.py`, `subcommands/gateway.py`), one existing store
(`conversation_store.py`, two new tables), and one new module
(`scheduling.py`). The intent-stage interview twice confirmed the user wants
both mechanisms in one work item despite this; every individual change above
is additive to something that already exists, and none of the two mechanisms
depends on the other being built first — scheduling and the wait-node half
could still be built and reviewed as two separate diffs within this one
work item's build stage if that turns out to help, without reopening the
one-work-item decision itself.

**A resumed run trusts a re-derived `InstalledPlugin` to still match the one
that paused.** If a plugin is upgraded or removed between pause and resume,
`resume_paused_run` either fails cleanly (entry/plugin gone) or, if the
plugin's graph structure changed but the same node name happens to still
exist, could resume into a graph the pausing run never actually walked.
`InstalledPlugin`'s own existing docstring already assumes "nothing in this
project can change [a plugin] mid-conversation" — this spec extends that
same trust across a pause, which can now span arbitrarily long real time
(requirement 7, no timeout) where the assumption is measurably weaker than
it was for a single in-process `dispatch()` call. No guard added — matches
this project's own two-occurrences bar for promoting a known gap into a
real fix; this is the first occurrence.
