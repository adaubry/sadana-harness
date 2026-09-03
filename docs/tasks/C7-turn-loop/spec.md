# Spec: One turn of a conversation actually runs, start to finish

Intent: docs/tasks/C7-turn-loop/intent.md

## Requirements

1. One function runs a full turn: it asks the model, carries out whatever
   actions the model asked for, asks again if needed, and always ends in
   one of eight named ways. Traces to intent's Proposed outcome.
2. Every one of the eight ways a turn can end is provable to actually
   happen, not just theoretically reachable. Traces to intent's Proposed
   outcome ("provable to actually happen, not just listed as a
   possibility").
3. The three parts of this project that don't exist yet — carrying out an
   action, shrinking an overly long conversation, saving durably — each
   get exactly one place to plug in later, and that place does nothing at
   backbone. Traces to intent's Constraints and "Changed during
   planning."
4. Nothing calls the model, carries out an action, or retries anything
   this work item's own design doesn't explicitly call for. Traces to
   intent's Constraints.
5. Nothing here decides what happens with more than one conversation or
   turn running at once. Traces to intent's Constraints.
6. Every promise this project has already made about not trusting a
   model's raw output — a real tool-call id, already-parsed arguments, no
   silently-wrong-shaped data — holds here too. Traces to intent's
   Constraints ("does not get relaxed here").

## Design

**Where this lives.** `src/sadana/conversation.py` — the fifth work item
in a row to extend it (C2 transcript, C3 tool surface, C4 budgets, C6
provider port). No new file: this work item is the point every one of
those four is finally used together, and splitting the loop that ties
them together into its own module would separate it from the exact
things it depends on for no benefit.

**Learning from the reference (guideline 1).** Read hermes source
directly for the two mechanisms blueprint names for this work item.

- `run_agent.py:5103` `_deduplicate_tool_calls`: dedupes by
  `(name, canonicalized-arguments)`, keeping the first occurrence,
  canonicalizing via `json.dumps(json.loads(raw), sort_keys=True,
  separators=(",",":"))` inside a `try/except` — hermes needs the
  `try/except` because its `tool_calls` arrive with unparsed argument
  *strings*. This project's own `tool_calls` never do: C6's `complete()`
  already parses every call's arguments into a dict before this work item
  ever sees one. The adapted version drops the `try`/`except` and the
  `json.loads` — `json.dumps(tc["arguments"], sort_keys=True,
  separators=(",", ":"))` is already guaranteed to succeed. This is a
  genuine simplification C6's own earlier work makes possible, not a
  dropped feature — recorded here so it reads as deliberate.
- `agent/chat_completion_helpers.py:2999` `handle_max_iterations`: on
  budget exhaustion with no final text yet, append a synthetic user
  message asking for a summary, then make exactly one more model call
  with no tools attached, and use its text as the turn's final answer.
  Adopted as blueprint §6's EPILOGUE step describes it — the one named
  exception to "no model call outside `MODEL_CALL`."

**`run_turn` — a single free async function, not a class.** Same shape
`complete()` (C6) already established: every dependency is an explicit
parameter, nothing is bound to an instance.

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

Returns four things, kept separate rather than folded into one struct:
`TurnResult` (what happened — a report, blueprint §4.6's shape), the
updated message history (what the next turn needs as its own
`messages`), the updated `IterationBudget` (consumed during this turn —
the next turn needs it to keep counting from where this one left off),
and the current `system_prompt` (unchanged unless `compress` rotated it
mid-turn — the next turn's caller needs to know if its own copy is now
stale). These four have different lifetimes and different callers within
whatever eventually holds a running conversation; conflating them into
one dataclass would make a caller that only wants the `TurnResult` for
logging carry the other three anyway.

**Everything a turn can do inside a step-cost budget, none of it
implicit.** REPAIR, PROLOGUE, MODEL_CALL, TOOL_ROUND, EPILOGUE, in the
order and shape blueprint §6 draws:

1. **REPAIR.** `messages = repair(messages)` — C2's own function,
   already a no-op on a clean transcript. This is, per blueprint's own
   diagram, the *only* mutation allowed before PROLOGUE.
2. **PROLOGUE.** Assert the byte-stability contract (below), raising
   `PromptDriftError` on mismatch — never an `ExitReason`, per blueprint
   §6's explicit instruction. Append the user's `Message` via C2's
   `append()`. Snapshot `turn_start_seq = len(messages) - 1` (the index
   of the just-appended user row) for the eventual `TurnResult.appended`
   range.
3. **MODEL_CALL loop**, repeated until something ends the turn:
   - `consume_iteration(iteration_budget)` — `None` means
     `BUDGET_EXHAUSTED`.
   - if `wall_clock_budget` is set, `wall_clock_remaining(wall_clock_budget,
     now) <= 0` means `WALL_CLOCK_EXHAUSTED`. `now` is a parameter, never
     `time.monotonic()` — see Interface and the testing-conventions
     discussion below.
   - call `complete(system=system_prompt, messages=messages, tools=tool_surface,
     provider=provider, model=model)` (C6). `ContextOverflow` calls
     `compress(messages, system_prompt)`; `None` means
     `CONTEXT_OVERFLOW_UNHANDLED`, a new `system_prompt` means retry
     `MODEL_CALL` with it (no re-check of the byte-stability hash — that
     assertion is PROLOGUE's, once per turn, per blueprint's own diagram,
     which draws the compression arrow straight back to `MODEL_CALL`, not
     back to `PROLOGUE`). `ProviderFailure` means `PROVIDER_FAILED`.
   - no `tool_calls`: append the assistant `Message`, exit `COMPLETED`
     with its `content` as `final_text`.
   - `tool_calls` present: proceed to TOOL_ROUND (below).
4. **TOOL_ROUND**, per blueprint §5.3, for one assistant message's
   `tool_calls`:
   1. Dedupe (`_deduplicate_tool_calls`, above). Partition into `valid`
      (name present in `tool_surface`) and `invalid`.
   2. If `valid` is empty, exit `INVALID_TOOL_CALLS` — nothing this batch
      asked for can be carried out, so looping back to `MODEL_CALL` would
      make zero progress. A batch with even one valid call never takes
      this path; it continues normally, matching §5.3.2's own instruction
      that an invalid call gets an error result, not a turn-ending one.
   3. Append the assistant row (every call, valid and invalid). Call
      `persist(messages)`; an exception means `PERSISTENCE_FAILED` — this
      is blueprint §4.2's "the assistant tool-call row is appended and
      durably stored before any handler runs," now with somewhere real
      (if only a no-op today) to actually run.
   4. Append an error-result `Message` for each `invalid` call
      immediately.
   5. For each `valid` call: `await dispatch(name, arguments)` wrapped in
      `try/except Exception` — an exception becomes a bounded
      `tool_error: <message>` string, matching §5.3.3's instruction that
      the tool round, not `dispatch`'s caller, is responsible for
      exception-safety (see Rejected alternatives — this is the
      B2-vs-§5.3 tension, resolved here). Every result — successful or
      errored — is capped at `SADANA_CONVERSATION_TOOL_RESULT_CHARS`
      characters, then the whole batch is capped again against a
      per-turn running total at `SADANA_CONVERSATION_TOOL_TURN_BUDGET_CHARS`;
      a capped result says so and by how much, per §5.3.3's own
      instruction. Each result is appended *as it lands*, per §5.3.4, not
      batched — so a crash mid-round leaves at most the unfinished ids
      unpaired, which C2's `repair()` already knows how to close on the
      *next* turn's own REPAIR step.
   6. Loop back to `MODEL_CALL`.
5. **EPILOGUE.** Build `TurnResult`. The one named exception: if
   `exit_reason` is `BUDGET_EXHAUSTED` and no `final_text` exists yet, make
   exactly one more `complete()` call with `tools=()` and a synthetic
   "please summarize, no more tool calls" user message appended first
   (per `handle_max_iterations`, above) — its text becomes `final_text` if
   the call succeeds; `exit_reason` stays `BUDGET_EXHAUSTED` either way
   (a summary doesn't undo having run out of budget). This call doesn't
   consume another iteration (there's none left to consume) and isn't
   subject to `MODEL_CALL`'s own iteration-budget check — it's already
   inside `EPILOGUE`, explicitly outside that loop.

**The byte-stability check.** Blueprint §4.3: "the prompt-stability
assertion covers `sha256(system_prompt + definitions_json)`, because for
cached providers the tool schemas are part of the prefix" — not
`system_prompt` alone. Exposed as its own function, because nothing else
could otherwise produce a correct `prompt_sha256` to pass in:

```python
def turn_prompt_hash(system_prompt: str, tool_surface: ToolSurface) -> str:
    """sha256(system_prompt + the tool surface's canonical JSON) — the
    same canonicalization surface_hash() already uses, so the two stay
    consistent with each other."""
```

PROLOGUE's own check is `if turn_prompt_hash(system_prompt, tool_surface)
!= prompt_sha256: raise PromptDriftError(...)`.

**Types added:**

```python
class PromptDriftError(Exception):
    """A turn's system_prompt/tool_surface don't hash to the caller's own
    prompt_sha256. Always a bug — this never becomes an ExitReason."""

class ExitReason(Enum):
    COMPLETED = "completed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    WALL_CLOCK_EXHAUSTED = "wall_clock_exhausted"
    PERSISTENCE_FAILED = "persistence_failed"
    PROVIDER_FAILED = "provider_failed"
    CONTEXT_OVERFLOW_UNHANDLED = "context_overflow_unhandled"
    INTERRUPTED = "interrupted"
    INVALID_TOOL_CALLS = "invalid_tool_calls"

@dataclass(frozen=True)
class TurnKey:
    conversation: ConversationKey
    turn_seq: int

@dataclass(frozen=True)
class TurnResult:
    turn_key: TurnKey
    final_text: str | None
    exit_reason: ExitReason
    detail: str | None
    model_calls: int
    usage: model_access.Usage
    appended: range
```

`TurnResult` carries `turn_key` only — blueprint §4.6 lists both
`conversation_key` and `turn_key` as separate fields, but `turn_key`
(this design's own shape, matching `MessageKey`'s established pattern)
already contains the conversation key. A second, redundant top-level
field would just be one more way for the two to quietly disagree; dropped
here, noted rather than silently deviating from blueprint's literal
shape.

**`TurnKey`, defined now.** C2's own spec.md named exactly this moment:
"CONV-05 (the turn loop) is where a turn boundary first has a caller, and
defines `TurnKey` then." `turn_seq` is a parameter this work item's
caller supplies (there is no `Conversation` aggregate yet to derive it
from — CONV-06's job) rather than something `run_turn` invents.

**The three stable-hole seams.** All injected, all no-ops or
None-returning at backbone, matching blueprint §8 Open Question 3's own
treatment of the compression seam:

- `dispatch: Callable[[str, dict], Awaitable[str]]` — name and
  already-parsed arguments in, a result string out (or it raises, and
  the tool round catches that per step 4.5 above). Not blueprint §5.2's
  `ToolSpec.handler` shape (`(dict, ToolContext) -> Awaitable[str |
  Multimodal]`) — `ToolContext` and `Multimodal` don't exist; C3 declined
  the whole `handler` field for exactly this reason ("no reader yet").
  There is still no real `handler` anywhere to call — `dispatch` is
  EXECUTION's whole single entry point (B2's spec.md), assumed to exist,
  not designed here, same as B2 already fixed.
- `compress: Callable[[tuple[Message, ...], str], Awaitable[str |
  None]]` — current messages and current `system_prompt` in, a new
  `system_prompt` or `None` (can't/won't compress) out. A pragmatic
  substitute for blueprint §8 Q3's own recommendation
  (`async def compress(conv: Conversation) -> RotatedPrompt | None`) —
  neither `Conversation` nor `RotatedPrompt` exist yet. The substitute
  carries exactly what this work item's own `MODEL_CALL` retry actually
  needs (a new prompt string, or "give up"), nothing blueprint's richer
  future shape would add that has a caller here.
- `persist: Callable[[tuple[Message, ...]], Awaitable[None]] =
  _noop_persist` — defaults to a real no-op (`async def _noop_persist(m):
  return None`) so most tests never construct one. `PERSISTENCE_FAILED`
  is exercised by a test-supplied fake that raises.

**Interrupt handling.** Blueprint §8 Q4: "`asyncio.CancelledError` is the
token; no `_set_interrupt` flag, no interrupt message." `run_turn` catches
`asyncio.CancelledError` around the `MODEL_CALL`/`TOOL_ROUND` body, builds
`TurnResult` with `exit_reason=INTERRUPTED` from whatever state exists at
the moment of cancellation, and returns normally rather than re-raising —
a cancelled turn still gets a reportable result, not a crash. This is the
one place `run_turn` catches a `BaseException` subclass rather than
`Exception`; noted explicitly in Concerns.

## Interface

**In:** every parameter listed in `run_turn`'s signature above. `now` is
always a caller-supplied `float` — never read from `time.monotonic()`
inside this module, matching the exact discipline `wall_clock_remaining`
(C4) already established, and required by testing-conventions' wall-clock
restriction (a test drives `now` and `wall_clock_budget` directly with
literal floats; `INTERRUPTED` is tested by cancelling the actual
`asyncio.Task` running `run_turn`, which is task cancellation, not a real
clock or network dependency).

**Out:** `(TurnResult, tuple[Message, ...], IterationBudget, str)`, or
`PromptDriftError` (a bug, not a turn outcome). No other exception
escapes `run_turn` — `ContextOverflow`/`ProviderFailure` from `complete()`
are caught internally and become `ExitReason`s; an exception from
`dispatch` is caught internally per TOOL_ROUND step 5; an exception from
`persist` is caught internally and becomes `PERSISTENCE_FAILED`;
`asyncio.CancelledError` is caught internally and becomes `INTERRUPTED`.

## Acceptance criteria

- [ ] All eight `ExitReason`s are each exercised by at least one test
      that reaches them through `run_turn` itself (not just constructed
      as a bare enum value).
- [ ] `COMPLETED`: a text-only `Completion` produces `final_text` equal
      to that text.
- [ ] `BUDGET_EXHAUSTED` with the one-more-call summary path: an
      `IterationBudget` exhausted before any text response still
      produces a non-`None` `final_text` from the tool-less summary call,
      and `exit_reason` stays `BUDGET_EXHAUSTED`.
- [ ] `WALL_CLOCK_EXHAUSTED` fires purely from `wall_clock_budget`/`now`
      arithmetic — no test sleeps or reads the real clock.
- [ ] `PERSISTENCE_FAILED` fires from a `persist` fake that raises,
      exactly at the point after the assistant row is appended, before
      any `dispatch` call happens (provable: the fake's dispatch
      equivalent, if supplied, is never invoked).
- [ ] `PROVIDER_FAILED` fires from a fake `complete()`-equivalent path
      (monkeypatching `model_access.send` to return `Abort`, matching
      C6's own test style) reaching `run_turn`.
- [ ] `CONTEXT_OVERFLOW_UNHANDLED` fires when `compress` returns `None`;
      a separate test proves the *retry* path — `compress` returning a
      new prompt string leads to a second `complete()` call using it, not
      a turn-ending exit.
- [ ] `INTERRUPTED` fires from cancelling the real `asyncio.Task` running
      `run_turn`, not a flag.
- [ ] `INVALID_TOOL_CALLS` fires for an all-invalid batch; a
      mixed valid/invalid batch in the same test suite proves it does
      *not* fire and the turn continues instead.
- [ ] `turn_prompt_hash` changes when the tool surface changes, even if
      `system_prompt` doesn't — proves the hash genuinely covers both,
      not just the prompt string.
- [ ] A prompt/tool-surface mismatch raises `PromptDriftError`, not any
      `ExitReason`.
- [ ] `_deduplicate_tool_calls` drops an exact duplicate `(name,
      arguments)` pair, keeps the first occurrence's id, and leaves a
      batch with no duplicates unchanged.
- [ ] A tool result longer than `SADANA_CONVERSATION_TOOL_RESULT_CHARS`
      is capped, and the capped `Message.content` says it was capped.
- [ ] Two tool results whose combined length exceeds
      `SADANA_CONVERSATION_TOOL_TURN_BUDGET_CHARS`, neither individually
      over `SADANA_CONVERSATION_TOOL_RESULT_CHARS`, are still capped by
      the per-turn budget — proves the two caps are independent, not the
      same check applied twice.
- [ ] A `dispatch` call that raises produces a `tool_error:`-prefixed
      result, not a crash — the turn continues to the next call in the
      batch.
- [ ] Every test constructs its own fake `dispatch`/`compress`/`persist`
      and monkeypatches `model_access.send` (C6's own established style)
      — no real provider, no real EXECUTION, no real CONTEXT, no real
      durable store.

## Non-goals

- Real `EXECUTION`, `CONTEXT` compaction, or `CONV-08` durable
  persistence — three stable holes, none filled.
- `blueprint §5.7`'s `timeouts.*` config keys or any timeout enforcement
  around `complete()`/`dispatch` — introducing the keys without real
  enforcement behind them would be config nobody's behavior reads; actual
  enforcement (`asyncio.wait_for` or similar) is real new behavior this
  already-large work item doesn't also need to carry.
- `blueprint §5.7`'s `plugin_result_chars` — no plugin system, no
  `<plugin>.<tool>`-prefixed names exist anywhere in this project.
- `Observer` (blueprint §5.5) — not named in any of the nine CONV-0x
  work items' own build-order line, no test infrastructure needs it.
- More than one turn, more than one conversation, or anything about a
  live `Conversation` aggregate — `run_turn` runs exactly one turn, given
  everything it needs as plain values.
- Child conversations, skills, streaming — declined per intent.md's
  Constraints and blueprint §1.4's scope for the whole block.

## Rejected alternatives

**Trusting EXECUTION's dispatch result as already-safe (B2's framing),
declined.** Full reasoning in Design (TOOL_ROUND step 5). Chosen per the
Plan interview: blueprint §5.3's literal instructions win, because
they're also the only way any of this is testable right now — a fake
`dispatch` can be made to raise or return oversized output on purpose,
proving the wrapping actually works, where "trust it" would leave that
code path unwritten and unprovable.

**A class or `Protocol` bundling the three seams (or `run_turn` itself),
declined.** Same reasoning C2 used against `Transcript` and C6 used
against `ProviderPort`: one implementation of each exists (a fake, in
tests), nothing yet needs a second to abstract over. Three separate
keyword parameters, not one bundled dependency object — `guideline 2`'s
own caveat (this only argues for bundling once there's a real seam
consumer beyond "fewer function-signature characters") doesn't clear the
bar for three callables with three different call signatures.

**Introducing `blueprint §5.7`'s `timeouts.*`, declined.** Full reasoning
in Non-goals — config with no enforcement behind it is worse than no
config; enforcement is its own real behavior, not a byproduct of adding a
key.

**A separate top-level `conversation_key` field on `TurnResult`,
declined.** Full reasoning in Design — `turn_key` already contains it;
duplicating it is exactly the "two places claiming the same fact" shape
C4's own spec.md already cited a hermes production incident to warn
against.

## Concerns

**Catching `asyncio.CancelledError` (a `BaseException`, not an
`Exception`) inside `run_turn` is the one place this design departs from
"only catch `Exception`."** It's necessary — blueprint §8 Q4 fixes
cancellation as the interrupt mechanism, and a cancelled turn needs to
still report `TurnResult(exit_reason=INTERRUPTED)` rather than propagate
a raw `CancelledError` to whatever's awaiting the task — but it's worth a
reviewer's attention specifically because swallowing `CancelledError`
incorrectly (e.g., inside a loop that keeps going instead of unwinding)
is a well-known async footgun. The design here catches it once, at the
outermost level of the MODEL_CALL/TOOL_ROUND body, and does not
re-enter the loop afterward — stated explicitly so a reviewer checks
exactly that.

**This is the largest work item in the project, and its own spec makes
more simplifying substitutions than any prior one** (the `compress`
signature diverges from blueprint's own recommendation; `dispatch`
diverges from blueprint's `ToolSpec.handler` shape; two of three
`tool_results` config keys are introduced, one isn't; `timeouts.*` isn't
introduced at all). Each is individually justified above, but a reviewer
checking this work item should weigh whether the *count* of substitutions
made in one sitting is itself worth flagging — guideline 2 argues for
minimizing bets one at a time, and this spec asks for several at once
because they all became decidable only once every prior work item's
shape was fixed.

`testing-conventions` is the only applicable policy skill and is
satisfied throughout, including the two hardest cases (`INTERRUPTED` via
real task cancellation, `WALL_CLOCK_EXHAUSTED` via an explicit `now`) —
discussed directly in Interface rather than left implicit.
`project-structure` and `reference-lookup` still don't exist in this
project.
