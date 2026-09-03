# Plan: One turn of a conversation actually runs, start to finish (from intent.md 2026-09-03)

## Context

Build stage for **C7-turn-loop**, CONV-05 from
`docs/reference/conversation_block_blueprint.md` §7 — the largest work
item in the project, tying together C2 (transcript), C3 (tool surface),
C4 (budgets), and C6 (provider port) into the actual turn loop.
`intent.md` and `spec.md` are already written and checked; `spec.md` is
unusually thorough (full step-by-step algorithm, every type shape, every
signature, an explicit Concerns section flagging its own risk areas), so
this plan skips Explore/Plan subagents — they would only restate it.

The problem this closes: every piece needed to run a conversation exists
in isolation. This work item is the first thing that actually runs one —
asks the model, carries out whatever it asked for, asks again if needed,
and always ends in one of eight named ways, each provably reachable.

## Files that change

- `src/sadana/conversation.py` — same file C2/C3/C4/C6 all extended. New
  additions: `PromptDriftError`, `ExitReason` (Enum), `TurnKey`,
  `TurnResult`, `turn_prompt_hash()`, `_deduplicate_tool_calls()`,
  `_noop_persist()`, `run_turn()`. New imports: `enum.Enum`,
  `collections.abc.Awaitable` (added to the existing `collections.abc`
  import line).
- `tests/unit/test_conversation.py` — same file all four prior work items
  extended. The largest test addition yet: one test per acceptance
  criterion, all eight `ExitReason`s each reached through `run_turn`
  itself.

## Design recap (from spec.md, restated so this plan stands alone)

```python
async def run_turn(
    *,
    conversation: ConversationKey,
    turn_seq: int,
    messages: tuple[Message, ...],
    user_input: str,
    system_prompt: str,
    prompt_sha256: str,
    tool_surface: ToolSurface,
    iteration_budget: IterationBudget,
    wall_clock_budget: WallClockBudget | None,
    now: float,
    provider: str,
    model: str,
    dispatch: Callable[[str, dict], Awaitable[str]],
    compress: Callable[[tuple[Message, ...], str], Awaitable[str | None]],
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
) -> tuple[TurnResult, tuple[Message, ...], IterationBudget, str]:
```

Full step-by-step algorithm is in `spec.md`'s Design section:

1. **REPAIR**: `messages = repair(messages)`.
2. **PROLOGUE**: assert `turn_prompt_hash(system_prompt, tool_surface) ==
   prompt_sha256` (raise `PromptDriftError` on mismatch — never an
   `ExitReason`); append the user message; snapshot
   `turn_start_seq = len(messages) - 1`.
3. **MODEL_CALL loop**: `consume_iteration` (→ `BUDGET_EXHAUSTED` on
   `None`) → wall-clock check (→ `WALL_CLOCK_EXHAUSTED`) → `complete()`
   (C6) → `ContextOverflow` routes to `compress()` (→
   `CONTEXT_OVERFLOW_UNHANDLED` on `None`, else retry `MODEL_CALL` with
   the new prompt) → `ProviderFailure` → `PROVIDER_FAILED` → text-only
   response → `COMPLETED` → `tool_calls` present → TOOL_ROUND.
4. **TOOL_ROUND**: dedupe → partition valid/invalid → all-invalid batch →
   `INVALID_TOOL_CALLS` → append assistant row → `persist()` (→
   `PERSISTENCE_FAILED` on raise) → append invalid-call error results →
   for each valid call, `dispatch()` wrapped in `try/except` → normalize
   → two-tier char cap (`SADANA_CONVERSATION_TOOL_RESULT_CHARS`, then
   `SADANA_CONVERSATION_TOOL_TURN_BUDGET_CHARS`) → append as it lands →
   loop back to MODEL_CALL.
5. **EPILOGUE**: build `TurnResult`; on `BUDGET_EXHAUSTED` with no
   `final_text`, one tool-less `complete()` call for a summary (the named
   exception).

`asyncio.CancelledError` caught once, at the outermost level of the
MODEL_CALL/TOOL_ROUND body, becomes `INTERRUPTED` rather than propagating
— the one place this design catches a `BaseException` subclass, flagged
in `spec.md`'s own Concerns for review attention.

## Order of work

Landed in dependency order — each step provable before the next depends
on it, riskiest logic (the loop's own control flow) proven incrementally
rather than all at once:

1. **Pure helpers first, independently testable, no `model_access`
   dependency**: `PromptDriftError`, `ExitReason`, `TurnKey`,
   `TurnResult`, `turn_prompt_hash()`, `_deduplicate_tool_calls()`,
   `_noop_persist()`. Each has its own acceptance criteria that don't
   need `run_turn` to exist.
2. **`run_turn`'s REPAIR/PROLOGUE/MODEL_CALL skeleton, text-only path
   only** — no TOOL_ROUND yet. Proves `COMPLETED`, `PROVIDER_FAILED`,
   `CONTEXT_OVERFLOW_UNHANDLED` (both branches), `BUDGET_EXHAUSTED`
   (including the one-more-call summary), `WALL_CLOCK_EXHAUSTED`,
   `PromptDriftError` — six of eight exit paths — before TOOL_ROUND's own
   complexity is added on top.
3. **TOOL_ROUND**, added to the loop body — dedupe, partition,
   persist-before-execute, dispatch-wrapping, two-tier capping. Proves
   `INVALID_TOOL_CALLS`, `PERSISTENCE_FAILED`, and the normal
   tool-call-then-continue path.
4. **`INTERRUPTED`** — the `asyncio.CancelledError` catch, added last
   since it wraps the now-complete loop body rather than needing its own
   new branch inside it.
5. **Tests throughout steps 1-4**, landing with the code they check
   (not batched at the end) — `make test` scoped to the file after each
   step.
6. **`make verify`**, pasted as this stage's evidence.

## Risks

**What could this change break?** Nothing existing — every new name is
an addition, no existing function in `conversation.py` is touched.
`model_access.py`, every provider, and `config.py` stay untouched (same
as C4 and C6's own risk sections). The one new cross-cutting concern:
`run_turn` is the first function in this file whose control flow is
genuinely stateful across many steps (a `while` loop with multiple exit
points) rather than a single pass — worth extra care that every exit
path actually builds a complete `TurnResult` and doesn't fall through
with a missing field.

**Which step is riskiest?** Step 4, the `CancelledError` catch — per
`spec.md`'s own Concerns, incorrectly swallowing a cancellation (e.g.
catching it inside the `while` loop and looping again instead of
unwinding) is a well-known async footgun that would look correct in a
quick read and wrong under real concurrent cancellation. Mitigation: the
catch wraps the *entire* MODEL_CALL/TOOL_ROUND body from outside the
`while` loop, not a `try` inside each iteration, and the acceptance test
cancels a real `asyncio.Task`, not a mocked flag — proving the actual
`CancelledError` propagation path, not a simulated one.

**Is this drifting back toward anything `spec.md` already rejected?**
Checked against all four Rejected Alternatives: `dispatch`'s result is
wrapped defensively inside TOOL_ROUND, not trusted as pre-cleaned; no
class/Protocol bundles `dispatch`/`compress`/`persist` (three separate
keyword parameters); no `timeouts.*` config keys or enforcement anywhere;
`TurnResult` has no separate `conversation_key` field alongside
`turn_key`.

## Proof

`tests/unit/test_conversation.py` covers, at minimum, one test per
`spec.md` acceptance-criteria checkbox — the full list is in `spec.md`'s
own Acceptance criteria section; restated here as the checklist this
stage discharges:

- All eight `ExitReason`s each reached through `run_turn` itself.
- `COMPLETED` final_text matches a text-only `Completion`.
- `BUDGET_EXHAUSTED` with the one-more-call summary path producing a
  non-`None` `final_text`, `exit_reason` unchanged.
- `WALL_CLOCK_EXHAUSTED` from pure `now`/`wall_clock_budget` arithmetic,
  no sleep, no real clock.
- `PERSISTENCE_FAILED` from a raising `persist` fake, provably before any
  `dispatch` call for that round.
- `PROVIDER_FAILED` via `monkeypatch.setattr(model_access, "send", ...)`
  returning `Abort`.
- `CONTEXT_OVERFLOW_UNHANDLED` (compress returns `None`) and the retry
  path (compress returns a new prompt, a second `complete()` call uses
  it) as two separate tests.
- `INTERRUPTED` from cancelling a real `asyncio.Task`.
- `INVALID_TOOL_CALLS` for an all-invalid batch; a mixed batch in the
  same suite proves it does *not* fire.
- `turn_prompt_hash` changes when the tool surface changes even with an
  unchanged `system_prompt`.
- `PromptDriftError` on a mismatch, not any `ExitReason`.
- `_deduplicate_tool_calls` drops an exact duplicate, keeps the first
  id, leaves a no-duplicate batch unchanged.
- Per-result cap and per-turn cap each independently provable (one test
  for a single oversized result, one for two results individually under
  the per-result cap but over the per-turn cap together).
- A raising `dispatch` produces a `tool_error:`-prefixed result and the
  turn continues to the next call in the batch.
- Every test uses fakes/monkeypatches only — no real provider, network,
  EXECUTION, CONTEXT, or durable store.

`make verify` run at the end, pasted in full, ending `VERIFY OK`, is this
stage's evidence.
