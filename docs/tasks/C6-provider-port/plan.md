# Plan: Asking a model for a real response never requires trusting an unkept promise (from intent.md 2026-09-03)

## Context

Build stage for **C6-provider-port**, CONV-04 from
`docs/reference/conversation_block_blueprint.md` §7 — the largest
CONVERSATION work item so far. `intent.md` and `spec.md` are already
written and checked; `spec.md` is unusually complete for this project (it
cites exact hermes line numbers for every ported function and gives
step-by-step pipeline behavior), so this plan skips Explore/Plan
subagents — they would only restate `spec.md`.

The problem this closes: C2 and C3 already wrote code
(`pending_tool_call_ids`, `ToolSpec`) that assumes a tool call arrives
with a real id and parsed arguments. Nothing enforces that yet —
`model_access.send()` hands back whatever a provider's raw JSON contains,
arguments still as text, ids sometimes absent, and stops after exactly
one HTTP attempt. This work item is the thing that keeps that promise: an
async `complete()` that retries transparently, repairs malformed
arguments to the same standard hermes's production history required, and
never returns a tool call without a real id.

## Files that change

- `src/sadana/conversation.py` — same file C2/C3/C4 all extended. New
  additions: `ContextOverflow`, `ProviderFailure` (exceptions),
  `Completion` (frozen dataclass), `repair_tool_call_arguments(raw) ->
  dict`, `deterministic_call_id(name, arguments, index=0) -> str`,
  `coalesce_tool_call_id(tool_call: dict) -> str`,
  `uniquify_tool_call_ids(tool_calls) -> tuple[dict, ...]`, and
  `async def complete(*, system, messages, tools, provider, model) ->
  Completion`. New imports: `asyncio`, `re`, `from sadana import
  model_access`.
- `tests/unit/test_conversation.py` — same file all four prior work items
  extended. New tests, one per `spec.md` acceptance criterion, monkeypatching
  `model_access.send` (matching `tests/unit/test_model_access.py`'s own
  established style — e.g. `test_send_calls_request_fn_and_classifies_its_result`)
  rather than any real network call.

## Design recap (from spec.md, restated so this plan stands alone)

**Ported, with hermes's exact source cited in each docstring:**

```python
def repair_tool_call_arguments(raw: str) -> dict:
    """Ported from hermes agent/message_sanitization.py:195-293
    (_repair_tool_call_arguments) + :144-183
    (_escape_invalid_chars_in_json_strings), adapted to return a parsed
    dict instead of a repaired string, and with no logging (this project
    has no logging convention yet — see spec.md Rejected alternatives).
    Never raises; last resort is {}."""
    # Pass 0: json.loads(raw, strict=False) then re-serialize — handles
    #   literal control chars inside string values.
    # Pass 1: strip trailing commas before "}"/"]" via regex.
    # Pass 2: close unclosed "{"/"[" by counting brace/bracket balance.
    # Pass 3: trim excess closing brackets, bounded to 50 iterations.
    # Pass 4: escape unescaped control chars char-by-char, retry parse.
    # Last resort: "{}".

def deterministic_call_id(name: str, arguments: str, index: int = 0) -> str:
    """Ported from hermes :654-663. sha256(f"{name}:{arguments}:{index}")[:12],
    prefixed "call_". Never uuid4 — a random id invalidates a provider's
    prompt cache on every retry of the same call."""

def coalesce_tool_call_id(tool_call: dict) -> str:
    """Ported from hermes :713-731, dict-only (every tool_call this
    project sees is already a parsed-JSON dict). Tries call_id then id,
    strips whitespace, splits on "|" and takes the first half (a bridged
    id can arrive as "call_id|response_item_id"). Returns "" when neither
    field is set — the caller (complete()) treats "" as "missing"."""

def uniquify_tool_call_ids(tool_calls: tuple[dict, ...]) -> tuple[dict, ...]:
    """Ported from hermes :734-810. First occurrence of a duplicate id
    keeps it; later collisions get a deterministic "_d<n>" suffix — never
    random, same prompt-cache reasoning as deterministic_call_id."""
```

**Declined from the same hermes section** (full reasoning in spec.md):
`tool_call_id_variants`/`tool_result_id_variants` (hermes `:691-712`) —
no caller in this project's own pairing logic, which does exact-match
pairing against one already-resolved id, never a composite one.

**New types:**

```python
class ContextOverflow(Exception):
    """The request was too large for the model's context window."""

class ProviderFailure(Exception):
    """This attempt failed outright — not retryable by this function."""

@dataclass(frozen=True)
class Completion:
    content: str | None
    tool_calls: tuple[dict, ...]  # {"id": str, "name": str, "arguments": dict}
    finish_reason: str
    usage: model_access.Usage  # reused directly, no new Usage type
```

`Completion.tool_calls` stays `tuple[dict, ...]` — no `ToolCall`
dataclass. C2's own spec.md already rejected exactly that for
`Message.tool_calls` ("Reusing the dict shape is one fewer bet");
inventing one here would silently reopen that decision.

**`complete()` — the full pipeline, in order:**

```python
async def complete(
    *, system: str, messages: tuple[dict, ...], tools: tuple[dict, ...],
    provider: str, model: str,
) -> Completion:
```

1. `request_messages = ({"role": "system", "content": system},) + messages`.
2. `attempt = 0`; loop: build `model_access.Request(messages=request_messages,
   provider=provider, model=model, tools=tools, attempt=attempt)`, call
   `outcome = await asyncio.to_thread(model_access.send, request)`.
3. Branch on `outcome`'s type:
   - `Retry` → `attempt = outcome.next_attempt`, loop again. No cap of
     its own — `model_access.classify()` already enforces
     `SADANA_MODEL_ACCESS_MAX_RETRIES` and returns `Abort` once exceeded,
     so this loop's termination is already guaranteed by a contract C1
     already built and tested.
   - `NeedsContextCompression` → `raise ContextOverflow(outcome.detail)`.
   - `NeedsCredentialOrProviderChange`, `Abort`, or `Degenerate` →
     `raise ProviderFailure(outcome.detail)`. (`Degenerate` only carries a
     `detail: str`, no `finish_reason`/`usage` — it cannot become a real
     `Completion`; see spec.md Concerns for the C1 gap this surfaces,
     not fixed here.)
   - `Response` → proceed to step 4.
4. For each raw `tool_call` dict in `outcome.tool_calls`: resolve an id
   via `coalesce_tool_call_id(tc)`, falling back to
   `deterministic_call_id(name, raw_arguments, index)` where `index`
   counts *only among the calls needing synthesis* in this batch (so two
   missing-id calls don't collide); parse arguments via
   `repair_tool_call_arguments`. Run the whole resolved batch through
   `uniquify_tool_call_ids` last (catches a provider-supplied duplicate
   even after synthesis).
5. Return `Completion(content=outcome.content, tool_calls=<resolved
   batch>, finish_reason=outcome.finish_reason, usage=outcome.usage)`.

## Order of work

1. **Add the four pure helper functions first** — `repair_tool_call_arguments`,
   `deterministic_call_id`, `coalesce_tool_call_id`,
   `uniquify_tool_call_ids` — plus `ContextOverflow`, `ProviderFailure`,
   `Completion`. Each is independently testable with no dependency on
   `model_access` at all; landing them first means the riskiest part
   (the repair cascade, ported from a 100-line hermes function) gets
   proven before anything is built on top of it.
2. **Add `complete()`** on top of the now-proven helpers, importing
   `model_access` and `asyncio`.
3. **Append tests to `tests/unit/test_conversation.py`**, in the same
   order: helper tests first (no `monkeypatch` needed for the pure ones),
   then `complete()`'s own tests (`monkeypatch.setattr(model_access,
   "send", ...)`). Run `make test` scoped to this file first.
4. **Run `make verify`** and paste its output as this stage's evidence.

## Risks

**What could this change break?** Nothing existing. All new names are
additions; no existing function in `conversation.py` is touched.
`model_access.py` and every `model_providers/*/provider.py` are untouched
— this work item only calls `model_access.send`/reads its dataclasses,
never edits them. The only new cross-module dependency
(`conversation.py` importing `model_access`) is new for this file but not
new for the project — `model_access.py` already imports `config` the
same way `conversation.py` started doing in C4.

**Which step is riskiest?** `repair_tool_call_arguments` — a five-pass
cascade ported from a ~100-line hermes function, the largest single piece
of ported logic in this project so far. Getting a pass's order wrong (or
dropping the bounded-iteration guard on the bracket-trimming pass) could
either fail to repair a case hermes's own production history needed, or
loop unboundedly on adversarial input. Mitigation: land it first (order
of work, step 1) with a dedicated test per pass (trailing comma, unclosed
bracket, control character, and the unconditional last-resort `{}`) before
`complete()` is built on top of it, and keep hermes's own 50-iteration
bound on the bracket-trimming pass exactly as written rather than
"simplifying" it away.

**Is this drifting back toward anything `spec.md` already rejected?**
Checked against all four Rejected Alternatives entries: no `logger`/
`logging` import anywhere (the repair cascade stays pure), no
`tool_call_id_variants`/`tool_result_id_variants`, no `ProviderPort`
class or `Protocol` (`complete` is a plain `async def`, no `self`), no
second retry cap (the loop's only state is a local `attempt` variable,
nothing read from config), no `ToolCall` dataclass (`Completion.tool_calls`
is `tuple[dict, ...]`).

## Proof

`tests/unit/test_conversation.py` covers, at minimum, one test per
`spec.md` acceptance-criteria checkbox:

- `complete()` returns a `Completion` for a `Response` outcome with
  `content`/`finish_reason`/`usage` carried through unchanged.
- `complete()` retries transparently: a fake `send()` returning `Retry`
  once then `Response` yields exactly one `Completion` (no exception),
  and the second call's `request.attempt` equals the first `Retry`'s
  `next_attempt`.
- `complete()` raises `ContextOverflow` for `NeedsContextCompression`.
- `complete()` raises `ProviderFailure` for `NeedsCredentialOrProviderChange`,
  for `Abort`, and for `Degenerate` — three separate tests.
- A tool call with a well-formed `id`/`call_id` (including the `"x|y"`
  composite-split case) keeps it, coalesced correctly.
- A tool call with no `id`/`call_id` gets a `deterministic_call_id`
  result, and the same inputs produce the same output across two
  separate calls (determinism, not just non-emptiness).
- Two tool calls in one batch both missing an id get two *different*
  synthesized ids.
- Two tool calls in one batch sharing the same supplied id get
  deduplicated via `uniquify_tool_call_ids`'s `_d<n>` suffix — first
  keeps its id, second doesn't.
- `repair_tool_call_arguments`: well-formed JSON parses straight through;
  at least one malformed case per cascade pass (trailing comma; unclosed
  bracket; a literal control character inside a string) is repaired;
  fully unparseable input falls back to `{}`.
- Every test constructs its own fake `Outcome` via
  `monkeypatch.setattr(model_access, "send", ...)` — no real provider, no
  network, no turn loop, no live conversation.

`make verify` run at the end, pasted in full, ending `VERIFY OK`, is this
stage's evidence.
