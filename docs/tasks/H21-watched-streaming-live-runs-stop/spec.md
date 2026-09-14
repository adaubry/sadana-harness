# Spec: Watched — streaming, live runs and stop

Intent: docs/tasks/H21-watched-streaming-live-runs-stop/intent.md

Author: Adam Aubry (project owner). Status: approved.

## Requirements

1. A turn's model call can stream: the reply's text arrives as incremental
   fragments (deltas) rather than only as one block once the whole
   completion is done. (Intent: Proposed outcome, "words as they arrive.")
2. Each plugin-graph node a turn executes produces a live record of when it
   started, when it ended, and how it ended, readable while the turn is
   still running — not only after the fact. (Intent: "which step... is
   executing right now.")
3. A turn's own run record is readable while the turn is in flight, in a
   state that distinguishes running, waiting on something, and finished
   (successfully, with an error, or because it was stopped) — not only
   written once the turn is over. (Intent: "whether the overall request is
   running, waiting... or finished.")
4. A person can ask a running turn to stop, and the turn honors that request
   at its next safe point — before the next model call, or after the
   current plugin dispatch finishes — without anything being forcibly
   killed. (Intent: "a way to stop it mid-flight... honored at the next
   safe point.")
5. A turn that finishes normally (not stopped) produces exactly the answer
   it would have produced without any of this — streaming, live records and
   stop change only what is visible and controllable while a turn runs, not
   what a finished turn's answer is. (Intent: Proposed outcome, last
   sentence; Constraints, bullet 1.)
6. Every existing caller of a turn — `eval_harness.py`, every
   `scripts/prove_*.py` script, the CLI, the gateway/webhook path — keeps
   its current behavior unchanged if it does not ask for streaming/live/stop.
   (Intent: Affected users and systems; confirmed during planning.)
7. What streams or is recorded live is not durably stored beyond the turn's
   own finished answer and run/span records — a delta that arrives while
   nobody is watching is not recoverable afterwards. (Intent: Constraints,
   bullet 3.)
8. Stopping a turn is a request the harness can decline to have already
   missed: asking to stop a turn that already finished, or one that never
   registered (e.g. the process restarted mid-turn), answers with a
   conflict rather than pretending to have stopped something that either
   isn't running or is running in a process that no longer holds it.
   (Intent: Constraints, bullet 4 is out of scope for reconnect/multi-watch,
   but a stop request still needs a defined answer for "too late.")
9. This item does not add or change any of the console's own screens — it
   makes the underlying request watchable and stoppable through the door
   H19/H20 already built. (Intent: Constraints, bullet 4.)

## What the reference corpus showed

Read in full before proposing anything (kept out of the design, not
skipped): `agent/transports/chat_completions.py`,
`agent/chat_completion_helpers.py`, `tools/openrouter_client.py`,
`agent/stream_single_writer.py`, `agent/plugin_stream_hooks.py`,
`agent/stream_diag.py` (all under `../hermes-agent`).

**Adopted, with citations:**

- `chat_completions.py` itself never streams — streaming is a completely
  separate code path in `chat_completion_helpers.py` that consumes an
  OpenAI-SDK stream and re-normalizes it into the *same shape* the
  non-streaming path returns (`_relay_final_response()`,
  `chat_completion_helpers.py:4147-4159`). This is the direct precedent for
  this item's own design constraint that `stream_fn` returns exactly the
  `(status, body)` shape `request_fn` returns — hermes proves the same
  choice at production scale, not just in a toy case.
- Tool-call fragment accumulation is keyed by the delta's `index`, and the
  two fields are handled asymmetrically:
  `function.name` is **assigned**, not concatenated, because some providers
  resend the full name on every chunk and `+=` would duplicate it
  (`chat_completion_helpers.py:4404-4412`); `function.arguments` **is**
  concatenated (`:4413-4415`), because every provider observed sends it as
  a genuine split string. This item adopts the identical asymmetry.
- A dropped connection mid-stream is not swallowed and not raised as a bare
  exception: hermes builds a synthetic response carrying whatever text had
  already streamed (`_build_partial_stream_stub()`,
  `chat_completion_helpers.py:3406-3435`).

**Declined, with reasons:**

- Hermes's partial-continuation behavior for a mid-stream drop (return the
  partial text tagged as truncated, let the outer loop continue it) is
  declined in favor of the simpler choice this item's own decisions already
  fixed: a mid-stream transport error returns `(None, {...})`, which
  `model_access.classify()` already treats as transient and retries from
  scratch via the one retry loop `resolve()` already owns. Adopting
  hermes's richer behavior would mean inventing a second, parallel notion of
  "partial success" alongside `classify()`'s six closed outcomes — the kind
  of duplicated retry/outcome logic CLAUDE.md's "a retry loop... lives
  once" rule exists to prevent. Losing a half-streamed answer on a dropped
  connection and asking again is an acceptable cost for one provider with
  no continuation infrastructure anywhere else in this codebase.
- `_parse_provider_sse_events()`'s hand-rolled `event:`/`data:`/comment
  state machine (`chat_completion_helpers.py:198-262`) exists to decode
  error bodies shaped like SSE across many non-conforming providers. This
  project has exactly one wired provider whose stream is a conforming
  `data: {...}` / `data: [DONE]` sequence — a plain line-by-line split on
  `data: ` is enough, and the fuller state machine would be unexercised
  code for providers this project doesn't have.
- `stream_single_writer.py`'s superseded-writer fence (a monotonic token
  guarding against a retried stream racing an older one into the same
  callback, `:31-70`) is declined: `model_access.resolve()` makes one
  attempt at a time, never two concurrently, so there is no race for a
  fence to arbitrate.
- `plugin_stream_hooks.py`'s per-callback background-thread dispatcher with
  its own bounded queue (`:60-145`) is declined for `on_delta` itself: a
  single synchronous callback invoked inline from the SSE read loop is
  correct and simpler, with nothing slow or third-party on the other end.
  The one queue this item does add (`door/events.py`) is a different,
  narrower thing — the door's own hand-off to H30's future socket drainer,
  not a fan-out mechanism for multiple plugin callbacks.
- `stream_diag.py`'s provider-forensics header capture (Cloudflare ray id,
  per-provider headers, `:26-35`) is declined: it exists to debug hermes's
  multi-provider fallback chain; nothing here falls back between providers.

No hermes file addresses this item's other half (live run/span records and
cooperative stop) in a form worth adopting: hermes's closest analogue,
`agent/monitoring/*` and `agent/trace_upload.py`, is an externally-shipped
telemetry pipeline (OTLP export, a monitoring policy engine) for a hosted
fleet — D4's own spec.md already researched and declined
`agent/moa_trace.py` for the same reason (speculative infrastructure with no
second consumer here). This item's live records are a much smaller thing: a
few extra SQL rows, read by the same door nouns H20 already built.

## Design

**A pre-existing gap this item had to close before design could start: H20
wasn't in this worktree.** Before design, `door/nouns/{conversations,messages,runs,spans,traces,
artifacts}.py`, `door/turn_client.py` and `router.py`'s child-route dispatch
— all things this item extends — did not exist in this checkout. H20 had
been built, cold-reviewed and merged to `origin/main`/`origin/lane-a`
(`b1831e3`), but this worktree's local `lane-a` was stale. Fast-forwarded
(`git merge --ff-only origin/lane-a`) before any of the reading or design
below; confirmed a clean fast-forward, not a rewrite of anything local.

That merge also surfaced one unrelated, pre-existing lint break —
`src/sadana/subcommands/door.py`'s `DoorContext.nouns` dict literal lists
`"harness": harness` twice (a leftover from two branches each adding the
`harness` noun registration). Harmless at runtime (a dict just keeps the
last value) but fails `ruff` (F601) for anyone on this branch. Fixed inside
this item's own diff, at the user's direction, rather than as a separate
commit — see `## Files that change` in plan.md.

**Chain (a): the streaming port and the observer.**

**`model_access.py`.** `Request` gains `stream: bool = False`.
`ProviderManifest` gains `stream_fn: Callable[[Request, OnDelta],
RawResult] | None = None` (`OnDelta = Callable[[str], None]`), defaulted so
every existing provider manifest (38 unwired, `openrouter` wired) keeps
working with no changes. `send(request, on_delta=None)` calls `stream_fn`
when `request.stream` is true and the provider declares one, else
`request_fn` exactly as today — a provider with no `stream_fn` streaming a
request simply falls back to one non-streaming call, deltas or not, rather
than raising (matches `ProviderNotWired`'s existing posture of a named,
recoverable gap rather than a crash for the *unwired* case, but here it is
a silent, correct degrade because the *shape* of the outcome is identical
either way). `resolve(request, on_delta=None)` threads `on_delta` through
unchanged — the retry loop itself does not need to know streaming exists,
matching CLAUDE.md's "the retry loop lives once, in resolve()." `classify()`
is untouched: `stream_fn`'s job is to hand back the same `(status, body)`
shape `request_fn` already produces, assembled from the stream, so one
classifier still serves both.

**`model_providers/openrouter/provider.py`.** `stream_fn` sets
`stream: true` in the request body, opens the same `urllib.request` POST,
and reads the response body line by line (`resp.readline()` in a loop, not
`resp.read()`) rather than the existing `request_fn`'s single `read()`.
Each `data: {...}` line is JSON-parsed; `data: [DONE]` ends the loop.
Blank lines and any `event:`/`:`-comment line hermes's own multi-provider
parser has to handle (declined above) are simply skipped — this provider's
stream is a plain, conforming sequence. Each event's
`choices[0].delta.content` fragment, when present, is appended to an
accumulator and passed to `on_delta`. Each event's
`choices[0].delta.tool_calls`, when present, are merged into a
`dict[int, dict]` keyed by `tc_delta.get("index", 0)` — `function.name`
assigned (not concatenated), `function.arguments` concatenated, matching
the adopted hermes asymmetry above. `finish_reason` and `usage` are
overwritten on every event that carries them, so the loop naturally ends up
holding the last event's values, matching `_relay_final_response()`'s
approach. On loop exit, the function builds and returns
`(200, {"choices": [{"message": {"content": accumulated, "tool_calls":
[...]}, "finish_reason": ...}], "usage": {...}})` — byte-for-byte the same
shape `classify()` already parses for a non-streaming `200`. A transport
error raised mid-read (`OSError`, matching `request_fn`'s own existing
catch) returns `(None, {...})`, same as `request_fn`'s own transport-error
branch, so `classify()` treats it as transient without any new case. An
HTTP error status arriving before the first SSE line (a 4xx/5xx with a
normal JSON error body, no `stream: true` framing) is read and returned
exactly as `request_fn` already does — a stream attempt that fails before
it ever starts streaming is not a new kind of failure.

**`model_providers/stub/provider.py`** (new). `request_fn` returns a fixed
`(200, {"choices": [{"message": {"content": "..."}, "finish_reason":
"stop"}], "usage": {...}})`. `stream_fn` emits the same final content split
into three `on_delta` calls, then returns the identical body `request_fn`
would have. `env_vars=()` — no credential required, so every test and this
item's own development can exercise streaming with zero network and zero
cost. This becomes the second real member of `model_access.py`'s provider
registry (today only `openrouter` has a `request_fn`), and is registered
the same way every provider is — dropping a directory under
`model_providers/` — so no registry code changes.

**`conversation.py`.** A `TurnObserver` protocol — `turn_started(turn_key,
user_message_seq)`, `text_delta(seq: int, text: str)`,
`turn_finished(result: TurnResult)`, `should_stop() -> bool` — joins
`persist`'s existing shape as a second optional seam `run_turn` accepts.
`_noop_observer` mirrors `_noop_persist`'s existing "accepts anything, does
nothing, always returns False for should_stop" default, so a caller that
never opted in behaves exactly as today (Requirement 6). `complete(...,
on_delta=None)` threads the callback into the one `model_access.Request`
it builds. `run_turn(..., observer=None)`:

- calls `observer.turn_started(TurnKey(conversation, turn_seq),
  user_message_seq)` exactly once, right after PROLOGUE's own
  `append()` of the user message — `user_message_seq` is the same
  `turn_start_seq` the function already computes for `TurnResult.appended`,
  reused rather than recomputed a second way;
- passes a per-call `on_delta` into every `complete()` call in the loop
  (MODEL_CALL and the BUDGET_EXHAUSTED epilogue's own summary call) that
  forwards each fragment to `observer.text_delta(seq, text)` with a
  per-turn `seq` counter starting at 1, incremented once per delta
  regardless of which model call produced it — Requirement 1's "words as
  they arrive" is one continuous stream from the turn's point of view, not
  one stream per model call. A completion that turns out to carry tool
  calls (no visible text, or text discarded because the model changed its
  mind) is simply followed by no further deltas for that round — the
  console's own `sent` transition replaces whatever partial text a viewer
  saw, so a delta stream that "goes nowhere" needs no special handling
  here (matches the given decision: "deltas from a completion that turns
  out to contain tool calls are simply followed by nothing");
- calls `observer.should_stop()` at two points: immediately before each
  `complete()` call in the MODEL_CALL step (before spending a model call
  that a stop request already arrived for), and immediately after a
  TOOL_ROUND's dispatch loop finishes (before looping back to MODEL_CALL) —
  never mid-dispatch, since a plugin's own `call` node is not
  interruptible from here (Requirement 4's "next safe point"). A `True`
  answer ends the loop with `exit_reason = ExitReason.INTERRUPTED, detail =
  "stopped"` — the same `ExitReason` member `asyncio.CancelledError`
  already maps to, distinguished only by `detail` (`"turn cancelled"` vs
  `"stopped"`), so no new `ExitReason` member is needed and
  `door/nouns/runs.py`'s existing `interrupted -> stopped` mapping already
  covers it with no change;
- calls `observer.turn_finished(result)` once, at the very end, with the
  same `TurnResult` the function is about to return — after the EPILOGUE,
  so a `BUDGET_EXHAUSTED` turn's own best-effort summary attempt is
  reflected in what `turn_finished` sees.

`client_surface.take_turn(..., observer=None)` and
`plugin_dispatch.take_turn_and_reconcile(..., observer=None)` grow the same
parameter and pass it straight through to `conversation.take_turn`/
`run_turn` — no logic of their own, matching how `persist`/`record_turn`
already thread through both functions today.

**Chain (b): live runs, spans and stop.**

**`observability.py`.**

- `record_turn_started(turn_key, user_message_seq, started_at) -> str`
  (returns the minted `run_id`): inserts a `turn_runs` row with
  `id=ids.make_id("run")`, `state="running"`, `started_at=started_at`,
  `ended_at=NULL`, `exit_reason=""` (the column is `NOT NULL`, documented
  in the schema comment — an empty string, not a sentinel word, since
  every real value in that column is already a lowercase
  `ExitReason.value` and `""` cannot collide with one), and every
  cost/count column at its zero value (nothing has happened yet). Emits a
  ledger `created` row exactly as `_insert_turn_run` already does today.
- `record_turn` (existing) becomes the **completion** of that same row: an
  `UPDATE ... WHERE conversation_key = ? AND turn_seq = ?` setting `state`
  (via the same `done`/`failed` split `_insert_turn_run` already computes,
  now joined by `TURN_RUN_STATES` gaining `"stopped"` for
  `ExitReason.INTERRUPTED`), `ended_at`, `duration_s`, the cost/count
  columns, and the real `exit_reason` — a ledger `changed` row, not a
  second `created`. A caller that never calls `record_turn_started` first
  (every caller unchanged by this item — Requirement 6) falls back to
  today's exact behavior: `record_turn` still works standalone, inserting a
  finished row in one shot exactly as it always has, detected by "no row
  exists yet for this `(conversation_key, turn_seq)`" rather than a second
  parameter, so `RecordTurnFn`'s own signature does not change and no
  caller has to pass anything new.
- `record_plugin_run_started`/`record_plugin_run` — the identical pattern,
  one level down, for `plugin_runs`.
- New table `spans` (schema below), and `record_node(turn_key,
  seq_in_turn, node_seq, node, kind, status, started_at, ended_at, port,
  detail, input_preview, output_preview, error)`: one insert per node,
  `id=ids.make_id("spn")` — the reserved prefix `spans.py`'s own module
  docstring already names (`prefix="spn"`), distinct from `traces.py`'s own
  deliberate reuse of the `"run"` prefix for a different reason (H20's
  commit message: re-minting `traces` ids belongs to `observability.py`,
  outside that item's scope — spans is that observability-owned table, so
  it mints its own prefix cleanly from day one). Every insert and update
  in this item is a ledger row, same posture as every existing recording
  write; every recording write stays caught-and-logged, never raised into
  the turn or plugin run being observed — `record_node` included, no
  exception to the existing rule.

```sql
CREATE TABLE IF NOT EXISTS spans (
    id               TEXT PRIMARY KEY,
    conversation_key TEXT NOT NULL,
    turn_seq         INTEGER NOT NULL,
    seq_in_turn      INTEGER NOT NULL,
    node_seq         INTEGER NOT NULL,
    node             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    status           TEXT NOT NULL,   -- 'ok' | 'error' | 'skipped'
    started_at       REAL NOT NULL,
    ended_at         REAL,
    port             TEXT,
    detail           TEXT,
    input_preview    TEXT,
    output_preview   TEXT,
    error            TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1
);
```

Appended at the end of `ensure_schema`'s `_SCHEMA` string, per this item's
own instructions — a brand-new table needs no `_MIGRATED_COLUMNS`/
`PRAGMA table_info` guard (that machinery is for adding a column to a table
that already has rows; `CREATE TABLE IF NOT EXISTS` alone is already
idempotent for a table nothing has written yet).

**`plugin_manifest.py`.** `_run_graph` (and `run_graph`, threading it
through the same way `output_dir` already is) gains `node_sink: NodeSink |
None = None`. `NodeSink` is a pure protocol — `node_started(node: str,
kind: plugins.NodeKind, input_preview: str)`, `node_finished(trace:
plugins.NodeTrace, output_preview: str)` — called immediately before and
after each node kind's own body executes (the same points `trace.append`
already brackets today), for every node the walk actually visits,
including a `wait`/pause and a `call` parked for approval (a `node_started`
with no matching `node_finished` yet, since the walk itself ends there).
The walker stays clock-free: `node_sink` is called with no timing
information of its own; `plugin_dispatch.py`'s own sink implementation
reads `time.time()` on both sides and is the one thing that turns two
calls into one `record_node` insert. Previews are `_coerce_text(value)[:200]`
(the existing function, already used for `ask`'s own text coercion) —
applied to whatever `value` is about to be handed to (or was just returned
from) the node's body. The `except Exception as e:` branch already present
in the walker's `try` also calls `node_sink.node_finished` with a
`NodeTrace(ok=False, ...)` carrying `f"{type(e).__name__}: {e}"[:200]` as
the span's `error` — the exception's own message, not the trace's generic
`detail` sentence, matching this item's own instruction ("the message,
because a person reads it") and distinct from the trace's existing
`detail` field (which stays the generic `"node raised {type}"` the walker
already writes).

**`plugin_dispatch.py`.** `build_dispatch` gains three parameters:
`record_turn_started`/`record_plugin_run_started` (both defaulted to a new
`observability.noop_record_started`, the same no-op posture every other
optional recorder here already takes) and folds a `node_sink` builder into
its existing `dispatch()` closure — built fresh per `plugin_manifest.run_graph`
call, closing over that call's own `turn_key`/`seq_in_turn` (already
in scope) and a local `node_seq` counter starting at 0, incrementing once
per `node_started`. `client_surface.take_turn` passes all three (plus the
existing two) from `runtime.recorder`, which grows the matching three
fields.

**`door/run_control.py`** (new). A lock-guarded `dict[str, threading.Event]`
— the same "plain dict, bounded by concurrently-running turns, not total
history" shape `stores.py`'s own `_conversation_locks` already uses and
justifies (ponytail comment carried over): `register(run_id) -> None`
(creates and stores a fresh `Event`), `request_stop(run_id) -> bool`
(`True` and sets the event if `run_id` is registered, `False` otherwise —
the caller turns `False` into a 409), `unregister(run_id) -> None` (pops
the entry; called once from `turn_finished`, in a `finally`-equivalent
position, so a run's `Event` never outlives the run itself and two turns
sharing a stopped-then-restarted process never collide on a reused id —
ids are UUIDv7, never reused, so there is no collision to guard against
even without this, but the entry would otherwise leak for the life of the
process). Unregistering on every terminal path — `COMPLETED`, every
failure `ExitReason`, and `INTERRUPTED` itself — not only the happy path.

**The door's observer**, built in `door/nouns/messages.py`
(`turn_client.py` itself stays exactly as narrow as its own docstring
already requires — "calls take_turn and nothing deeper" — so the observer
object, which needs `conn`/`conversation_key`/the run-control registry, is
built where `messages.py` already has all three, and passed *into*
`turn_client.send(..., observer=...)` as one more argument that function
forwards unchanged):

- `turn_started(turn_key, user_message_seq)`: calls
  `observability.record_turn_started` to mint `run_id`, registers it with
  `run_control.register(run_id)`, inserts the assistant row
  (`msg_seq = user_message_seq + 1`, `role="assistant"`, `content=""`,
  `state="streaming"`, `run_id`, `id=ids.make_id("msg")`) with a ledger
  `created` row, and updates the user row already at `user_message_seq`
  to carry the same `run_id` (an `UPDATE ... SET run_id = ? WHERE
  conversation_key = ? AND msg_seq = ?` — the user row itself was written
  by `messages.py`'s own `create()` before calling `send()`, since its
  content and position are already fully known synchronously and do not
  need to wait for anything the turn produces).
- `text_delta(seq, text)`: puts `{"type": "ephemeral", "name":
  "message.delta", "harness_id": ..., "data": {"conversation_id": ...,
  "message_id": <the assistant row's id>, "delta": text, "seq": seq}}` on
  `door.events.ephemeral_queue` — a new, small `door/events.py` holding one
  bounded `queue.Queue` (`maxsize` from config, dropping the oldest frame
  under sustained backpressure the same way `plugin_stream_hooks.py`'s
  declined-elsewhere pattern would, but with no per-callback fan-out: one
  queue, one eventual drainer, H30's job). Nothing here writes to SQLite or
  the ledger — ephemeral frames are never ledgered, per this item's own
  rule.
- `turn_finished(result)`: calls the extended `_insert_messages` (see
  below) so the provisional assistant row is finalized with its real
  content in the same write that would have happened anyway; separately
  sets `state = "sent"` or `"failed"` (mirroring `messages.py`'s own
  existing failure branch, now folded into this one path instead of a
  second, separate `UPDATE` after the fact) with the turn's own
  diagnostic recorded nowhere new (the run's `exit_reason`/`detail` already
  carries it, per `docs/console/nouns/message.md`'s existing rule that a
  message never duplicates it); calls `observability.record_turn` to close
  the run row; calls `run_control.unregister(run_id)`.
- `should_stop()`: returns `run_control` — no, returns
  `not self._stop_event.is_set() is False`... **plainly**: returns
  `self._stop_event.is_set()`, where `self._stop_event` is the exact
  `threading.Event` `run_control.register` created for this run, held by
  the observer instance for the run's own lifetime (never looked up by id
  a second time — the observer already has it).

**A genuine tension, resolved rather than picked silently**: the
`messages` table schema comment states plainly, "a message row is never
edited once written (`_insert_messages`'s own invariant)" — and this item
needs exactly one row (the provisional assistant row) to be written once
as a placeholder and then completed once with its real content. Read
`_insert_messages`'s actual behavior rather than trusting the comment
alone: H20 already breaks the letter of it, narrowly, for the `state`
column and for `run_id` (`messages.py`'s own `UPDATE ... SET state =
'failed'`, and the bulk `UPDATE ... SET run_id = ?` after every turn) — so
the invariant, as actually built, already means "a message's `content`
does not change once it reflects something that happened," not "no row is
ever the target of a second write." This item's own change stays inside
that already-established boundary rather than widening it:
`_insert_messages`'s `present` computation changes from "every `msg_seq`
already in the table" to "every `msg_seq` already in the table whose
`state` is not `streaming`" — so a still-provisional row is not treated as
already-present, and is resubmitted — and the `INSERT` becomes `INSERT ...
ON CONFLICT(conversation_key, msg_seq) DO UPDATE SET content=excluded.content,
tool_calls_json=excluded.tool_calls_json, tool_call_id=excluded.tool_call_id,
state=excluded.state WHERE messages.state = 'streaming'`. Two guards, not
one: the Python-side `present` filter decides what gets resubmitted at
all, and the SQL-side `WHERE messages.state = 'streaming'` means that even
a resubmission of a row that has *already* settled (a scenario the
Python-side filter is not expected to allow, but SQL should not trust a
caller to have gotten right) can never overwrite a finished row's content.
`id` and `created_at` are excluded from the `DO UPDATE` list entirely — the
same asymmetry `_UPSERT_CONVERSATION_SQL` already established for
`conversations` — so the provisional row's own minted `id` (already handed
to the console the moment it was created, as `message_id` inside every
ephemeral frame) survives being finalized. This is the one edit this
item's own instructions ask for ("make it an upsert in H16's store"),
narrowed to the smallest change that keeps every other row genuinely
append-only. `_record_messages` (the ledger emitter) is extended to accept
which of the returned ids were freshly inserted versus promoted from
`streaming`, emitting `created` for the former and `changed` for the
latter — `harness.get_changes()` already folds both into the wire's single
`changed` kind, so this adds no new console-visible vocabulary
(CLAUDE.md's ledger-kind rule).

**`door/nouns/runs.py`**: `act()` gains a real body for `name == "stop"`
(every other name keeps returning `HARNESS_CAPABILITY_MISSING`, unreachable
either way while undeclared): looks the run up by `id` exactly as `get()`
already does (reusing `_row`), calls `run_control.request_stop(id)`; `True`
returns the run's own current rendering (`_render`, unchanged — the state
the console sees next is whatever the observer's own `turn_finished`
eventually writes, since stop is cooperative and this call does not
itself flip any state); `False` returns `problems.make("CONFLICT", ...)`
— the run is not held by anything in this process (already finished
between the router's own state check and this call, or the process
restarted since it started). `router.py` itself needs no change: its
existing `from_states=("running", "waiting")` check and `capability=
"runs.stop"` gate already do exactly the right thing once
`"runs.stop"` is declared.

**`door/capabilities.py`**: `DECLARED` gains `"streaming"`, `"runs.live"`,
`"runs.stop"`, appended in that order at the end — append-only, per the
file's own rule.

**Small doc updates**, bundled into this item because they describe
exactly the behavior this item changes (not new files, not a new
work item): `docs/console/nouns/run.md` (drop the "arrive with H21" note
for `running`/`waiting`/`stopped`, since this item is what makes them
real), `docs/console/nouns/span.md` (drop the "no table exists yet"
paragraph), `docs/console/nouns/message.md` (the "a failed turn does not
always produce an assistant row" paragraph is superseded — this item makes
the assistant row exist from the start of every turn, always, per the
locked decision below), `docs/console/wire.md` (capabilities list).

## Interface

- `model_access.Request.stream: bool = False`.
- `model_access.ProviderManifest.stream_fn: Callable[[Request, OnDelta],
  RawResult] | None = None`; `OnDelta = Callable[[str], None]`.
- `model_access.send(request, on_delta=None) -> Outcome`;
  `model_access.resolve(request, on_delta=None) -> Response | ...`
  (unchanged return type).
- `conversation.TurnObserver` (Protocol): `turn_started(turn_key: TurnKey,
  user_message_seq: int) -> None`; `text_delta(seq: int, text: str) ->
  None`; `turn_finished(result: TurnResult) -> None`; `should_stop() ->
  bool`.
- `conversation.run_turn(..., observer: TurnObserver | None = None)`;
  `conversation.take_turn(..., observer=None)`;
  `client_surface.take_turn(..., observer=None)`;
  `plugin_dispatch.take_turn_and_reconcile(..., observer=None)`.
- `observability.record_turn_started(turn_key, user_message_seq, started_at)
  -> str` (the minted `run_id`); `observability.record_plugin_run_started(
  turn_key, seq_in_turn, started_at) -> str`; `observability.record_node(...)
  -> None`; `observability.NodeSink` (Protocol, re-exported from
  `plugin_manifest` or defined here — decided in plan.md, not load-bearing
  either way).
- `plugin_manifest.run_graph(..., node_sink: NodeSink | None = None)`.
- `door/run_control.py`: `register(run_id: str) -> threading.Event`;
  `request_stop(run_id: str) -> bool`; `unregister(run_id: str) -> None`.
- `door/events.py`: `ephemeral_queue: queue.Queue[dict]`.
- Wire shapes: the ephemeral frame and the run/message state vocabularies
  are exactly as given in this item's own brief — quoted verbatim into
  `## Acceptance criteria` below rather than restated with any drift.

## Acceptance criteria

- [ ] `Request.stream`/`ProviderManifest.stream_fn`/`send(...,
  on_delta=...)`/`resolve(..., on_delta=...)` exist; every existing
  provider and every existing caller of `send`/`resolve` compiles and
  passes unchanged with no `on_delta` argument.
- [ ] `model_providers/openrouter/provider.py`'s `stream_fn` parses
  `tests/fixtures/openrouter_stream.txt` (a recorded transcript including a
  tool-call fragment sequence and `[DONE]`) into a body equal to the
  non-streaming shape; a simulated mid-stream disconnect returns
  `(None, {...})`.
- [ ] `model_providers/stub/provider.py` exists, is discovered by the
  existing registry with no code change, and its `stream_fn` emits three
  deltas whose concatenation equals its own `request_fn`'s content.
- [ ] `run_turn` with a real observer: `turn_started` called exactly once;
  deltas arrive with a strictly increasing per-turn `seq` starting at 1;
  `should_stop() -> True` before the second model call ends the turn with
  `ExitReason.INTERRUPTED, detail="stopped"` and no further model calls.
- [ ] A turn that completes normally with an observer produces the same
  `TurnResult.final_text` as the same turn with no observer (byte-equal,
  same seed/stub).
- [ ] Exactly one `messages` row per `msg_seq` after a fully streamed turn
  — never two rows for the assistant's own turn — and its final `content`
  matches `TurnResult.final_text`.
- [ ] Every node a plugin run visits yields exactly one `spans` row with
  `started_at <= ended_at`; a raising node's row carries the exception's
  message in `error`.
- [ ] `POST /v1/runs/{id}:stop` on a run this process is not holding (never
  registered, or already finished) answers `409 CONFLICT`; on a
  registered, running run it signals the run's `Event` and the run's own
  state eventually settles to `stopped`.
- [ ] `door.events.ephemeral_queue` receives frames in non-decreasing `seq`
  order for one turn.
- [ ] `door.capabilities.declared()` includes `"streaming"`, `"runs.live"`,
  `"runs.stop"`.
- [ ] `make verify` ends `VERIFY OK`, including the pre-existing
  `door.py` duplicate-key fix.
- [ ] `scripts/prove_streaming_e2e.py`: one real OpenRouter turn with
  `stream=True` printing each delta's `seq` and the final message; a
  second turn stopped after the first delta, printing the run's own final
  state. Deploy-stage evidence, not part of `make test`.

## Non-goals

- The console's own screens (the Stream page, a "this turn" panel, the stop
  button's own UI) — this item is the harness side only.
- A client reconnecting mid-turn and resuming a stream it missed part of;
  two viewers watching the same run at once. Both explicitly out of scope
  per intent.md.
- Actually delivering an ephemeral frame anywhere outside this process —
  `door/events.py`'s queue is written to; nothing in this item drains it
  onto a socket. That is H30.
- A `202`-promoted `messages.create` operation's `resource` field (the
  gap `docs/console/nouns/message.md` already names as H30's) — unrelated
  to streaming and not reopened here.
- Anything about `runs.live` beyond making `running`/`waiting`/`stopped`
  states real and spans queryable while a turn is in flight — no new
  filter, sort, or aggregate beyond what `runs.py`/`spans.py` already
  declare.

## Rejected alternatives

- **A second, streaming-only outcome type in `model_access.py`** (e.g. a
  `StreamingResponse` distinct from `Response`) instead of reassembling
  into the existing shape. Rejected: `classify()` would need a second
  branch for every one of its six outcomes, doubling logic that already
  works, for no reader that needs to tell the two apart — `run_turn`
  wants exactly one `Completion` either way, which is the whole point of
  the reference corpus's own `_relay_final_response()` precedent above.
- **Killing a turn outright on stop** (cancelling its `asyncio.Task`, or
  raising into it from outside) instead of cooperative polling. Rejected
  per this item's own locked decision and CLAUDE.md's existing
  `INTERRUPTED`/`asyncio.CancelledError` handling: a `call` node's body can
  run arbitrary first-party code off the event loop
  (`asyncio.to_thread`), and there is no safe point to interrupt that from
  outside without leaving a plugin's own side effect (a file write, an
  HTTP call already sent) half-done. Polling at two named safe points is
  slower to react but never corrupts a plugin's own effect.
- **A registry/dispatch table for `NodeSink`/`TurnObserver` implementations**
  (letting a caller select one by name). Rejected under CLAUDE.md's
  "a dispatch seam earns its cost only once a second real member exists":
  this item has exactly one real `TurnObserver` (the door's) and one real
  `NodeSink` (plugin_dispatch's clock-reading wrapper) — a plain optional
  parameter, not a seam, matches every other optional callback already in
  `run_turn`/`build_dispatch` (`persist`, `record_turn`, `approve`).
- **Replaying missed ephemeral frames from a buffer** for a client that
  connects mid-turn, instead of "nothing shown while nobody was watching
  is recoverable." Rejected per intent.md's own explicit non-goal — a
  buffer is stored state with a lifetime and an eviction policy
  (guideline 4: minimise mutable state) for a scenario this item's
  interview already declined.
- **Widening `_insert_messages` into a general-purpose upsert** for every
  row, not just a `streaming` one. Rejected in the Design section above:
  it would silently reopen "history only grows" for every already-settled
  row, for no caller that needs it — the conditional `WHERE messages.state
  = 'streaming'` gets the one behavior this item needs without touching
  the guarantee every other reader of `messages` already relies on.

## Concerns

- **The biggest state-machine risk in this item**: `messages.py`'s
  `create()` today reads `before`/`after` snapshots around a single
  blocking `turn_client.send()` call to figure out which rows the turn
  produced. With the observer writing the assistant row itself, at
  `turn_started`, `create()`'s own post-call reads change shape (it can
  read the row by `run_id` directly instead of diffing `msg_seq` ranges) —
  a real rewrite of that function's body, not an additive change, and the
  place most likely to regress the two existing accepted findings H20's
  own `review.md` already recorded (the empty-200-vs-404 list gap, and the
  untested failed-turn-marks-assistant-row path). Both need to be
  re-verified against the new code, not assumed still true.
- **Where the door's observer actually gets built** — `messages.py`,
  reading `ctx.conns.writer`/`conn` it already has — was not fully nailed
  down to a class/function signature here; plan.md must settle it exactly,
  because it is the one piece of this item with the most state to thread
  correctly (the run id, the `Event`, the assistant row's own id) through
  a callback interface (`TurnObserver`) that has to stay a pure protocol
  per the walker's own clock-free rule.
- **Testing a cooperative stop without a real clock race** needs the stub
  provider's `stream_fn` to yield control (e.g. `await
  asyncio.sleep(0)` between deltas) so a test's `should_stop` can flip
  `True` from another coroutine between two deltas deterministically,
  never a real `time.sleep` — testing-conventions' own ban on tests that
  depend on wall-clock timing applies directly here and needs a concrete
  answer in plan.md, not left implicit.
- **`ensure_schema`'s three-line checklist has now grown a fourth
  responsibility** (spans, alongside turn_runs/plugin_runs/artifacts) in
  the same function PERSONA-02's own postmortem already flagged once for
  exactly this growth pattern (see `[[project-sdlc-state]]`'s "the third
  table shipped... and the gap produced a runtime error"). `stores.ensure_schemas`
  already calls `observability.ensure_schema(conn)` as one line, so this
  item's new table rides the existing checklist with no fifth call site to
  add or forget — named here so a future reviewer does not have to
  rediscover that this was checked.
- **Policy conformance named explicitly**: `testing-conventions` (unit
  tests never touch the network or the real clock — the stub provider and
  the fixture transcript exist specifically to satisfy this for the
  streaming parser and the observer tests); this project has no
  `project-structure` or `reference-lookup` skill file to load — the
  reference-corpus consultation above follows CLAUDE.md's own
  "Our methodology for learning from the reference" section directly
  instead. No other policy skill in `.claude/skills/` applies (no
  security/brand/UX skill exists in this repo).
- **No tension found between the four design guidelines themselves for
  this item's central choice** (cooperative polling over forced
  cancellation): it is the cheapest of the three step-costs available —
  it makes an existing step (the loop's own iteration boundary) slightly
  heavier (one `should_stop()` call, one boolean check) rather than adding
  a new step (a supervisor task) or making an existing one harder to
  reason about (cancellation semantics through `asyncio.to_thread`). Said
  once here rather than re-argued per guideline, since all three guidelines
  1, 3 and 4 independently point at the same answer.
