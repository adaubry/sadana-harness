# Spec: A run leaves numbers behind, not just a transcript

Intent: docs/tasks/OBSERVABILITY-01-turn-and-plugin-run-records/intent.md

## Requirements

1. Every `conversation.take_turn` (top-level turn, and a plugin's `ask`-spawned
   child turn) produces a stored record with: which turn it was, how long it
   took, how many model calls it made, how many prompt/completion tokens it
   used, and how it ended (`ExitReason`). *(intent: "how many turns did that
   take, how long, what did it cost, how often does this fail")*
2. Every plugin-dispatch call (one `plugin_manifest.run_graph` invocation via
   `plugin_dispatch.build_dispatch`'s `dispatch()` closure) produces a stored
   record with: which turn it happened inside, its position within that turn,
   how long it took, how many DAG nodes it visited, and whether it failed
   (`DagResult.failed_node`). *(same intent line, for the plugin-dispatch
   emitter)*
3. A record can be read back for a given conversation without opening the
   database by hand. *(intent: "there is a way to read that record back for a
   given run")*
4. Recording never changes whether, or how, the turn/plugin run it is
   observing succeeds or fails, and does not measurably slow it down.
   *(intent Constraints: no critical-path latency, no new failure point)*
5. No new external dependency or service is introduced. *(intent Constraints)*
6. Nothing beyond the turn loop and plugin dispatch is instrumented in this
   item; thresholds, alerting, and "regression" detection are out of scope.
   *(intent Proposed outcome, "explicitly not part of this outcome")*

## Design

One new I/O module (`observability.py`), two new SQLite tables on the
existing conversation store, and one new optional parameter on two
already-existing functions in `plugin_dispatch.py` — no new module gets
to touch `conversation.py` or `plugins.py` internals.

### Reference corpus

hermes's OBSERVABILITY block (`docs/reference/hermes_core_blocks_kind.csv`,
`kind == production-code`) is `agent/monitoring/{emitter,events,policy,
cron_health,gateway_health}.py` plus `agent/battery.py`, `agent/trace_upload.py`,
and a `hermes_cli/observability/` package (`shared_metrics*.py`,
`relay_shared_metrics.py`) with its own versioned JSON-Schema contract
(`hermes.shared_metrics.v1/v2.schema.json`) and an OTLP exporter, an
external-service sink.

The shape adopted: one structured record per run, with a stable set of
fields (duration, count, outcome) — that part of `events.py`'s idea is sound
and is what Requirements 1–2 restate. The shape declined:

- **A generic emitter/event-bus with pluggable sinks** (`emitter.py`, the
  OTLP exporter, `shared_metrics_sender.py`/`_subscriber.py`). Today there is
  exactly one consumer of a run record: the CLI readback in Requirement 3. A
  publish/subscribe layer earns its cost once a second real subscriber
  exists to register (`CLAUDE.md`: "A registry or dispatch seam for a family
  of pluggable backends earns its cost only once a second real member
  exists"). Declined for that reason, not preference — it is the exact
  "family of one" the rule names.
- **A versioned wire-schema for the record** (`hermes.shared_metrics.v1/v2`).
  That versioning cost exists in hermes because `shared_metrics_sender.py`
  ships the record to something outside the process. Nothing here leaves the
  process (Requirement 5), so there is no wire boundary to version yet.
- **`policy.py`'s threshold/alerting layer** — explicitly descoped by
  intent.md and Requirement 6.
- **`gateway_health.py`/`cron_health.py`** — health checks of long-running
  daemons. GATEWAY-DAEMON-01 exists, but polling its liveness is a different
  scenario from "did this run's numbers look right"; no requirement here
  covers it, and no requirement is added for it.

### Where the numbers already are

`conversation.TurnResult` (`src/sadana/conversation.py:606`) already carries
`model_calls`, `usage` (`model_access.Usage`: `prompt_tokens`,
`completion_tokens`), and `exit_reason` — three of Requirement 1's four
fields exist the moment a turn finishes; only wall-clock duration is missing,
because `take_turn` receives `now` as a single injected instant (determinism,
not a clock it owns) and never measures its own elapsed time.

`plugins.DagResult` (`src/sadana/plugins.py:139`) already carries `trace`
(one `NodeTrace` per node visited — its length is the node count Requirement
2 asks for) and `failed_node`. Duration is missing for the same reason.

So neither `conversation.py` nor `plugins.py` needs a new field — both
already return everything but duration. Duration is measurable entirely at
the two existing call boundaries that already await these functions and
already sit in the one module sanctioned to cross into both
(`plugin_dispatch.py`'s own docstring: "The one module allowed to import
both `conversation.py` and `plugins.py`/`plugin_manifest.py`").

There's one gap this surfaces: `model_access.Usage` has no cost field, only
token counts — see Rejected alternatives.

### New module: `src/sadana/observability.py`

A new file, not a new section of `plugin_dispatch.py` or `conversation_store.py`
— it is the first OBSERVABILITY-block module to touch real I/O
(`sqlite3`, `time.monotonic()`), and `CLAUDE.md` already states the rule this
follows: "A module that touches real I/O ... is its own file, separate from a
block's pure-function module, regardless of line count."

It reuses the conversation store's own file and connection — no second
SQLite file, no second writer (`CLAUDE.md`'s conversation_store.py note: a
second writer connection is the exact condition that forced hermes into a
refcounted connection registry; nothing here creates one). It adds two new
tables via `CREATE TABLE IF NOT EXISTS`, which is safe for both a fresh store
and one that predates this item — no `ALTER TABLE` migration is needed
because these are whole new tables, not new columns on `conversations`/
`messages` (`CLAUDE.md`'s `PRAGMA table_info` + guarded `ALTER TABLE` rule is
for the latter case).

```sql
CREATE TABLE IF NOT EXISTS turn_runs (
    conversation_key    TEXT NOT NULL,
    turn_seq            INTEGER NOT NULL,
    duration_s          REAL NOT NULL,
    model_calls         INTEGER NOT NULL,
    prompt_tokens       INTEGER NOT NULL,
    completion_tokens   INTEGER NOT NULL,
    exit_reason         TEXT NOT NULL,
    recorded_at         REAL NOT NULL,
    PRIMARY KEY (conversation_key, turn_seq)
);

CREATE TABLE IF NOT EXISTS plugin_runs (
    conversation_key    TEXT NOT NULL,
    turn_seq            INTEGER NOT NULL,
    seq_in_turn         INTEGER NOT NULL,
    plugin              TEXT NOT NULL,
    entry               TEXT NOT NULL,
    duration_s          REAL NOT NULL,
    node_count          INTEGER NOT NULL,
    failed_node         TEXT,
    recorded_at         REAL NOT NULL,
    PRIMARY KEY (conversation_key, turn_seq, seq_in_turn)
);
```

Both primary keys are natural keys already minted upstream, not invented
here (`CLAUDE.md`: "Use names, not pointers for anything long-lived"):
`turn_runs` reuses `TurnKey` (`conversation.py:597`) verbatim; `plugin_runs`
extends it with `seq_in_turn`, a plain 0-based counter local to one
`dispatch()` closure's lifetime — the same "fresh per call, never shared
across turns" posture `plugin_dispatch.capturing_dispatch`'s own `captured`
list and `build_dispatch`'s own `ChildSeqTracker` already take.

`observability.py` exposes:

```python
def make_recorder(conn: sqlite3.Connection) -> Recorder:
    """Ensures both tables exist on conn, returns the two callables below
    bound to it. Call once per open connection — mirrors
    conversation_store.open_store()'s own "create tables on open" posture."""

@dataclass(frozen=True)
class Recorder:
    record_turn: Callable[[TurnKey, TurnResult, float], Awaitable[None]]
    record_plugin_run: Callable[[TurnKey, int, DagResult, float], Awaitable[None]]
```

Each callable times its own write with `time.monotonic()`, writes via
`asyncio.to_thread` (matching `conversation_store.bind_persist()`'s existing
off-thread-write precedent for the same connection), and catches+logs any
`sqlite3.Error` rather than raising — the one deliberate error guard this
item adds, justified by Requirement 4 (recording must never become the
reason the observed run fails differently). Everywhere else in this codebase
error handling is declined for scenarios that can't happen; a full disk or a
locked database during a background write is a scenario that can, and the
intent names surviving it as a constraint, not a nicety.

### Wiring: `plugin_dispatch.py`

`build_dispatch()` and `take_turn_and_reconcile()` each gain one new
optional parameter, following the exact convention `persist` and `approve`
already use in the same functions (a default no-op, overridden by a real
caller):

```python
record_turn: Callable[[TurnKey, TurnResult, float], Awaitable[None]] = _noop_record_turn
record_plugin_run: Callable[[TurnKey, int, DagResult, float], Awaitable[None]] = _noop_record_plugin_run
```

- `take_turn_and_reconcile()` times its own `_take_turn()` call and awaits
  `record_turn(turn_key, result, duration_s)` after.
- `dispatch()` (inside `build_dispatch`) times its own `plugin_manifest.run_graph()`
  call, awaits `record_plugin_run(turn_key, seq, result, duration_s)` after,
  and increments a closure-local `seq` — a third `int`-holding closure
  alongside `ChildSeqTracker`, same lifetime rules.
- `ask()` (inside `build_dispatch`) times its own `run_child()` call and
  awaits `record_turn(child_turn_key, child_result, duration_s)` too — a
  plugin's `ask`-spawned child conversation produces its own `TurnResult`
  (`ChildResult = TurnResult`, `conversation.py:1229`) that would otherwise
  never reach Requirement 1's "every turn" at all.

`plugin_dispatch.py` does not import `sqlite3` or call `time.monotonic()`
itself — it only calls whatever `Callable` it was given, same as it already
does for `persist`. The real I/O stays inside `observability.py`.

### Wiring: the two real callers

`src/sadana/subcommands/chat.py:60` and `src/sadana/gateway_dispatch.py:90`
are, today, the only two places that actually call `build_dispatch()` +
`take_turn_and_reconcile()` end to end. Both already hold the open
`conversation_store` connection they use for persistence. Both pass
`observability.make_recorder(conn)`'s `record_turn`/`record_plugin_run` into
`build_dispatch()`/`take_turn_and_reconcile()` — no new connection, no new
config key, one extra call each.

`eval_harness.py` (EVAL-01/02) is not wired in this item: it does not go
through `build_dispatch()`/`take_turn_and_reconcile()` today, and
instrumenting it is not named in intent.md's affected systems.

### CLI readback: `src/sadana/subcommands/runs.py`

Mirrors `subcommands/conversations.py`'s own shape exactly
(`build_conversations_parser` / `cmd_conversations` / a `format_*_line`
helper): `build_runs_parser` adds a `runs <conversation-key>` subcommand,
wired into `cli.py` next to the other `build_*_parser` imports. `cmd_runs`
opens the store (`conversation_store.store_path_from_config()` +
`open_store()`, the same pair every other subcommand already uses), selects
every `turn_runs` and `plugin_runs` row for that key ordered by
`turn_seq, seq_in_turn`, and prints one line per row: turn rows show
`turn_seq`, duration, model calls, prompt/completion tokens, exit reason;
plugin rows show `turn_seq.seq_in_turn`, plugin/entry, duration, node count,
failed node (or "ok").

## Interface

- **Inputs**: a `TurnKey` + `TurnResult` + elapsed seconds (turn runs); a
  `TurnKey` + `int` position + `DagResult` + elapsed seconds (plugin runs).
  Both already exist as return values at their call site — nothing new is
  computed except the elapsed time and, for plugin runs, the position.
- **Outputs**: rows in `turn_runs`/`plugin_runs` in the existing conversation
  store file; `sadana runs <key>` stdout lines.
- **Errors**: a `sqlite3.Error` during a recording write is caught inside
  `observability.py` and logged; it never reaches the caller of
  `take_turn_and_reconcile`/`dispatch`/`ask`. `sadana runs` with an unknown
  conversation key prints an empty result (matches `conversations`
  subcommand's existing not-found posture) rather than raising.

## Acceptance criteria

- [ ] `turn_runs` and `plugin_runs` tables are created idempotently on an
      existing `conversations.sqlite3` store and on a fresh one.
- [ ] A `sadana chat` turn that calls no plugin produces exactly one
      `turn_runs` row with correct `model_calls`/token counts/`exit_reason`.
- [ ] A `sadana chat` turn that dispatches a plugin produces one `turn_runs`
      row and one `plugin_runs` row, the latter's `node_count` matching
      `len(DagResult.trace)`.
- [ ] A plugin's `ask` node produces an additional `turn_runs` row for the
      spawned child conversation, keyed by the child's own `TurnKey`.
- [ ] A recording write forced to fail (e.g. a read-only store file) does not
      change the turn's `TurnResult`/`ExitReason` or raise out of
      `take_turn_and_reconcile`/`dispatch`.
- [ ] `sadana runs <conversation-key>` prints every recorded row for that
      conversation without requiring `sqlite3` to be opened by hand.
- [ ] `gateway_dispatch.py`'s daemon path records the same way `chat.py`'s
      does — verified by exercising `sadana runs` against a conversation
      driven through the gateway.
- [ ] `make verify` passes.

## Non-goals

- Thresholds, alerting, or any judgment about whether a number is "bad"
  (explicit intent descope — a later work item).
- Any external sink: OTLP, langfuse, or otherwise (intent Constraints: no
  new dependency/service).
- Dollar-cost computation (no pricing table exists anywhere in this codebase
  today — see Rejected alternatives).
- Instrumenting `model_access.py`, the CLI/gateway entry points themselves,
  or `eval_harness.py` (intent's "turn loop and plugin dispatch only").
- Per-node timing inside a plugin's DAG (`NodeTrace` is unchanged); only the
  whole-dispatch-call duration is recorded.

## Rejected alternatives

Covered inline above (Design § Reference corpus and § New module). Summary:

1. **A generic event-bus/emitter with pluggable sinks** (hermes's
   `emitter.py` + OTLP + shared-metrics-subscriber shape) — declined: one
   consumer exists (the CLI), and a registry/seam for a family of one is the
   bet `CLAUDE.md` already says to defer.
2. **A versioned wire schema for the record** (hermes's
   `shared_metrics.v1/v2.schema.json`) — declined: nothing crosses a process
   boundary here, so there is no wire format to version yet.
3. **Recording inside `conversation.take_turn`/`plugin_manifest.run_graph`
   directly** — declined as the heavier of the three step-moves available
   (add a step / make a step heavier / make a step harder): it would touch
   two core modules every other block already depends on, instead of adding
   one new optional parameter at the single boundary already sanctioned to
   cross both.
4. **A dollar-cost field** — declined: `model_access.Usage` has no pricing
   data, and inventing one here would mean owning a per-provider price table
   that changes independently of everything else in this item — exactly the
   kind of long-lived value `CLAUDE.md`'s naming rule warns should be held
   by name, not embedded, and that ownership question belongs to
   `model_access.py`'s own future work, not this one. Token counts are
   recorded instead; named as an open question below.
5. **A second SQLite file for run records** — declined: reuses
   `conversation_store`'s existing single-writer connection instead of
   opening a second one, per `CLAUDE.md`'s explicit warning about a second
   writer connection racing one SQLite WAL file.

### State inventory (guideline 4)

- `turn_runs`/`plugin_runs` rows: genuinely new information — a run's
  duration cannot be derived after the fact (the clock has moved on), so
  this is stored, not derived.
- `seq_in_turn`: a closure-local counter, not persisted as its own concept —
  derived fresh each turn from `dispatch()` being called in order, exactly
  like `ChildSeqTracker.next_seq` already is. No new mutable state class;
  reuses the existing pattern.
- Everything else recorded (`model_calls`, token counts, `exit_reason`,
  `trace` length, `failed_node`) is copied from a value `TurnResult`/
  `DagResult` already computed — nothing is recomputed or re-derived by this
  item.

## Open questions

- Dollar-cost conversion: whether/when `model_access.py` gains a pricing
  table, and whether `turn_runs` then gains a derived cost column or a
  later item computes it from the token counts already stored here.
- Whether `eval_harness.py` should eventually go through
  `build_dispatch()`/`take_turn_and_reconcile()` (and thus get recording for
  free) is a question for EVAL block work, not this one.

## Concerns

- **Best-effort recording is a deliberate, named exception to this
  project's usual "no error handling for scenarios that can't happen"
  posture.** A disk-full or locked-database write failure during a
  background recording write is caught and logged, not raised. This is the
  one place in this item that adds error handling, and it exists only
  because intent.md's own constraint (recording must never change whether
  or how the observed run fails) requires it — not general defensiveness.
- **Observability data's lifetime is now coupled to `conversations.sqlite3`.**
  Sharing the connection avoids a second writer (a real, named risk in this
  codebase's history), but means run records live and die with the
  conversation store's own file/location. If that ever needs to split (e.g.
  a much higher write volume than conversation persistence itself), it is
  reversible later behind `store_path_from_config()`'s own config-key
  pattern — flagged, not built, since no second consumer needs it yet.
  Same "add the seam when a second real need exists" reasoning as
  Rejected alternative 1.
- **`policy conformance`**: `testing-conventions` is applied (see plan-skill
  handoff — acceptance criteria above are written as things `make verify`
  and a demo script can check, per that skill's rules once plan.md exists).
  No `project-structure` or `reference-lookup` skill exists in
  `.claude/skills/` in this repository today, so neither is named as
  "applied" — there is nothing to check this design against beyond
  `CLAUDE.md`'s own layout rules, which are cited by name above wherever
  they bear on a decision.
