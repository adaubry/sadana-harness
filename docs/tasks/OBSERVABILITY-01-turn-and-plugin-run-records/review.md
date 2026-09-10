# Review: OBSERVABILITY-01 turn-and-plugin-run-records (from plan.md 2026-09-10)

Reviewed: d6300f69..working tree — 15 files, +827/-38 (everything uncommitted;
no feature branch exists yet, per this stage's own instructions)
Reviewer context: fresh session (delegated cold review)
Second opinion: none — ran during build (self-check), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/OBSERVABILITY-01-turn-and-plugin-run-records: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 27 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 15%]
........................................................................ [ 31%]
........................................................................ [ 47%]
........................................................................ [ 63%]
........................................................................ [ 79%]
........................................................................ [ 95%]
......................                                                   [100%]
454 passed in 5.81s
TESTS OK
VERIFY OK
```

## Scope

`git diff d6300f69` + `git status` (new files were `git add -N`'d, so they show
in the diff): 15 files, +827/-38.

`plan.md` § Files that change names 11 files. All 11 are touched. The diff
also touches 4 files `plan.md` does not name: `CLAUDE.md`,
`src/sadana/conversation.py`, `src/sadana/subcommands/gateway.py`,
`tests/conftest.py`. See Important finding 1.

`docs/audits/2026-09-10/findings.md` is untracked but unrelated to this item
(a separate audit artifact) — out of scope for this review, not counted
above.

## Findings

Three passes run: Bugs, Security, Compliance. Security is clean — no new
external dependency or service (spec.md Requirement 5), no PII or secrets in
what's recorded (`turn_runs`/`plugin_runs` hold only counts, durations, and
identifiers, never message content), no injection surface (`observability.py`
uses parameterized `?` placeholders throughout, never string-built SQL). Four
Important findings below, all Bugs/Compliance; three Nits.

### Important

1. **[Compliance] `plan.md` § Files that change omits 4 files the diff
   touches.** `CLAUDE.md` (two new "Please do" bullets on best-effort
   recording and natural-key reuse), `src/sadana/conversation.py` (a new
   `Conversation.pending_turn_key` property), `src/sadana/subcommands/gateway.py`
   (wiring `observability.make_recorder` into `cmd_gateway_run`), and
   `tests/conftest.py` (shared `turn_result()`/`dag_result()` fixture
   helpers) are none of them in `plan.md`'s file list. Per this stage's own
   rule, a file the plan did not name is an Important finding regardless of
   whether the change itself is sound — and the `conversation.py` one is the
   file `spec.md` § Design explicitly said would need no new field
   ("neither `conversation.py` nor `plugins.py` needs a new field"). The
   property turns out to be necessary (`dispatch()` needs a `TurnKey` before
   the turn it belongs to has finished — see finding 2), but `plan.md` never
   says so.

2. **[Bugs] A plugin dispatched from inside an `ask`-spawned child turn is
   recorded under the parent's `TurnKey`, not the child's own.**
   `build_dispatch()` binds `turn_key = conversation.pending_turn_key` and a
   closure-local `seq_in_turn` once per call
   (`src/sadana/plugin_dispatch.py:169-170`). `ask()` passes that exact same
   `dispatch` closure into `run_child(..., dispatch=dispatch, ...)`
   (`plugin_dispatch.py:186`), which forwards it unchanged into
   `take_turn(child, ..., dispatch=dispatch, ...)`
   (`src/sadana/conversation.py:1310-1318`) — the child conversation's own
   turn loop. If the child's tool surface includes a tool that maps to
   another plugin, the resulting `dispatch()` call inside the child's turn
   is the identical closure, so `record_plugin_run(turn_key, seq_in_turn, ...)`
   reports the *parent's* `turn_key` and continues the *parent's*
   `seq_in_turn` sequence — not the child's own turn. This violates
   `spec.md` Requirement 2 ("which turn it happened inside") for every
   plugin call nested inside an `ask`. No test in the diff dispatches from
   within a spawned child turn:
   `test_build_dispatch_calls_record_plugin_run_once_per_dispatch_call`
   (`tests/unit/test_plugin_dispatch.py`) only dispatches at the top level,
   and `test_build_dispatch_ask_calls_record_turn_with_the_childs_own_turn_result`
   only checks `record_turn`, not a nested `record_plugin_run`. This is the
   same closure-scoping shape `docs/reference/dispatch_closure_state_bug.md`
   already documents for this exact trio of functions, and `plan.md` § Risks
   itself names this as "the most risky step" — it landed here.

3. **[Compliance] `plan.md` § Proof's own claim about failure isolation is
   false as written, and the code follows the narrower, correct contract
   instead.** `plan.md` promises: "a raising fake recorder does not change
   `TurnResult`/`DagResult` or propagate out of
   `take_turn_and_reconcile`/`dispatch`/`ask`." The actual test,
   `test_build_dispatch_does_not_add_its_own_guard_around_the_given_recorder`
   (`tests/unit/test_plugin_dispatch.py`), asserts the opposite —
   `pytest.raises(RuntimeError, match="boom")` — when the given recorder
   itself raises. `spec.md` § Interface only promises the narrower
   guarantee ("a `sqlite3.Error` during a recording write is caught inside
   `observability.py`"), and the implementation matches that. `plan.md`'s
   Proof text is the artifact that's now wrong and needs correcting before
   this item closes — it currently describes behavior the code does not
   have.

4. **[Compliance] Acceptance criterion "verified by exercising `sadana runs`
   against a conversation driven through the gateway" is not discharged by
   any test in the diff.** The added test,
   `test_handle_inbound_records_the_turn_when_given_a_recorder`
   (`tests/unit/test_gateway_dispatch.py`), checks the `turn_runs` row with
   a direct `SELECT` on `conn` — it never calls `cmd_runs` or the `runs`
   subcommand at all. Functionally the row is there, but the criterion as
   `spec.md` literally wrote it (exercising `sadana runs` itself) isn't
   exercised by anything in this diff.

### Nits

- [Compliance] Acceptance criterion "A `sadana chat` turn that dispatches a
  plugin produces one `turn_runs` row and one `plugin_runs` row..." isn't
  exercised end-to-end through `cmd_chat` in
  `tests/unit/test_subcommands_chat.py` — only the plain-turn case is (
  `test_cmd_chat_records_the_turn`). The plugin-dispatch/`node_count`
  correctness is proven instead at the `plugin_dispatch.py`/`observability.py`
  unit layers, which functionally covers it, just not through the literal
  path the criterion names.
- [Bugs] `test_build_dispatch_ask_calls_record_turn_with_the_childs_own_turn_result`
  (`tests/unit/test_plugin_dispatch.py`) uses `_fake_run_child_factory`,
  whose fake `TurnResult.turn_key` is `TurnKey(conversation=parent.key,
  turn_seq=0)` — the same conversation key as the parent, not a distinct
  child key the way the real `run_child` mints one
  (`conversation.py:1288`, `_child_key(...)`). The test can't actually tell
  "keyed by the child's own `TurnKey`" (acceptance criterion 4) apart from
  "keyed by the parent's," because the fake never simulates a distinct
  child key.
- [Nit] `observability.RecordTurnFn` is `Callable[[TurnResult, float],
  Awaitable[None]]`, dropping the `TurnKey` parameter `spec.md` § Design
  wrote out explicitly (`Callable[[TurnKey, TurnResult, float],
  Awaitable[None]]`) — a reasonable simplification since `TurnResult.turn_key`
  already carries it, but `spec.md`'s Design section is now stale on this
  one signature.

## Compliance pass detail

- **`plan.md` § Proof items**: schema idempotency, round-trip write, and
  forced-failure swallowing are all discharged in `test_observability.py`
  (`test_make_recorder_creates_both_tables_on_a_fresh_store`,
  `test_make_recorder_is_idempotent_on_a_store_that_already_has_the_tables`,
  `test_record_turn_writes_a_row_matching_theturn_result`,
  `test_record_plugin_run_writes_a_row_matching_thedag_result`,
  `test_record_turn_swallows_a_write_failure_instead_of_raising`,
  `test_record_plugin_run_swallows_a_write_failure_instead_of_raising`).
  The `test_plugin_dispatch.py` and `test_gateway_dispatch.py` Proof items
  are discharged except where noted in Important findings 3 and 4 above.
  `test_subcommands_runs.py`'s Proof item (output matches directly-inserted
  rows, unknown key prints nothing without raising) is discharged by
  `test_cmd_runs_prints_recorded_turn_and_plugin_rows` and
  `test_cmd_runs_unknown_key_prints_a_clear_message_not_an_error`.
  `make verify` ends `VERIFY OK` above.
- **`spec.md` § Acceptance criteria**: all eight are satisfied except the
  two literal gaps in Important findings 3(partially, re: failure-isolation
  wording lives in `plan.md` not `spec.md`) and 4, and the Nit on the
  chat-with-plugin end-to-end path. Idempotent tables:
  `test_observability.py`. Plain-turn row with correct fields:
  `test_observability.py:61-75` (field correctness) +
  `test_subcommands_chat.py::test_cmd_chat_records_the_turn` (through the
  real CLI path). Plugin-turn row + `node_count`: `test_observability.py:93`
  (field correctness) + `test_plugin_dispatch.py::test_build_dispatch_calls_record_plugin_run_once_per_dispatch_call`
  (call-site correctness) — not through `chat.py` itself (Nit above).
  Child-turn row: `test_plugin_dispatch.py::test_build_dispatch_ask_calls_record_turn_with_the_childs_own_turn_result`,
  weakened per the Nit above. Forced-failure isolation:
  `test_observability.py`'s two swallow tests plus
  `test_build_dispatch_does_not_add_its_own_guard_around_the_given_recorder`
  composing correctly (`observability.py` guarantees no exception ever
  reaches `plugin_dispatch.py` for the one error type it can raise,
  `sqlite3.Error`). `sadana runs` readback:
  `test_subcommands_runs.py`. Gateway parity: not discharged as written
  (Important finding 4).
- **`spec.md` § Rejected alternatives**: none reinstated. No event-bus/
  `subscribe()` registry (`Recorder` is two plain callables). No wire-format
  versioning. No dollar-cost field (`turn_runs` stores only token counts).
  No second SQLite file (`make_recorder(conn)` takes the connection
  `chat.py`/`gateway_dispatch.py` already hold; `subcommands/runs.py` opens
  its own connection the same way every other subcommand already does, not
  a second writer). Checked file by file against the diff.
- **Design principle 1 (learn from the reference first)**: `spec.md` § Design
  and `plan.md` § Reference corpus checked both cite hermes's
  `agent/monitoring/{events,emitter}.py` and name what was adopted (one
  structured record per run) versus declined (pluggable sinks, wire
  versioning) with reasons. Satisfied.
- **Design principle 2 (reduce the number of bets)**: the new
  `Conversation.pending_turn_key` property (finding 1) is a small,
  reversible addition — a computed property, not stored state — so it
  doesn't read as an expensive bet on its own; the finding is about
  `plan.md` not naming it, not about the bet's size.
- **Design principle 3 (more plugins, not more core)**: not applicable —
  this item adds no plugin surface.
- **Design principle 4 (catch the scenario at the least step-cost)**:
  `spec.md` § Rejected alternatives 3 already weighs this (recording at the
  `plugin_dispatch.py` boundary versus inside `conversation.py`/
  `plugin_manifest.py` directly) and the diff matches that choice. Finding
  2 is a correctness bug in that placement, not evidence a cheaper
  placement existed.
- **Design principle 5 (minimise mutable state)**: `seq_in_turn` is a
  closure-local counter matching the existing `ChildSeqTracker` posture,
  fresh per `build_dispatch()` call. `Recorder` is a frozen dataclass of
  closures with no turn-specific captured state (unlike `bind_persist()`),
  so building it once per process/`_chat_loop` (`chat.py`, `gateway.py`)
  rather than once per turn is correct and doesn't reintroduce the staleness
  risk `CLAUDE.md` warns about for `bind_persist()`.

## Decision

Approved by Adam Aubry, 2026-09-10, as-is — the four Important findings are
not fixed in this branch. Finding 2 (a plugin dispatched from inside an
`ask`-spawned child turn would be recorded under the parent's `TurnKey`) is
a real defect but is currently dormant: `ask()` hardcodes `tools=frozenset()`
for every spawned child, so a child can never actually have a tool to
dispatch today. Worth a `maintain`-stage `intent.md` the day a spawned child
is given real tools, not before.
