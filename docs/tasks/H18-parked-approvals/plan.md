# Plan: parked approvals (from intent.md 2026-09-14)

Author: adam (engineer). Status: approved.

## Files that change

- `src/sadana/plugins.py` — `DagResult.paused_kind`/`paused_value`;
  `ResumeState.kind`/`decision`. Additive fields only.
- `src/sadana/plugin_manifest.py` — `parking_approve` sentinel (new
  function); `_run_graph`'s `call` branch gains the identity check and park
  return; the resume branch splits on `resume.kind`.
- `src/sadana/plugin_dispatch.py` — `resume_paused_run` signature grows
  `decision`/`answered_by`, `payload_text`→`payload` rename; marks the
  approvals row in the same transaction as the pause delete/rewrite.
- `src/sadana/conversation_store.py` — `approvals` table in `_SCHEMA`;
  `plugin_pauses` gains `paused_kind`/`paused_value_json` via
  `_MIGRATED_COLUMNS`; `Pause` gains `kind`/`paused_value`;
  `save_pause_from_result` writes the approvals row too;
  `load_waiting_approval`, `mark_approval_decided` (new).
- `src/sadana/client_surface.py` — `take_turn` gains keyword-only
  `approve: plugins.ApproveFn | None = None`; outstanding-pause branch
  splits on `pause.kind` (wait resumes as today; call stores the message,
  returns `ok=False` with the `[waiting_for_approval] <id>` diagnostic).
- `src/sadana/subcommands/chat.py` — one line:
  `approve=plugin_manifest._default_approve if sys.stdin.isatty() else None`.
- `src/sadana/scheduling.py` — `tick()` calls
  `door.nouns.approvals.expire_due(runtime.connections, now)`.
- `src/sadana/door/nouns/approvals.py` (new) — the six-verb module: `spec`,
  `CAPABILITIES`, `list`/`get`/`create`(unavailable)/`update`(unavailable)/
  `remove`(unavailable)/`act`(approve/decline/answer)/`search_doc`, plus
  `expire_due(conns, now) -> int`.
- `src/sadana/door/capabilities.py` — `DECLARED` gains `"approvals.wait"`,
  `"approvals.call"` (two appended lines; shared file, per the amendment).
- `src/sadana/subcommands/door.py` — one import, one dict entry
  (`"approvals": approvals`, alphabetically first) in the `nouns={...}`
  literal at line 93 (shared file, per the amendment). That literal was a
  single line (`nouns={"harness": harness},`) with no per-entry-line shape
  to insert into — converted once to one-entry-per-line, the same
  reformatting the amendment explicitly permits for `capabilities.py`'s
  `DECLARED` tuple, applied here by the same reasoning (two lanes inserting
  into one line is unmergeable; the resolution rule needs one line per
  entry to work at all).
- `docs/console/nouns/approvals.md` (new) — the noun's own doc page (own
  file; `docs/console/nouns.md` itself is left untouched, per the
  amendment).
- `tests/unit/test_plugin_manifest.py` — `parking_approve` park behavior on
  a `call` node; resume-with-`decision="approve"`/`"decline"` on a
  `call`-kind `ResumeState`; existing `wait` resume tests unaffected;
  `DagResult`/`ResumeState` new-field construction sanity.
- `tests/unit/test_plugin_dispatch.py` — `resume_paused_run`'s three
  decisions, the approvals-row marking, the not-found branch still marking
  it.
- `tests/unit/test_conversation_store.py` — `approvals` table round trip
  (`save_pause_from_result` → `load_waiting_approval` → `mark_approval_decided`),
  `plugin_pauses` legacy-row migration (`paused_kind` reads back `None`,
  `load_pause` defaults that to `kind="wait"`).
- `tests/unit/test_client_surface.py` — call-parked conversation stores the
  message and doesn't run; wait-parked still resumes; TTY isatty→
  `_default_approve` wiring covered at the `take_turn` level with a fake
  `approve`.
- `tests/unit/test_scheduling.py` — `tick()` calls `expire_due` before the
  trigger loop; a failing `expire_due` row doesn't stop trigger firing or
  other rows' expiry.
- `tests/unit/test_state_words.py` (not in the original plan — added during
  implementation, see note below) — `conversation_store.APPROVAL_STATES`
  registered in `_CLOSED_SETS`, so `save_pause`'s new `approvals` write
  (state `"waiting"`) doesn't trip this file's own closed-set contract.
- `tests/unit/test_subcommands_chat.py` (not in the original plan — added
  during implementation) — its one interactive-approval test fakes
  `input()` but never faked `sys.stdin.isatty()`; under pytest that is
  `False`, so `chat.py`'s new isatty gate now (correctly) parks instead of
  prompting. Fixed by also faking `isatty()` True in that one test, so it
  keeps proving what it always proved: a TTY session that types "n" stops
  the call node without running it.
- `tests/contract/test_console_grammar.py` (not in the original plan —
  added during implementation, and a 5th shared file; put to the user
  directly before writing it, per the amendment's own rule) — one line,
  `test_get_harness`'s exact-list capabilities assertion loosened to a
  subset check (`>= {"grammar.v1", "changes", "inventory"}`), since
  `DECLARED` growing by *any* work item's own capability (mine or the other
  lane's) breaks an exact-list assertion the same way. The user chose the
  subset form specifically because it also protects the other lane's own
  addition from the identical collision.
- `tests/unit/test_door_capabilities.py` and `tests/unit/test_door_nouns_harness.py`
  (not in the original plan — found by grepping for every other place
  hardcoding the exact pre-H18 `DECLARED` tuple, same fix, same reasoning
  as `test_console_grammar.py` above, applied without a second
  confirmation since the user already decided the shape of this fix).
- `tests/contract/nouns/test_approvals.py` (new) — the noun proven against
  `router.handle()` directly: list/get/act, state gate (409), `If-Match`
  (412), the badge `count=true` query, capability presence in `DECLARED`.
- `scripts/prove_call_approval_e2e.py` (new) — Deploy-stage evidence script:
  parks a real `call` node through `gateway_dispatch.handle_inbound`
  (webhook path, non-interactive), then approves it by sending a real HTTP
  request to a `sadana door serve` process, printing the `approvals` row
  before and after.

## Order of work

1. `plugins.py` additive fields (`DagResult`, `ResumeState`). Nothing calls
   them yet; `make typecheck` on this file alone proves the shapes compile.
2. `plugin_manifest.py`: `parking_approve` + the `call`-branch park + the
   resume-branch split, with its own unit tests (park returns the right
   `DagResult`; approve/decline resume paths; existing `wait` tests
   untouched). Riskiest step (see Risks) — sequenced second, right after
   the inert field changes, so it is proven by its own narrow suite before
   anything else calls into it.
3. `conversation_store.py`: schema, `Pause` fields, `save_pause_from_result`
   writing the approvals row, `load_waiting_approval`, `mark_approval_decided`,
   with unit tests. Independent of step 2's internals (only needs a
   `DagResult` with the new fields set by hand), sequenced after it only
   because step 4 needs both.
4. `plugin_dispatch.py`: `resume_paused_run`'s new signature, wired to step
   3's `mark_approval_decided`, with unit tests — this is where steps 2 and
   3 are proven to compose.
5. `client_surface.py` + `subcommands/chat.py`: the `approve` parameter, the
   `pause.kind` branch, with unit tests. Depends on steps 2-4.
6. `scheduling.py` + `door/nouns/approvals.py`'s `expire_due` (module
   created here with just `expire_due` and the `spec`/`CAPABILITIES`
   scaffolding). Depends on step 4 and step 3.
7. `door/nouns/approvals.py`'s remaining six verbs, `door/capabilities.py`,
   `subcommands/door.py`'s registry line, `docs/console/nouns/approvals.md`,
   and the contract test.
8. `scripts/prove_call_approval_e2e.py` — written and run last, against
   everything above already green under `make test`.
9. Self-check (`/ponytail-review` + `/simplify` on the diff), then
   `make verify`.

## Risks

**What could this change break?**
- Every existing caller of `resume_paused_run` breaks at the signature
  level (`payload_text` → `payload`, new required `decision`).
  `grep -rn "resume_paused_run"` outside `plugin_dispatch.py`/
  `client_surface.py`/tests returns nothing, so every call site is already
  in this plan's own file list.
- `_run_graph`'s resume branch is restructured (split on `resume.kind`);
  every existing `wait`-resume unit test in `tests/unit/test_plugin_manifest.py`
  must keep passing unchanged, since `kind="wait"` is the default-shaped
  path and its behavior is not supposed to change.
- `tests/unit/test_plugin_dispatch.py`'s existing tests construct
  `ResumeState` by hand in a few places — each needs `kind=` added (they
  fail to construct, loudly, not silently misbehave, since `kind` has no
  default).
- The `plugin_pauses` schema migration (`_MIGRATED_COLUMNS`) runs against
  every existing store on next open — the same idempotent
  `PRAGMA table_info` guard every prior migration in this file already
  uses; step 3 adds an explicit test proving a pre-H18-shaped row (no
  `paused_kind` column value) still loads.
- `scheduling.tick()`'s return value (`int`, "how many fired") must keep
  meaning "triggers fired" — `expire_due`'s own count is not folded into
  it.

**Which step is the most risky, and why that one?**
Step 2 (`plugin_manifest._run_graph`'s split). It is the one place both the
live-park path and the two resume paths (approve-then-run-body,
decline-without-running) touch code `F1`/`G1-call-node-run`'s own passing
tests already depend on, and it is the only step where a mistake could let
a `call` body run without having been approved — the one invariant
CLAUDE.md states explicitly and spec.md's requirement 2 traces to.
Sequenced second, right after the purely additive field changes, so it is
proven by its own dedicated "approve runs the body, decline never does"
test pair before `plugin_dispatch.py`, `client_surface.py`, or the door
ever call into it.

**Which options did spec.md already reject, and is this plan drifting
toward one?** Re-checked against spec.md's own Rejected alternatives:
not adding a second `resume`-kind parameter to `run_graph` (identity check
on `approve` alone, as designed); not collapsing `paused_kind` into
`paused_node`'s own value (a separate field, as designed); not guessing
`answered_by` from `memory.owner_account()` for the TTY/webhook path (left
`None` there, only the door supplies a real value); not touching
`ledger._NOUNS["approvals"].sources` or `door/grammar.py` (neither appears
in this plan's file list — both are explicit spec.md Non-goals). No drift
found.

## Proof

- `make typecheck` green after step 1 and again after step 2.
- `tests/unit/test_plugin_manifest.py`: existing `call`-node and `wait`-
  resume tests unchanged and green; new
  `test_call_node_parks_under_parking_approve` (asserts `paused_kind=="call"`,
  `paused_value` equals the value flowing into the node, and `approve` is
  never awaited — proven with a fake `approve` distinct from
  `parking_approve` that raises if called, so the test proves the identity
  check specifically, not "any non-default approve parks");
  `test_resume_call_approve_runs_body_and_continues`;
  `test_resume_call_decline_fails_without_running_body` (the node's body
  replaced with a fake that raises if called, proving it never ran).
- `tests/unit/test_plugin_dispatch.py`: `resume_paused_run`'s `"answer"`
  path (existing, renamed call sites), `"approve"` and `"decline"` paths
  each asserting the approvals row was marked via `mark_approval_decided`,
  and the not-found/no-longer-resolves branch still marking it.
- `tests/unit/test_conversation_store.py`: `save_pause_from_result` writes
  an `approvals` row with `expires_at` computed from
  `SADANA_APPROVALS_TTL_S` (monkeypatched to a small value in one test to
  prove the env var is read, not just defaulted); `mark_approval_decided`
  transitions state/answered_by/answer/decided_at and writes one ledger row
  (visible via `ledger.changes_since`); a `plugin_pauses` row with no
  `paused_kind` value reads back `kind="wait"` via `load_pause`.
- `tests/unit/test_client_surface.py`: a call-parked conversation appends
  the incoming text as a stored `user` message (visible via
  `conversation_store.load`), returns
  `TurnOutcome(ok=False, diagnostic="[waiting_for_approval] <real appr_ id>")`,
  and never reaches `build_dispatch`/`dispatch` (a fake that raises if
  called proves this); a wait-parked conversation still resumes exactly as
  before.
- `tests/unit/test_scheduling.py`: `expire_due` is called once per `tick()`;
  a row whose expiry raises is caught and logged without stopping either
  other rows' expiry or the existing trigger loop, matching `tick()`'s own
  per-trigger `try`/`except` posture.
- `tests/contract/nouns/test_approvals.py`: a waiting approval seeded
  directly against the store (proving the door's own six verbs, not the
  plugin walk), then: list with `filter=state%20%3D%20waiting&count=true`
  returns the badge shape; get; approve returns 200 with the updated
  resource; decline on an already-decided row is 409; a stale/missing
  `If-Match` is 412; `"approvals.wait"`/`"approvals.call"` are present in
  `door.capabilities.DECLARED`.
- `scripts/prove_call_approval_e2e.py` run once by hand: prints the
  `approvals` row (as JSON) before the approve call and after, showing
  `state: waiting` → `state: approved`, `answered_by` set — pasted as
  Deploy-stage evidence, not part of `make test`.
- `make verify` ending `VERIFY OK`.

## Deploy-stage cold review findings, closed

A cold review found three gaps this plan committed to but the first build
pass did not actually deliver, all now closed in the same diff (not a
separate work item — this build was still open):

1. **A real bug**: `act(..., "approve", ...)` on a `wait`-kind row (or
   `"answer"` on a `call`-kind row) reached `resume_paused_run`'s own
   `decision`/`kind` `assert` as an uncaught `AssertionError`, which
   router.py's catch-all turned into `500 INTERNAL` — not the `409
   CONFLICT` spec.md's own Acceptance criteria already promised. Fixed
   with `door/nouns/approvals._REQUIRED_KIND`, a second gate `act` checks
   itself before ever calling `resume_paused_run`. Proven by
   `test_approve_on_a_wait_kind_row_is_409_not_a_crash` in
   `tests/contract/nouns/test_approvals.py`.
2. **This plan's own two `test_scheduling.py` tests were never written** in
   the first build pass (`test_tick_calls_expire_due_once`,
   `test_tick_one_failing_approval_expiry_does_not_stop_trigger_firing`) —
   written now, both green.
3. **A plan deviation that went unflagged**: `resume_paused_run` gained a
   required `state` parameter (from a `/simplify` altitude fix — see
   spec.md's Interface section for the full account) without a note here
   or in spec.md at the time. spec.md's Interface section now documents it.
