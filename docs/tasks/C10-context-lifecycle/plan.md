# Plan: A conversation can compact its history and mark what's safe to reuse (from intent.md 2026-09-06)

## Two implementation refinements found during planning (not in spec.md verbatim)

1. **`context_state` rides on `TurnResult`, not as a fifth `run_turn` return
   value.** spec.md's Design section said "returned as a fifth value
   alongside `(result, messages, iteration_budget, system_prompt)`." Every
   real call site that invokes `run_turn` destructures that return as a
   4-tuple — 13 in `tests/unit/test_conversation.py`, 2 in
   `tests/unit/test_conversation_store.py`. Growing the tuple means editing
   all 15. `TurnResult` already is the first element of that tuple and
   already carries per-turn facts (`usage`, `model_calls`, `appended`);
   adding `context_state: ContextState` to it carries the same information
   with zero destructuring-site edits. Same information crossing the same
   boundary, smaller diff — a pure implementation-level refinement, not a
   change to what crosses between CONVERSATION and CONTEXT.
2. **`conversation_store.py` already fully persists and reconstructs a
   `Conversation`** (`load()`, `tests/unit/test_conversation_store.py:150,163,176`
   assert full-object round-trip equality) — CONV-08 built this before
   CONTEXT existed. spec.md's requirement 7 ("CONTEXT's own state does not
   persist... never written to disk") settles that `stable_prompt_len`/
   `context_state` get **no new DB columns**, but doesn't say what `load()`
   hands back for two fields the row can't supply. Resolution: `load()`
   reconstructs `context_state=ContextState()` (fresh — a resumed
   conversation's running usage total restarts at zero, an accepted gap
   the same shape as CONV-08's own already-accepted "budget durability is
   real at turn granularity" limitation) and `stable_prompt_len=len(row["system_prompt"])`
   (mark the *whole* stored prompt as the cacheable prefix on resume —
   always correct, since nothing in this item ever makes `system_prompt`
   partially volatile after creation; it just can't recover the *narrower*
   cross-conversation-reuse boundary `create_conversation()` used, since
   the originating recipe isn't stored). `tests/unit/test_conversation_store.py`'s
   `_conversation()` fixture gets the same two values
   (`stable_prompt_len=len(_SYSTEM_PROMPT)`, `context_state=ContextState()`),
   so the three existing round-trip equality assertions keep passing
   unchanged, not weakened.

## Files that change

**Corrected post-build, at deploy-stage cold review** — this section
originally named a `BeforeSendResult` wrapper and a `before_send(...,
context_window_size=...)` signature. Both changed during implementation
(see spec.md's own "Corrected twice during build" note); the review found
this file never got updated to match. Text below is now the actual shape.

- `src/sadana/context.py` (new) — `ContextState`, `CacheHint`,
  `trailing_marks_from_config()`, `before_send()`, `after_response()`,
  `after_tool_result()`, `turn_complete()`. Zero runtime imports of
  `conversation.py` or `model_access.py` — `Message` and
  `model_access.Usage` are referenced only under `TYPE_CHECKING` (avoids a
  real import cycle; neither type is constructed or `isinstance`-checked
  here, only attribute-accessed/passed through). `before_send` returns a
  bare `CacheHint`, not a wrapper — self-check found neither of the
  wrapper's other two fields had a consumer.
- `CLAUDE.md` — one new "Please do" line (provider-specific wire-format
  knowledge lives in MODEL-ACCESS), approved by the user before build per
  spec.md's own Concerns section. Omitted from this list originally;
  cold review caught the omission.
- `tests/unit/test_context.py` (new) — one test per checkpoint function,
  plus `trailing_marks_from_config()`'s config-default and override cases.
- `src/sadana/model_access.py` — add `mark_cache_boundary(messages, hint)`.
  `context.CacheHint` referenced only under `TYPE_CHECKING` (same reasoning
  — no runtime import of `context.py`, no cycle).
- `tests/unit/test_model_access.py` — tests for `mark_cache_boundary`
  against fixture dicts (system message with a stable/volatile split,
  assistant/tool/user messages, an empty-content message that must not
  get a marker) — no network, matching the unit tier's own ban.
- `src/sadana/conversation.py`:
  - `Conversation` gains `stable_prompt_len: int` and
    `context_state: ContextState` (both required, no default — every
    constructor site below is updated explicitly).
  - `TurnResult` gains `context_state: ContextState`.
  - `create_conversation()` sets `stable_prompt_len=len(recipe.stable_prompt)`,
    `context_state=ContextState()`.
  - `run_child()` drops its `compress` parameter; its inline `Conversation(...)`
    gets `stable_prompt_len=len(stable_prompt)`, `context_state=ContextState()`.
  - `complete()` gains `cache_hint: context.CacheHint | None = None`;
    when set, calls `model_access.mark_cache_boundary(request_messages, cache_hint)`
    before constructing `Request`.
  - `run_turn()` drops `compress: Callable[...]`; gains
    `context_state: ContextState` and `stable_prompt_len: int` parameters.
    Wires in, at the real call sites already in the loop:
    - `context.before_send(history=messages, stable_prompt_len=stable_prompt_len)`
      (no `context_window_size` — dropped during build, see the correction
      note above) computed fresh before each `complete()` call (MODEL_CALL
      and the EPILOGUE summary call) — matches B2's "may be called more
      than once per turn" allowance — and its returned `CacheHint` passed
      straight through as `cache_hint`.
    - `context.after_response(context_state, completion.usage)` right
      after each successful `Completion`, alongside the existing
      `usage_total = _add_usage(...)` line.
    - `context.after_tool_result(context_state, Message(role="tool", ...))`
      before the TOOL_ROUND append, replacing the plain `Message(...)`
      construction there with its (today, identical) return value.
    - `context.turn_complete(context_state, messages, system_prompt)`
      replaces `await compress(messages, system_prompt)` in the
      `except ContextOverflow` branch, 1:1.
    - Final `context_state` goes onto the returned `TurnResult`.
  - `take_turn()` drops `compress` from its own signature and its
    `run_turn(...)` call; passes `conversation.context_state`/
    `conversation.stable_prompt_len` in; folds `result.context_state` back
    via the same `replace()` call that already folds `messages`/
    `next_turn_seq`/`iteration_budget`.
- `src/sadana/conversation_store.py` — `load()` reconstructs the two new
  `Conversation` fields as described in refinement 2 above. No new columns,
  no change to `save()`/`create()`/`_conversation_row()`.
- `tests/unit/test_conversation_store.py` — `_conversation()` fixture gets
  the two new fields (matching `load()`'s own reconstruction, so the
  existing round-trip-equality tests at lines 150/163/176 keep passing
  unchanged); `_run_turn()` helper drops its inline `compress` closure,
  adds `context_state=ContextState()`, `stable_prompt_len=len(_SYSTEM_PROMPT)`.
- `tests/unit/test_conversation.py`:
  - `_run()`'s shared `kwargs` dict: replace `"compress": _fake_compress_none`
    with `"context_state": ContextState()`, `"stable_prompt_len": len(_SYSTEM_PROMPT)`
    — fixes 11 of its 13 call sites for free.
  - The two call sites that override `compress=` explicitly
    (`test_run_turn_context_overflow_retries_with_compressed_prompt`,
    `test_take_turn_compression_rotates_prompt_and_stays_self_consistent`)
    get rewritten to `monkeypatch.setattr(context, "turn_complete", ...)`
    instead — same scenario (a shorter prompt comes back, the loop retries
    with it), same assertions, exercised through the new real seam instead
    of the removed injected one. This is a mechanical consequence of
    removing the `compress` parameter these two tests happen to use, not a
    test that was wrong — CLAUDE.md's "fix the test only if it's wrong"
    rule doesn't apply; the seam it was written against no longer exists,
    by this item's own design.
  - New tests: usage accumulates in `Conversation.context_state` across
    multiple `take_turn()` calls and starts at zero for a fresh one;
    `complete()`'s built `Request.messages` carry `cache_control` on the
    system message's stable-prefix content part and on the latest message
    when a `cache_hint` is supplied (captured via the existing
    `monkeypatch.setattr(model_access, "send", ...)` pattern already used
    throughout this file — inspect the `request` argument the fake `send`
    receives); `after_tool_result`/`turn_complete` no-op behavior is
    unchanged (a `ContextOverflow` still exits `CONTEXT_OVERFLOW_UNHANDLED`
    when `context.turn_complete` isn't monkeypatched to return something
    real).
- `src/sadana/eval_harness.py` — drop `_compress_noop`, drop `compress=`
  from its `take_turn()` call.
- `scripts/prove_conversation_e2e.py` — drop `_compress`, drop every
  `compress=` kwarg (6 call sites). Also gains a small, permanent
  instrumentation addition (a `model_access.send` wrap capturing the real
  request for turn 1) to prove this item's own cache-marker acceptance
  criterion against a live request body — added post-build, at Test
  stage, once it became clear the script's pre-existing assertions
  (written for CONV-09, before CONTEXT existed) never checked this.
- `scripts/eval/tasks/plugin_dispatch.py` (EVAL-02's own real task) —
  drop `_compress_noop`, drop `compress=` from its `run_child()` call.
  **Missed in the original build pass**, caught by deploy-stage cold
  review: this file calls `run_child()` directly rather than through
  `eval_harness.run_task()`, so the original caller-migration search
  (scoped to `eval_harness.py`/`prove_conversation_e2e.py`/the two test
  files) never found it. Fixed and re-verified with a real run
  (`exit_reason=COMPLETED score=1.0`) before this review's Decision.

## Order of work

1. `src/sadana/context.py` + `tests/unit/test_context.py`. Fully
   standalone — no dependency on `conversation.py`/`model_access.py` at
   runtime. `make test` on this one file proves it in isolation before
   anything else changes.
2. `src/sadana/model_access.py`'s `mark_cache_boundary` +
   `tests/unit/test_model_access.py`. Depends only on `context.CacheHint`
   (`TYPE_CHECKING`-only import) — standalone, fixture-dict tests, no
   network. Proves the wire-marker shape correct before it's wired into
   the loop.
3. Wire both into `conversation.py` (`Conversation`, `TurnResult`,
   `create_conversation`, `run_child`, `complete`, `run_turn`, `take_turn`)
   — the risky step, see Risks. Update `conversation_store.py`'s `load()`
   in the same step, since it constructs a `Conversation` too and would
   otherwise fail to type-check/construct.
4. Migrate every real caller off `compress`: both test files,
   `eval_harness.py`, `prove_conversation_e2e.py`.
5. Self-check (`/ponytail-review` + `/simplify`) against the full diff,
   apply what's worth taking now per build-skill's own triage.
6. `make verify`.

## Risks

**What could this change break?** Every real caller of `run_turn`/
`take_turn`/`run_child`: `eval_harness.py` (EVAL-01/EVAL-02's own real,
keep-forever machinery) and `prove_conversation_e2e.py` (CONV-09's deploy
evidence). Both are migrated in step 4 and covered by `make verify`, but
neither is re-run against a live model in this build stage — that's the
Test stage's job (a live `prove_conversation_e2e.py` run becomes Deploy
Evidence, matching CLAUDE.md's own convention), not build-stage proof.
Also at risk: `conversation_store.py`'s three round-trip equality tests
(refinement 2 above) — addressed by matching `_conversation()`'s new
field values to `load()`'s own reconstruction, not by loosening the
assertions. CONV-10's `next_child_seq` propagation fix is untouched by
this item (only `run_child`'s dropped `compress` param and two added
`Conversation` fields are new there) — worth a quick re-read of that
diff before touching `run_child`, to avoid brushing against it by
accident.

**Which step is riskiest?** Step 3. It's the only step touching the
MODEL_CALL/TOOL_ROUND/EPILOGUE loop logic itself, and it's ordered after
steps 1-2 prove `context.py` and `mark_cache_boundary` correct in
isolation — step 3 only has to wire already-correct pieces together, not
debug new logic and integration simultaneously. Within step 3, the
sub-risk named above (import direction) is resolved by construction:
`context.py` and `model_access.py` both stay dependency-free of
`conversation.py`/each other at runtime (`TYPE_CHECKING`-only
cross-references), so `conversation.py` is the only module gaining a real
new import (`from sadana import context`), and no cycle is possible.

**Which options did spec.md reject, and is this plan drifting back?**
Checked against spec.md's `## Rejected alternatives` one by one: not
keeping `compress` as an injected callable (removed, not repointed — step
3/4); not storing the literal `stable_prompt` string (only its length,
everywhere); not putting `mark_cache_boundary` in `context.py` (it's in
`model_access.py`, step 2); not building a pluggable cache-marking
strategy (`before_send`'s only real decision is one config int,
`trailing_marks_from_config()`); not deriving usage from message history
(a stored `ContextState`, folded via `after_response`); not porting any
part of `prompt_cache_scope.py` (nothing in this plan touches conversation
identity/`Conversation.key` at all). No drift found.

## Proof

- `make test` after step 1: `test_context.py` green, in isolation.
- `make test` after step 2: `test_model_access.py` green, including the
  no-marker-on-empty-content case.
- `make verify` after steps 3-4: full suite green, specifically —
  `test_conversation.py`'s new usage-accumulation and
  `cache_control`-presence assertions pass; the two rewritten
  monkeypatch-based compression tests still pass; `test_conversation_store.py`'s
  three round-trip equality assertions (lines 150/163/176) still pass
  unchanged.
- Self-check output (`/ponytail-review` + `/simplify`) reported verbatim
  when work is reported, with each finding sorted into taken-now /
  follow-up-item / already-settled-by-spec, per build-skill's own triage.
- **Not build-stage proof, named here so it isn't mistaken for missing
  evidence**: a live `prove_conversation_e2e.py` run against OpenRouter,
  inspected for a real `cache_control` marker in the actual request body —
  that's the Test stage's evidence, pasted into `review.md` next, not
  produced by this stage.
