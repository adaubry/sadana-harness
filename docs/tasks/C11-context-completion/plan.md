# Plan: A conversation survives running out of room, and a bulky tool result stops crowding it out (from intent.md 2026-09-06)

## One real gap found during planning, not in spec.md

**Compaction and real persistence don't coexist safely today, and
nothing in this item fixes that.** `conversation_store.py`'s
`_insert_messages` is `INSERT OR IGNORE` keyed on `(conversation_key,
msg_seq)`, on the explicit invariant that "a message row is never edited
once written — history only grows" (its own docstring). Real compaction
directly violates that: it renumbers the tail to start after one new
summary message, so persisting a compacted conversation would try to
insert rows under seq numbers a stale, pre-compaction row already
occupies at — and `INSERT OR IGNORE` would silently keep the old,
stale row instead. **Checked whether this is live risk**: grepped the
whole repo — nothing outside `tests/unit/test_conversation_store.py`
calls `conversation_store.save()`/`create()`/`bind_persist()` at all;
no eval task, no proof script persists a conversation today. So this is
a real, latent bug in already-closed CONV-08 code, currently inert.
Named here and added to spec.md's Open questions rather than expanding
this item's scope to fix it — solving it properly means deciding how a
compaction event should be represented durably (a new "segment," a
rewritten row set, something else), which is its own design question,
not a corollary of this one. **Trigger for revisiting, matching this
project's own "two occurrences" bar elsewhere**: the moment anything
real calls `conversation_store.save()`/`bind_persist()` on a conversation
that also compacts.

`conversation_store.py` itself is untouched by this plan — `Message`'s
new `is_summary` field is not added to the DB schema either, so a
persisted-and-reloaded summary message silently loses its marking today.
Same reasoning: no real caller persists anything yet, so this can't bite
in practice, but it's the same class of gap and worth naming once
instead of twice.

## Files that change

**Corrected post-build, at deploy-stage cold review**: this list originally
omitted `CLAUDE.md` (two new "Please do" lines: the `conversation.compact`
naming extension and the `resolve()` retry-loop rule, both approved before
build) and `scripts/prove_context_completion.py` (new — Test-stage evidence
script; see review.md's own Evidence). Also, `resolve()` was corrected
after cold review caught that running the whole retry loop inside one
`asyncio.to_thread` call (rather than one call per attempt) would make a
mid-turn cancellation wait out every remaining attempt before it could be
delivered — `resolve()` is `async def` and awaits `asyncio.to_thread` once
per attempt, not once around the whole loop; both callers (`complete()`,
`context.turn_complete()`) now `await model_access.resolve(request)`
directly. And `result_spill.write_and_reference()`'s path computation
(including the directory-creating `mkdir`) was moved inside its own `try`
block — cold review caught it could otherwise raise past the function's
own "never raises" contract.

- `src/sadana/model_access.py`:
  - **Bug fix** (C10's own accepted-not-fixed Decision): the
    `# ponytail:` comment in `mark_cache_boundary` goes away — locate the
    system message by `role == "system"` via a scan (mirroring
    `_eligible_trailing_indexes`'s own backward-scan style) instead of
    assuming index 0. `_eligible_trailing_indexes` excludes whatever
    index that turns out to be, rather than hardcoding `range(1, ...)`.
  - New: `async def resolve(request: Request) -> Response | NeedsCredentialOrProviderChange
    | NeedsContextCompression | Degenerate | Abort` — loops while `send()`
    returns `Retry`, awaiting `asyncio.to_thread(send, ...)` once per
    attempt (not once around the whole loop — see correction note above),
    returns the first non-`Retry` outcome. Needs
    `from dataclasses import replace` added to its existing
    `from dataclasses import dataclass, field` import, and a new
    `import asyncio`.
- `tests/unit/test_model_access.py` — tests for `resolve()` (retries
  transparently, returns each non-`Retry` outcome type unchanged) and for
  the index-0 fix (a system message NOT at index 0 still gets its
  stable-prefix marker; a non-system message at index 0 is still
  correctly eligible for a trailing mark).
- `src/sadana/result_spill.py` (new — touches real disk I/O, its own file
  per CLAUDE.md's rule): `write_and_reference(tool_call_id: str, content:
  str) -> str`. Writes under
  `config.get_paths().state_dir / "tool_results" / <sanitized id>.txt`;
  returns a reference note (path, char count, ~1500-char preview,
  explicit "no tool exists yet to re-read this" line). Never raises —
  the whole path computation (including the directory-creating `mkdir`,
  not just the write) runs inside one `try`/`except OSError` that returns
  `content` unchanged (see correction note above — the `mkdir` originally
  sat outside the guarded region).
- `tests/unit/test_result_spill.py` (new) — writes under the existing
  `tmp_path`-redirected state dir fixture (never the real one); asserts
  the reference note's shape; asserts a write failure (simulated via a
  state dir made read-only, or a monkeypatched `Path.write_text` raising
  `OSError`) falls back to returning the original content unchanged;
  asserts the filename-sanitization handles a `tool_call_id` with
  path-unsafe characters.
- `src/sadana/conversation.py`:
  - `Message` gains `is_summary: bool = False`.
  - New `find_compaction_boundary(messages: tuple[Message, ...],
    keep_tail_count: int) -> int` — largest safe cut index leaving at
    least `keep_tail_count` messages in the tail when available, walked
    backward past any leading `tool`-role rows so the cut never orphans
    one (same idiom `pending_tool_call_ids`/`repair` already use). Pure,
    no I/O.
  - New `compact(messages: tuple[Message, ...], tail_start: int,
    summary_text: str) -> tuple[Message, ...]` — the one new sanctioned
    way to replace history:
    `(Message(role="user", content=summary_text, is_summary=True),) +
    messages[tail_start:]`. Trusts `tail_start` came from
    `find_compaction_boundary` — does not re-validate it.
  - `complete()` refactored: replace its own `while True` / `Retry`
    handling with one call to `model_access.resolve()`, then translate
    the returned outcome to `ContextOverflow`/`ProviderFailure`/`Completion`
    exactly as today. Behavior-preserving refactor — proof is its own
    existing tests passing unchanged.
  - `run_turn`'s `except ContextOverflow` branch rewritten:
    ```
    except ContextOverflow as e:
        model_calls += 1
        tail_start = find_compaction_boundary(
            messages, context.compaction_tail_messages_from_config()
        )
        turn_complete_result = await context.turn_complete(
            context_state, messages, system_prompt, tail_start, provider, model
        )
        context_state = turn_complete_result.context_state
        if turn_complete_result.new_summary_text is None and turn_complete_result.new_system_prompt is None:
            exit_reason = ExitReason.CONTEXT_OVERFLOW_UNHANDLED
            detail = str(e)
            break
        if turn_complete_result.new_summary_text is not None:
            messages = compact(messages, tail_start, turn_complete_result.new_summary_text)
            turn_start_seq = min(turn_start_seq, len(messages))
        if turn_complete_result.new_system_prompt is not None:
            system_prompt = turn_complete_result.new_system_prompt
        continue
    ```
  - TOOL_ROUND reordered: `context.after_tool_result` (now `await`ed)
    runs on the raw `Message(role="tool", tool_call_id=tc["id"],
    content=result_text)` *before* `_cap_tool_result`, not after.
    `_cap_tool_result` then runs on whatever `after_tool_result` decided
    to leave in context (a reference note, or the original content under
    the spill threshold) as the final safety-net cap it already was.
- `tests/unit/test_conversation.py`:
  - `find_compaction_boundary`/`compact` unit tests, including a fixture
    history built specifically to try to strand a `tool` row at the
    proposed cut.
  - `complete()`'s existing tests (`test_complete_retries_transparently`
    etc.) re-run unchanged — the proof the `resolve()` refactor didn't
    change behavior.
  - New `run_turn`-level test: a `NeedsContextCompression` outcome
    followed by a mocked summarization response followed by a normal
    completion — asserts the turn recovers (`COMPLETED`, not
    `CONTEXT_OVERFLOW_UNHANDLED`), the resulting messages contain exactly
    one `is_summary=True` message, and `model_calls` reflects all three
    model calls.
  - New test: a second compaction event, with the first message of the
    portion being summarized already `is_summary=True` — asserts the
    request sent for that second summarization call is built differently
    (the "refine" framing) than the first.
  - New test: the TOOL_ROUND reordering — a dispatch result over the
    spill threshold but under `SADANA_CONVERSATION_TOOL_RESULT_CHARS`
    ends up in the appended tool message as a reference note, not
    truncated raw text; a result over *neither* threshold is unchanged;
    a result over *only* the truncation cap (spilling disabled via a
    very high threshold) is still truncated exactly as before — proving
    the reordering doesn't change today's behavior for the case spilling
    doesn't touch.
  - New test: `TurnResult.appended`'s clamp — a fixture where the turn
    both overflows and compacts, asserting `appended`'s range stays valid
    (no `ValueError`, start `<=` stop) rather than asserting a specific
    value (the doc's own accepted approximation).
- `src/sadana/context.py`:
  - `TurnCompleteResult` (frozen dataclass): `context_state: ContextState`,
    `new_system_prompt: str | None = None`, `new_summary_text: str | None
    = None`.
  - `compaction_tail_messages_from_config() -> int` (`SADANA_CONTEXT_COMPACTION_TAIL_MESSAGES`,
    default 10) and `result_spill_threshold_from_config() -> int`
    (`SADANA_CONTEXT_RESULT_SPILL_CHARS`, default 100,000, matching
    `SADANA_CONVERSATION_TOOL_RESULT_CHARS`'s own default).
  - `turn_complete` becomes `async def turn_complete(state, history,
    system_prompt, tail_start, provider, model) -> TurnCompleteResult`.
    Builds a prompt over `history[:tail_start]` — two framings: refining
    (when `history[0].is_summary`) vs. fresh, both concrete text below.
    Resolves via `model_access.resolve()` (real runtime import — first
    real dependency `context.py` has ever had on `model_access.py`,
    `asyncio.to_thread`-wrapped matching `complete()`'s own precedent);
    `tools=()`. Returns `new_summary_text=None` on any non-`Response` or
    empty-content outcome (a terminal failure leaves the conversation
    unhandled, matching today's shape — no static fallback, per spec.md's
    own Rejected alternatives).
  - `after_tool_result` becomes `async def after_tool_result(state,
    result: Message) -> Message`. Identity at or under
    `result_spill_threshold_from_config()`; otherwise calls
    `result_spill.write_and_reference` via `asyncio.to_thread` and
    returns `dataclasses.replace(result, content=<reference>)`.
  - New real imports: `model_access`, `result_spill`, `asyncio` (all safe
    — neither has any dependency back on `context.py` or
    `conversation.py`).
- `tests/unit/test_context.py`:
  - `test_after_tool_result_is_identity`/`test_turn_complete_is_a_real_no_op`
    (C10's own) updated to `asyncio.run(...)`-wrap the now-async calls.
  - New tests for both config accessors (default + override, matching
    `trailing_marks_from_config`'s own test pattern).
  - New `turn_complete` tests: fresh summarization (mocked
    `model_access.send`, asserts the returned `new_summary_text` and that
    `new_system_prompt` stays `None`); the refining-vs-fresh prompt
    framing (asserts on the built request's content, given a fixture
    `history` whose first message is `is_summary=True` vs. not); failure
    (mocked `send` returns `Abort`/`Degenerate`/empty content) returns
    both fields `None`.
  - New `after_tool_result` tests: under threshold is identity (no call
    to `result_spill`); over threshold calls
    `result_spill.write_and_reference` (mocked) and returns its result as
    the new content.
- `CLAUDE.md` — two new "Please do" lines, approved before build:
  `conversation.append`/`conversation.repair`/`conversation.compact` (the
  rule's named set extended for the one new sanctioned history-replacing
  operation), and the `resolve()` retry-loop rule.
- `scripts/prove_context_completion.py` (new) — Test-stage evidence
  script: a simulated `ContextOverflow` trigger recovers via a real
  second model call and a real retry; a second simulated overflow
  refines the existing summary instead of resummarizing it (verified
  against the real prompt sent, not just the resulting text); a real
  oversized tool result spills to a real file, verified byte-for-byte.
  Hybrid, not fully organic — see review.md's own Evidence section for
  why a genuine provider-side overflow wasn't affordably reproducible.

## Order of work

1. `model_access.py`'s two independent pieces first — the index-0 fix
   and `resolve()` — plus their tests. Both are self-contained, zero
   interaction with anything else in this plan. `make test` green on
   just this file proves them before anything depends on either.
2. `result_spill.py` + its tests. Zero dependency on `conversation.py`/
   `context.py` — only `config`. Proves the one new I/O module correct
   in isolation.
3. `conversation.py`'s pure additions: `Message.is_summary`,
   `find_compaction_boundary`, `compact`. Testable without touching
   `run_turn` at all.
4. Refactor `complete()` to use `model_access.resolve()`. Its own
   existing tests are the proof — run them immediately after this one
   change, before touching anything else, so a behavior change here is
   caught in isolation rather than blamed on a later step.
5. `context.py`: `TurnCompleteResult`, both config accessors, real
   `turn_complete`, real `after_tool_result` — now every piece they
   depend on (steps 1-4) already exists and is proven.
6. Wire it all into `run_turn`: the rewritten `ContextOverflow` branch,
   the TOOL_ROUND reorder, the `appended` clamp. The riskiest step,
   ordered last on purpose — see Risks.
7. Self-check (`/ponytail-review` + `/simplify`), triage, apply what's
   worth taking now.
8. `make verify`.

## Risks

**What could this change break?** `complete()`'s refactor (step 4) is the
one change to already-shipped, already-tested behavior with no new
feature riding on it — its own test suite passing unchanged is the whole
proof it's safe, checked before anything else builds on top of it. The
TOOL_ROUND reorder (step 6) touches every existing tool-result test in
`test_conversation.py` that asserts on truncation
(`test_run_turn_caps_oversized_tool_result`,
`test_run_turn_caps_per_turn_budget_independently`) — these must keep
passing exactly as today when the spill threshold is left at its high
default (nothing to spill, so `after_tool_result` stays identity and
`_cap_tool_result` behaves unchanged). The compaction+persistence gap
(above) is real but proven inert — not a risk to this item's own
correctness, only a documented limitation.

**Which step is riskiest?** Step 6. It's the only step touching
`run_turn`'s actual control flow, and it's ordered after every piece it
calls (`find_compaction_boundary`, `compact`, `context.turn_complete`,
`context.after_tool_result`) is already independently correct — step 6
only has to wire already-proven pieces together, not debug new logic and
integration at the same time. Within step 6, the sub-risk is the
`turn_start_seq` clamp for `TurnResult.appended` — checked that exactly
one existing test reads `.appended`
(`tests/unit/test_conversation.py:634`) and it doesn't exercise
compaction, so the clamp can't silently break it.

**Which options did spec.md reject, and is this plan drifting back?**
Checked spec.md's own `## Rejected alternatives` against this plan:
not collapsing `turn_complete`'s return to history-only (kept both
fields, independently optional); not deleting `rotate_prompt()`/
`PromptRotationReason` (untouched — still callable, just never called by
this item's own real behavior); not porting a static non-LLM fallback
summary (a failed summarization call returns `None`, full stop); not
having `context.turn_complete` construct a `Message` itself (it returns a
plain string; `conversation.py`'s own `compact()` does the construction);
not content-sniffing for a summary marker (a real `Message.is_summary`
field); not duplicating `complete()`'s retry loop (extracted to
`model_access.resolve()`, shared). No drift found.

## Proof

- `make test` after step 1: `test_model_access.py` green, in isolation
  (both the index-0 fix and `resolve()`).
- `make test` after step 2: `test_result_spill.py` green, in isolation.
- `make test` after step 3: new pure-function tests
  (`find_compaction_boundary`/`compact`) green.
- `make test` after step 4: `test_conversation.py`'s existing `complete()`
  tests pass unchanged — the specific proof the refactor is
  behavior-preserving, not just similarly-shaped.
- `make test` after step 5: `test_context.py` green, including the new
  `turn_complete`/`after_tool_result` real-behavior tests.
- `make verify` after step 6: full suite green, specifically — the new
  `run_turn`-level compaction-recovery test, the refine-vs-fresh prompt
  test, the TOOL_ROUND-reorder tests (spill takes priority over
  truncation; truncation still fires when spilling doesn't apply), the
  `appended`-clamp test, and every pre-existing tool-result/truncation
  test still passing at their old defaults.
- Self-check output reported verbatim when work is reported, findings
  sorted taken-now / follow-up-item / already-settled-by-spec.
- **Not build-stage proof, named here so it isn't mistaken for missing
  evidence**: a live round trip proving a real `ContextOverflow` recovers
  via a real second model call, a real refined second summary, and a
  real oversized tool result spilling to a real file — that's the Test
  stage's evidence next, not produced by this stage. Real spend, same
  posture as every other live round trip this project has run.
