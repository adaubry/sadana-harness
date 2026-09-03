# Spec: Asking a model for a real response never requires trusting an unkept promise

Intent: docs/tasks/C6-provider-port/intent.md

## Requirements

1. One call gets a caller a model's response, or one of exactly two named
   failures — never MODEL-ACCESS's six-shaped `Outcome`, and never a
   transient failure a caller has to notice and retry itself. Traces to
   intent's Proposed outcome.
2. Every tool call in that response carries a non-empty id and
   already-parsed arguments — never raw text, never nothing. Traces to
   intent's Proposed outcome and Problem (the promise C2/C3 already
   assumed).
3. A malformed or missing id or a malformed arguments string from a real
   provider is repaired robustly, not just rejected — built to the same
   standard hermes's own production history required, not scaled down to
   only what today's one wired provider happens to need. Traces to the
   Plan interview's explicit choice (recorded in intent's "Changed during
   planning").
4. Provable entirely on its own — no live conversation, turn loop, or
   history needs to exist. Traces to intent's Constraints.
5. Nothing about how a request reaches a provider changes, and no
   provider's own code changes. Traces to intent's Constraints.
6. No provider-fallback, streaming, or attempt-resumption behavior is
   introduced. Traces to intent's Constraints.
7. Whatever a caller eventually needs to say about how big or how long one
   attempt may be is not decided here. Traces to intent's Constraints.

## Design

**Where this lives.** `src/sadana/conversation.py` — the fourth work item
in a row to extend it (C2 transcript, C3 tool surface, C4 budgets), per
B1/B2's one-flat-module-per-block convention. Decided explicitly during
the Plan interview against the blueprint's own (already-declined-once, in
C2) separate-file layout: reopening that decision now, for one work item,
would be the bet-count guideline's opposite move — one settled convention,
four times, beats reconsidering it every time a work item's flavor
differs slightly.

**Learning from the reference (guideline 1).** Read
`../hermes-agent/agent/message_sanitization.py` directly — not just the
blueprint's summary — for every function this spec ports.

- `_repair_tool_call_arguments` (`:195-293`) and its helper
  `_escape_invalid_chars_in_json_strings` (`:144-183`): a five-pass
  cascade — fast-path re-serialize via `json.loads(raw, strict=False)`
  (handles literal control chars inside strings); strip trailing commas
  before `}`/`]`; close unclosed brackets/braces by counting them; trim
  excess closing brackets (bounded to 50 iterations); escape unescaped
  control characters and retry; last resort, replace with `"{}"`. Every
  pass is a pure string transform with no side effect beyond hermes's own
  `logger.warning` calls, which this port drops (see Rejected
  alternatives — this project has no logging convention yet, and adding
  one as a side effect of this work item would be exactly the kind of
  unrequested infrastructure the ladder warns against).
- `deterministic_call_id` (`:654-663`): `sha256(f"{name}:{arguments}:
  {index}")[:12]`, prefixed `"call_"`. Deliberately not `uuid4` — hermes's
  own comment names why: a random id changes every retry, invalidating a
  provider's prompt cache on every replay of the same call. Copied
  verbatim in behavior.
- `coalesce_tool_call_id` (`:713-731`): tries `call_id` then `id` (dict or
  attribute access; this port only needs dict access, since every
  `tool_call` this project ever sees is already a plain dict off a parsed
  JSON body), strips whitespace, and — the one piece of real complexity
  worth keeping — splits on `"|"` and takes the first half, because a
  bridged id can arrive as `"call_id|response_item_id"`. Returns `""` when
  neither field is set, which this port's own caller (`complete()`) then
  treats as "missing" and routes to `deterministic_call_id`.
- `uniquify_tool_call_ids` (`:734-810`): when two calls in the same
  assistant turn carry the same id (observed by hermes across several
  providers reusing a counter that resets between batches), the first
  occurrence keeps its id and every later collision gets a deterministic
  `_d<n>` suffix — never a random one, same prompt-cache reasoning as
  `deterministic_call_id`.
- **Declined from the same hermes section, and why:**
  `tool_call_id_variants`/`tool_result_id_variants` (`:691-712`) exist to
  match a *result's* id back against a *call's* id under several possible
  spellings — the Codex Responses API's split `call_id`/`response_item_id`
  fields, and the same `"x|y"` composite encoding `coalesce_tool_call_id`
  already splits on. C2's own pairing logic
  (`pending_tool_call_ids`/`append`, `docs/tasks/C2-keys-transcript/spec.md`)
  never produces or expects a composite id — it does exact-string-match
  pairing against whatever `coalesce_tool_call_id` (or
  `deterministic_call_id`) already resolved to one clean id. Porting these
  two functions now would be state with no reader: nothing in this
  project would ever call them. This narrows the Plan interview's "port
  the full coalescing machinery" to the part of that machinery this
  project's own pairing logic can actually use — a refinement made
  explicit here, not a silent cut.

**Reusing `Message.tool_calls`'s shape, not inventing `ToolCall`.**
Blueprint §5.1 writes `Completion.tool_calls` as `list[ToolCall]` — a
dataclass. `Completion.tool_calls` in this design stays
`tuple[dict, ...]` instead, each dict `{"id": str, "name": str,
"arguments": dict}`. C2's own spec.md already argued this exact question
and rejected a second `ToolCall` type for `Message.tool_calls`
(`docs/tasks/C2-keys-transcript/spec.md` § Rejected alternatives:
"Reusing the dict shape is one fewer bet"), and `pending_tool_call_ids`
already reads `tc["id"]` from that shape. Introducing `ToolCall` here
would silently reopen a decision already made and justified — the exact
failure mode build-skill's own interrogation step exists to catch, caught
here at Design time instead.

**Outcome-to-result mapping.** `model_access.send()` returns one of six
`Outcome`s. This design maps every one:

| `Outcome` | Becomes |
|---|---|
| `Response` | `Completion` |
| `Retry` | absorbed — loop again with `next_attempt`, never surfaces |
| `NeedsContextCompression` | raise `ContextOverflow` |
| `NeedsCredentialOrProviderChange` | raise `ProviderFailure` |
| `Abort` | raise `ProviderFailure` |
| `Degenerate` | raise `ProviderFailure` — see Concerns |

`Retry` never reaching the caller is the point of this whole work item:
`model_access.send()`'s own docstring says it "never retries internally,"
and `classify()` already enforces a bounded retry cap
(`SADANA_MODEL_ACCESS_MAX_RETRIES`, read inside `send()` itself) — once
that cap is hit, `classify()` returns `Abort`, not another `Retry`. So
this port's own retry loop needs no cap, no counter, and no config key of
its own: `while` the outcome is `Retry`, call again with
`outcome.next_attempt`; the loop's own termination is already guaranteed
by a contract C1 already built and tested. Adding a second cap here would
be two things owning one guarantee — exactly the "config-vs-code drift"
shape C4's own Design section already cited hermes's `sys.maxsize`/500
incident to warn against.

**`complete()` — a free async function, not a `ProviderPort` class.**
Blueprint §5.1 writes `ProviderPort` as a `Protocol` with a `complete`
method — implying an object bound to one provider/model that a caller
holds and calls into. This project has, four work items running, never
built a class for a single-implementation concern: C2 declined a
`Transcript` protocol/class with the same reasoning ("a `Protocol` earns
its place once a second implementation exists to abstract over"). Applied
again here: nothing in this project has, or will soon have, a second
`complete`-shaped thing to swap in — there is one provider mechanism
(`model_access.send()`), and a test replaces its behavior by monkeypatching
`model_access.send` directly (matching `test_model_access.py`'s own
established style, e.g. `test_send_calls_request_fn_and_classifies_its_result`),
not by injecting a second implementation of an interface.

`self` and `budget` both drop from blueprint's signature — `self` because
there's no instance; `budget: RequestBudget` because intent.md's
Constraints explicitly decline inventing that shape now (blueprint itself
never defines `RequestBudget` anywhere else). `provider: str` and
`model: str` become explicit parameters instead, since nothing else binds
them for a free function the way a class's `__init__` would:

```python
async def complete(
    *, system: str, messages: tuple[dict, ...], tools: tuple[dict, ...],
    provider: str, model: str,
) -> Completion: ...
```

`system` stays separate from `messages`, matching blueprint exactly, and
gets prepended as a synthetic `{"role": "system", "content": system}`
entry only when building the actual `model_access.Request` —
`conversation.py`'s own `Message` tuple (a conversation's real history)
never carries a synthetic system-role row; that synthesis is this
function's own, local, wire-format concern.

**The full pipeline, in order, inside `complete()`:**

1. Build `request_messages = ({"role": "system", "content": system},) +
   messages`.
2. `attempt = 0`; loop: build a `model_access.Request`, call
   `await asyncio.to_thread(model_access.send, request)` (blueprint §5.1's
   own instruction: wrap the sync client, don't make this function sync
   to fit it — `send()` does blocking I/O via `manifest.request_fn`).
3. On `Retry`, set `attempt = outcome.next_attempt` and loop again. On
   `NeedsContextCompression`, raise `ContextOverflow(outcome.detail)`. On
   `NeedsCredentialOrProviderChange`, `Abort`, or `Degenerate`, raise
   `ProviderFailure(outcome.detail)`.
4. On `Response`: for each raw `tool_call` dict in `outcome.tool_calls`
   (already `tuple[dict, ...]` straight off the parsed JSON body, per
   `model_access.py`'s own `classify()`), resolve an id via
   `coalesce_tool_call_id(tc)`, falling back to
   `deterministic_call_id(name, raw_arguments, index)` — `index` is the
   call's position among *only the calls that needed synthesis*, matching
   hermes's own per-batch indexing so two missing-id calls in one batch
   don't collide — then parse arguments via the repair cascade. Run the
   whole resolved batch through `uniquify_tool_call_ids` last, so a
   provider-supplied duplicate is still caught even after synthesis.
5. Return `Completion(content=outcome.content, tool_calls=<the resolved
   batch>, finish_reason=outcome.finish_reason, usage=outcome.usage)`.

**Types added:**

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
    usage: model_access.Usage  # reused directly, not redefined — one fewer bet
```

**Guideline 2 (reduce bets) — where this sits relative to the plugin
seam.** No plugin infrastructure exists yet (declined per every prior
work item's own applicable-case check). This work item doesn't foreclose
one: `complete()` takes `provider`/`model`/`tools` as plain values, so a
future caller — whether that's CONV-05's loop directly or, eventually, a
plugin-aware layer above it — supplies them the same way regardless of
where they came from.

**Guideline 3 (least step-cost).** The central choice is absorbing
`Retry` inside this function rather than surfacing it as a third
exception type or a return-value variant the caller would have to branch
on. That's "make a step harder to misuse" applied at the cheapest
possible point: a caller of `complete()` literally cannot forget to
retry, because there is no retry-shaped thing in its interface to forget
about. The alternative — exposing `Retry` and asking CONV-05's loop to
call `complete()` again — would add a step (a retry loop) to every future
caller instead of building it once, here, where the knowledge of how
`model_access.send()`'s retry contract works already lives.

**Guideline 4 (minimise mutable state).** Inventory: the `attempt`
counter inside `complete()`'s own retry loop is real, necessary,
short-lived state — it cannot be derived, and it dies with the function
call. Nothing else in this design is stored: `Completion` is a plain
returned value, id resolution and argument repair are pure functions of
their inputs, and the retry cap itself is derived by *not* existing here
at all (owned once, by `model_access.classify()`).

## Interface

**In:** `complete()` takes `system: str`, `messages: tuple[dict, ...]`,
`tools: tuple[dict, ...]`, `provider: str`, `model: str`.

**Out:** `complete()` returns `Completion`, or raises `ContextOverflow` or
`ProviderFailure` (both carry the underlying `Outcome`'s `detail: str` as
their message). Never raises anything else that isn't already a bug
(`UnknownProvider`/`ProviderNotWired` from `model_access` itself still
propagate unchanged — this port adds no handling for "the provider name
doesn't exist," which is a caller configuration error, not a runtime
outcome to route).

`repair_tool_call_arguments(raw: str) -> dict` never raises — its own
last resort is `"{}"`, which always parses. `coalesce_tool_call_id(tc:
dict) -> str` never raises — returns `""` for "no id present."
`deterministic_call_id(name: str, arguments: str, index: int = 0) -> str`
never raises. `uniquify_tool_call_ids(tool_calls: tuple[dict, ...]) ->
tuple[dict, ...]` never raises.

## Acceptance criteria

- [ ] `complete()` returns a `Completion` for a `Response` outcome, with
      `content`/`finish_reason`/`usage` carried through unchanged.
- [ ] `complete()` retries transparently on `Retry` — a fake `send()`
      returning `Retry` once then `Response` results in exactly one
      `Completion`, not an exception, and the second call's `Request.attempt`
      equals the first `Retry`'s `next_attempt`.
- [ ] `complete()` raises `ContextOverflow` for `NeedsContextCompression`.
- [ ] `complete()` raises `ProviderFailure` for `NeedsCredentialOrProviderChange`,
      `Abort`, and `Degenerate` (three separate test cases, not folded
      into one).
- [ ] A tool call with a well-formed `id`/`call_id` keeps it, coalesced
      correctly, including the `"x|y"` composite-split case.
- [ ] A tool call with no `id` and no `call_id` gets a
      `deterministic_call_id` result — same inputs, same output, across
      two separate calls (determinism, not just "some non-empty string").
- [ ] Two tool calls in one batch that both need synthesis (both missing
      an id) get two *different* synthesized ids (the per-batch `index`
      argument actually varies).
- [ ] Two tool calls in one batch that arrive with the *same* supplied id
      get deduplicated via `uniquify_tool_call_ids`'s `_d<n>` suffix
      rule — the first keeps its id, the second doesn't.
- [ ] `repair_tool_call_arguments` parses well-formed JSON straight
      through, repairs at least one malformed case from each of the
      cascade's passes (a trailing comma; an unclosed bracket; a literal
      control character inside a string), and falls back to `{}` for
      input nothing else can parse.
- [ ] Every test constructs its own fake `Outcome`s by monkeypatching
      `model_access.send` — no real provider, no network, no turn loop, no
      live conversation.

## Non-goals

- `tool_call_id_variants`/`tool_result_id_variants` — no caller in this
  project; see Design.
- A `ProviderPort` class or `Protocol` — see Design.
- The `budget: RequestBudget` parameter blueprint's signature names — its
  shape isn't decided anywhere yet.
- Provider fallback chains, streaming, attempt resumption/checkpointing —
  declined per intent.md's Constraints and blueprint §1.4's own scope for
  the whole CONVERSATION block.
- Fixing `model_access.Degenerate`'s own shape (no `finish_reason`/`usage`
  carried) — see Concerns.
- A logging convention — see Rejected alternatives.

## Rejected alternatives

**Hermes's `logger.warning` calls throughout the repair cascade,
declined.** This project has no logging convention yet (no `OBSERVABILITY`
block, no `logging` usage anywhere in `src/sadana/`). Adding one as a side
effect of porting an unrelated mechanism would be exactly the kind of
unrequested infrastructure this project's own build discipline argues
against. The functions stay pure; if a caller wants to know a repair
happened, that's a future work item's own decision about what
observability this project wants, not a default this one reaches for
because hermes happened to have it.

**`tool_call_id_variants`/`tool_result_id_variants`, declined** — full
reasoning in Design. Dead code: no caller in this project's own pairing
logic exists or is planned to exist, since that logic already does
exact-match pairing against a single resolved id.

**A `ProviderPort` class/`Protocol`, declined** — full reasoning in
Design. Same argument C2 already used against a `Transcript`
protocol/class.

**A second, port-owned retry cap, declined.** `model_access` already
owns and enforces one; a second cap here would be two places claiming the
same guarantee, the precise shape of drift C4's own spec.md already cited
a real hermes production incident to warn against.

**A `ToolCall` dataclass, declined** — full reasoning in Design. Would
silently reopen a decision C2's own spec.md already made and justified.

## Concerns

**`model_access.Degenerate` (C1, already closed) discards `finish_reason`
and `usage` even though its own outcome is "a response that technically
succeeded but carries no usable content" — a distinction that matters for
anything that ever wants to know how many tokens a degenerate attempt
still cost.** This work item maps `Degenerate` to `ProviderFailure`
(matching blueprint §6's turn-loop state diagram, which shows no separate
degenerate exit from `MODEL_CALL`), which is the right call for *this*
work item's own scope — but it means a degenerate attempt's usage is
silently lost, not just unrouted. Not fixed here: `model_access.py` is a
closed work item's code, and reshaping `Degenerate` to carry `usage`
would be `model_access`'s own decision to make, prompted by an actual
caller that needs it — worth a maintain-stage look if one shows up,
mirroring exactly how C4→C5 handled a smaller version of the same shape
of gap.

**No unresolved policy conflict beyond the above.** `testing-conventions`
is the only applicable policy skill; every acceptance criterion above is
phrased as a relationship or a determinism check, not a snapshot, and
every test replaces `model_access.send` rather than touching the network.
`project-structure` and `reference-lookup` still don't exist in this
project.
