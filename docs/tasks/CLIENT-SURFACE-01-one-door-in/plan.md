# Plan: One door in (from intent.md 2026-09-11)

Spec: docs/tasks/CLIENT-SURFACE-01-one-door-in/spec.md

## Announced deviations from spec.md

Recorded here so the diff does not silently disagree with the approved spec.
The first two were declared before implementation and are naming and
placement, not design. The last two were found *during* implementation and
are amendments to this plan, added when they were found rather than
discovered later by a reviewer — a deploy-stage self-check caught that 3 had
gone unannounced, which is the failure this section exists to prevent.

1. **The value is `Runtime`, not `Surface`; the opener is `open_runtime()`.**
   `surface` is already taken in this codebase and means something else:
   `conversation.ToolSurface`, `build_surface()`, `filter_surface()`,
   `surface_hash()` and `Conversation.tool_surface` all name the *tool*
   surface (`conversation.py:188-259`), and `conftest.py:79` binds a local
   `surface = build_surface(...)`. `client_surface.Surface` would read as "a
   client's tool surface", which is not what it is. The module keeps its
   block name, `client_surface.py`.
2. **`conn_lock` stays a module-level lock in `client_surface.py` rather than
   becoming a field on the value.** spec.md's Concerns bundled two things that
   turn out to be separable: the lock's home, and whether `scheduling.py`
   keeps its own copy of the assembly. This plan takes the module-level lock
   *and* still collapses the assembly. There is one store path per process, so
   a module-level lock is correct; putting it on a frozen value forces a
   `cast` in `test_scheduling.py`'s recording-lock substitution and buys
   nothing today. `scheduling.py` and its test retarget the module name, one
   word each.

3. **The door has a third call, `open_conversation()`, and `take_turn` is not
   the only way in.** spec.md's Interface has two functions and
   `create_if_missing: bool`; there is a third, because a terminal shows a
   prompt before anybody has said anything, so its conversation has to exist
   before the first turn — `tests/unit/test_subcommands_chat.py:121` pins
   that an immediate EOF still leaves a created conversation behind. A
   channel never needs it: a webhook message is the first thing that
   happens, so it still passes `create_as` to `take_turn` and lets the turn
   create it. `open_conversation(..., template_name=None)` means "it must
   already exist" and raises `ConversationNotFound`; a name means "create it
   now" and raises `ConversationAlreadyExists` — the same
   None-means-must-exist convention `create_as` already uses, so it is one
   rule spelled the same way in both places, not two. This replaces the
   `start_conversation()` that a first pass of this implementation shipped,
   which covered only the create half and left the must-exist half in
   `subcommands/chat.py` reaching around the door into the store.
4. **`CLAUDE.md:61`'s rule is narrower than the line the user approved.** The
   approved wording claimed `create_conversation` has exactly one call site
   in `src/`. Running that check found `eval_harness.py:124` already calling
   it, so the claim was false when written. The rule now covers
   `build_dispatch` and `take_turn_and_reconcile` — both genuinely
   single-site — and names `eval_harness.py` as the one sanctioned
   non-client caller, because an eval task is a hermetic single turn with no
   store, no account and a stubbed dispatch (`eval_harness.py:68-91`), and
   routing it through the door would destroy EVAL-01's design.
   `spec.md`'s acceptance criterion was deliberately **not** edited to match;
   that discrepancy is reported at Deploy instead of reconciled away.

## Files that change

- `src/sadana/client_surface.py` (new) — the door.
- `tests/unit/test_client_surface.py` (new) — the door's own tests.
- `src/sadana/gateway_dispatch.py` — body replaced by translation: a
  `MessageEvent` becomes an account, a conversation key and text, then
  delegates. `conn_lock` deleted from here.
- `tests/unit/test_gateway_dispatch.py` — 8 `handle_inbound(...)` call sites
  rebuilt on a `Runtime`; no assertion's meaning changes.
- `src/sadana/scheduling.py` — `tick()` and `run_tick_loop()` take the
  `Runtime` and drop `conn`, `plugin_set`, `persona`, `provider`, `model`,
  `record_turn`, `record_plugin_run`; the two `with gateway_dispatch.conn_lock`
  blocks become `with client_surface.conn_lock`.
- `tests/unit/test_scheduling.py` — 5 `tick(...)` call sites, and the
  monkeypatch target at line 131 (`gateway_dispatch` → `client_surface`).
- `src/sadana/subcommands/gateway.py` — the eight-line assembly in
  `cmd_gateway_run` becomes one `open_runtime()`; the tick thread receives
  the runtime instead of seven keyword arguments.
- `tests/unit/test_subcommands_gateway.py` — the assertion at line 82
  (`"plugin_set" in seen` → `"runtime" in seen`), same intent: the tick
  thread got its wiring.
- `src/sadana/subcommands/chat.py` — `cmd_chat` opens the runtime and
  resolves `--account`; `_chat_loop`'s copied turn body becomes one
  `take_turn` call and the rendering decision.
- `tests/unit/test_subcommands_chat.py` — existing tests stay as the
  rendering oracle; one new test for the pause defect.

`CLAUDE.md:61` was already amended (approved this conversation) and is part of
this work item's commit.

## Order of work

1. **Write `src/sadana/client_surface.py`, with no callers.** `conn_lock`,
   `Runtime` (frozen: `conn`, `plugin_set`, `persona`, `provider`, `model`,
   `recorder`), `open_runtime(*, provider=None, model=None)`, `TurnOutcome`
   (frozen: `ok`, `answer`, `diagnostic`), and
   `async take_turn(runtime, *, account, conversation, text, create_if_missing)`.
   `take_turn`'s body is `gateway_dispatch.handle_inbound`'s body with the
   `MessageEvent` removed: acquire `conn_lock`; if `load_pause` returns a row,
   `resume_paused_run` and append the reply as one `assistant` message;
   otherwise `load` the conversation, or `create_conversation` when
   `create_if_missing` (and raise `ConversationNotFound` when not);
   `build_dispatch` with `persist_pause` wired; `take_turn_and_reconcile`;
   `save`. `open_runtime` calls `config.load_dotenv()`,
   `load_or_seed_persona`, `memory_store.ensure_plugin_seeded`,
   `build_plugin_set(discover_plugins())`, `open_store`,
   `memory_store.ensure_schema`, `observability.make_recorder`. No caller yet
   and that is deliberate — CLAUDE.md says early steps may have none.
   Check: `make lint`, `make typecheck`.
2. **Write `tests/unit/test_client_surface.py` and make it green** before any
   client moves onto the door. Contents in `## Proof`. Check: `make test`.
3. **Move the channel side.** `gateway_dispatch.handle_inbound(runtime, event,
   ...)` becomes `session_key_for(event)` +
   `memory.account_key_for(event.platform, event.chat_id)` +
   `take_turn(..., create_if_missing=True)` + `(outcome.ok, outcome.answer or
   outcome.diagnostic)`. Then `scheduling.py` and `subcommands/gateway.py` in
   the same step, because `handle_inbound`'s signature change breaks them
   immediately, then the three test files' call sites. The existing channel
   tests are the regression oracle for this step: they must pass with only
   construction changed. Check: `make test`.
4. **Move the terminal.** `cmd_chat` resolves `--account` /
   `SADANA_MEMORY_ACCOUNT` / `local` and states it; `--resume KEY` passes
   `create_if_missing=False`, `--key NAME` passes `True`; the loop reads a
   line, calls `take_turn`, then renders exactly as today —
   `print(answer)` when there is one, else `print(diagnostic,
   file=sys.stderr)`, then `return 1` when `not ok`. `closing(runtime.conn)`
   keeps today's connection lifetime. Add the pause test. Check: `make test`.
5. **Self-check and verify.** `/ponytail-review` and `/simplify` over the
   diff, sort the findings into take-now / future-item / spec-already-settled,
   apply the first bucket, then `make verify` and report both.

## Risks

**What could this break that already works.** Three closed work items' test
files are edited, and that is the main exposure. `tests/unit/test_gateway_dispatch.py`
has 8 `handle_inbound(...)` call sites (GATEWAY-DAEMON-01/02);
`tests/unit/test_scheduling.py` has 5 `tick(...)` call sites plus the
`conn_lock` monkeypatch at line 131 (GATEWAY-DAEMON-02, added by a
deploy-stage cold review); `tests/unit/test_subcommands_gateway.py:82`
asserts `"plugin_set" in seen`. Mitigation: every edit is a call-site
rebuild or a renamed key, and no assertion changes what it means — the one
semantic edit, `"plugin_set"` → `"runtime"`, keeps its original intent
(the tick thread received its wiring). If any of those tests needs its
*meaning* changed to pass, that is the signal the design is wrong and this
plan is what to revisit, not the test.

Four behaviours could shift without a test noticing. (a) The webhook's reply
string: `outcome.answer or outcome.diagnostic` must reproduce
`result.final_text or f"[{exit_reason.value}] {detail or ''}"`
(`gateway_dispatch.py:179`) exactly — covered by the existing channel tests,
which is why step 3 lands before step 4. (b) The terminal's stdout/stderr
split: a `BUDGET_EXHAUSTED` turn carries a real epilogue in `final_text` and
must still go to **stdout** with exit code 1, not to stderr
(`subcommands/chat.py:93-98`); this is the specific reason `TurnOutcome` has
three fields and the reason a test for it is named in `## Proof`. (c) The
non-reentrant lock: `tick()` deliberately calls `handle_inbound` *outside*
its own `with conn_lock` blocks because nesting the same lock deadlocks
(`scheduling.py:72`); `take_turn` acquires it internally, so that call must
stay outside — `test_scheduling.py`'s `acquisitions == 2` assertion is what
catches a regression here. (d) `memory_store.ensure_schema` and
`ensure_plugin_seeded` move into `open_runtime`, so a test that hand-builds a
connection and calls `take_turn` must ensure the schema itself; the door's
docstring states that obligation, the same posture `handle_inbound` already
documents.

`config.load_dotenv()` inside `open_runtime` duplicates `cli.main`'s call. It
is idempotent and never overrides a live environment variable
(`config.py:35`), and no test asserts how often it runs.

**Which step is most risky, and why that one.** Step 3. It is the only step
that changes a signature two production callers and three test files depend
on, and it touches four production files at once; it is also where the
webhook's observable behaviour could move silently if the `answer`/
`diagnostic` fold is wrong. The ordering already answers the "can it land
later" question in both directions: it cannot come before step 2, because the
door would be unproven, and it cannot come after step 4, because
`scheduling.py` would be broken in between. Step 4 is the subtler-but-safer
one — its trap is the stdout/stderr split above, and it has 230 lines of
existing tests as an oracle.

**Is this plan drifting back toward something spec.md rejected.** Checked
each. `MessageEvent` is not widened and `gateway.py` is untouched. No client
branches on a union — `TurnOutcome` is one shape, and the two clients take
different cuts of the same three fields. No client re-reads config to
assemble the door; `open_runtime` owns the assembly, and what `chat.py` still
resolves for itself is only identity, which requirement 3 demands it state.
`scheduling.py` is not left alone. `plugin_set` and `persona` stay
process-lifetime, not rebuilt per turn. The nearest thing to a drift is
deviation 2 above, where spec.md's Concerns implied the lock would move onto
the value; that is stated out loud rather than done quietly, and it changes
no behaviour.

One risk this plan cannot mitigate, carried forward from spec.md's Concerns:
nothing in `make verify` proves requirement 8 — that a fourth client needs no
edit here. The acceptance criteria are a proxy, and the argument list is the
thing to review hardest.

## Proof

`tests/unit/test_client_surface.py`, new, covering:

- `create_if_missing=True` on an unknown name creates the conversation and
  runs the turn; `create_if_missing=False` on the same name raises
  `conversation_store.ConversationNotFound` and writes nothing.
- A `wait` node reached during a turn writes a `plugin_pauses` row, and the
  next `take_turn` for that conversation resumes it with no model call —
  built on `conftest.wait_then_summarize_installed`, the fixture
  GATEWAY-DAEMON-02 already shares between `test_gateway_dispatch.py` and
  `test_plugin_dispatch.py`.
- A `COMPLETED` turn returns `ok=True`, `answer` set, `diagnostic == ""`.
- A non-`COMPLETED` turn that still carries text returns `ok=False` with that
  text in `answer` and a populated `diagnostic` — the distinction the
  terminal's stdout depends on, asserted as a relationship between the three
  fields rather than as a snapshot of either string.
- Two threads calling `take_turn` on one runtime serialize, proven with a
  recording lock monkeypatched over `client_surface.conn_lock` and an
  acquisition count, the shape `test_scheduling.py` already established.

Regression oracles, all pre-existing and green with construction changed only:

- `tests/unit/test_gateway_dispatch.py` — all 8 tests, including
  GATEWAY-DAEMON-02's pause-persist and pause-resume pair. The channel's
  replies and its pause behaviour have not moved.
- `tests/unit/test_scheduling.py` — all tests, including
  `acquisitions == 2`, proving the door is still called outside the tick's
  own lock.
- `tests/unit/test_subcommands_chat.py` — all tests, proving the terminal's
  rendering, exit codes and `--resume`/`--key` behaviour are unchanged, plus
  one new test: a `wait` node reached in a terminal turn is persisted and the
  next line of input resumes it. That test is the intent's defect, stated as
  code.

Structural checks, run and pasted:

- `grep -rn "take_turn_and_reconcile\|build_dispatch(\|create_conversation(" src/ --include=*.py`
  shows call sites in `client_surface.py` only — `CLAUDE.md:61`'s rule.
- `grep -n "^from\|^import" src/sadana/client_surface.py` shows no `gateway`,
  `channel_webhook`, `scheduling`, `editor_server` or `subcommands` import.
- `git diff --stat` shows `conversation_store.py` untouched: no new table, no
  new column.

`make verify` ending `VERIFY OK`, pasted in full into the conversation at the
Test stage and into `review.md` under `## Evidence` at Deploy.

Deploy stage adds what the unit suite is forbidden to do: a standalone script
outside `make test` that takes one real turn through `sadana chat` and one
through the webhook against the live provider, with its output pasted as
Evidence — CLAUDE.md's rule for proving a block's first real external round
trip.
