# Spec: A conversation can compact its history and mark what's safe to reuse

Intent: docs/tasks/C10-context-lifecycle/intent.md

## Requirements

1. A new module owns CONTEXT's four lifecycle checkpoints (before-send,
   after-response, after-tool-result, turn-complete), matching
   `docs/tasks/B2-cycle-contract/spec.md`'s already-fixed checkpoint order,
   read/write rules, and state-lifetime rule (only after-response and
   turn-complete may write CONTEXT's own state; before-send is read-only).
2. Every real caller of the turn loop (`run_turn`, `take_turn`, `run_child`,
   and every caller of those — `eval_harness.py`, `prove_conversation_e2e.py`,
   the test suite) is migrated off the injected `compress` callback
   parameter onto this module's real functions. The callback parameter is
   removed, not merely bypassed.
3. after-response has real behavior: it accumulates usage
   (`prompt_tokens`, `completion_tokens`) reported by each model call into a
   running, conversation-lifetime total.
4. before-send has real behavior for the cache-boundary piece: it marks the
   boundary between the conversation's stable system-prompt prefix and
   everything after it, and marks a small, fixed number of the most recent
   messages, using data the conversation already owns (no re-parsing of
   concatenated prompt text) — carried through to an actual HTTP request
   `send()` submits to OpenRouter, real enough to show up in a live round
   trip's request body.
5. after-tool-result and turn-complete (compaction) each get a real call
   site wired into the loop, but stay behaviorally identical to today's
   no-op (`None`/identity) — named explicitly as follow-up work, not built
   now.
6. Nothing here designs EXECUTION's or SESSION-STORE's own interface,
   matching B2's own non-goals.
7. CONTEXT's own state does not persist beyond the conversation and is
   never written to disk.

## What the reference corpus showed

Read in full: `agent/prompt_cache_boundary.py` (95 lines),
`agent/prompt_cache_scope.py` (126 lines), `agent/prompt_caching.py` (the
marker-placement half, through `apply_anthropic_cache_control`). Skimmed for
shape only: `agent/prompt_builder.py` (2471 lines) and `agent/system_prompt.py`
(1172 lines) are the message-assembly/system-prompt-assembly bulk of
hermes's CONTEXT block — neither is analogous to anything this item builds
(they assemble hermes's own much larger, plugin-fed system prompt; sadana's
`create_conversation()` already does the equivalent assembly in a few
lines).

**The real marker-placement algorithm** (`apply_anthropic_cache_control`) is
a pure function: takes a request-local copy of the wire message list, never
mutates the stored transcript, and is idempotent (strips any pre-existing
markers before placing new ones, so calling it twice never accumulates past
Anthropic's 4-marker budget). It marks up to two spots — the static system
prefix (a caller-supplied string boundary, passed in as a parameter, not
discovered) and the tail of the system prompt — then spends whatever
markers remain on the most recent eligible non-system messages, where
"eligible" means the end of a completed tool-call/result run or an ordinary
turn (`_completed_transaction_endpoint_indexes`), skipping messages a
provider's route would silently drop a marker from anyway (empty content,
empty `role:tool`).

**`agent/prompt_cache_boundary.py`** is a *different* mechanism: a
process-global registry (`threading.Lock` + an LRU `OrderedDict`) that lets
a webhook/cron message *builder* declare, at construction time, the exact
byte where a repeated invocation's static scaffold ends and its volatile
tail (ticket id, timestamp) begins — so a scaffold fired every few minutes
by the same job doesn't reinvalidate the cache on every fire. Its own
docstring is explicit about why it exists instead of a delimiter search at
request time: markers can legitimately recur inside a skill body or an
event payload, and any string-search heuristic then either shrinks the
cached prefix or silently absorbs volatile bytes into it — reintroducing
the exact per-invocation cache miss the mechanism exists to fix.

**`agent/prompt_cache_scope.py`** solves a problem this project's own
design does not have. It exists because hermes mints a *new physical
session id* on every legacy compression rotation, so a naive cache key
fragments on every rotation (issue #79017) — fixed with a DB-backed walk to
the pre-rotation lineage root, memoized on the agent object. Checked our
own `rotate_prompt()` (`src/sadana/conversation.py`) directly: it only ever
changes `system_prompt`, `prompt_sha256`, and `prompt_epoch` — `Conversation.key`
is untouched by construction, by the same design C8's `defer_invalidation`
already committed to (a `Conversation` holds only a name, never a live
handle, so nothing about rotation can touch its identity). The bug this
hermes file exists to fix cannot occur here.

**The rest of `prompt_caching.py`** — the four-way breakpoint-budget
arithmetic competing with a tool-schema cache layout, the native-Anthropic
vs. envelope vs. LiteLLM branching, the Alibaba/Qwen/opencode-go TTL clamp
tables built from wire-measured incident data — is real, hard-won,
production-tested knowledge, but all of it exists to arbitrate between
routes and providers sadana does not have. `model_access.py` wires exactly
one provider (OpenRouter, an envelope-style route) today.

**Decisions adopted:** a pure, request-local, idempotent marker-application
function, never mutating the stored transcript (this also matches CLAUDE.md's
own rule that a conversation's history is mutated only through
`append`/`repair`); storing an explicit boundary rather than re-deriving it
by string search, for the exact reason `prompt_cache_boundary.py`'s own
docstring gives; skipping a marker on content a provider's route would
silently drop it from anyway.

**Decisions declined, with the specific cost named:** the four-way
breakpoint-budget arithmetic and every native-Anthropic/LiteLLM/Alibaba
branch — sadana has one provider, so this is branching with no second
branch to take, and every additional provider this project wires can carry
its own quirk into `mark_cache_boundary` when it actually exists, the same
way hermes's own branches trace to a named incident on a real route.
`prompt_cache_boundary.py`'s registry — no webhook/cron caller exists in
this project (`CHANNELS`/`SCHEDULING` are both unbuilt blocks); a
process-global mutable registry with an LRU eviction policy for a workload
with no caller is exactly the speculative infrastructure CLAUDE.md's design
guideline warns against, and it is also the shape (a mutable counter behind
a lock) CLAUDE.md separately rules out for anything modeled as a resource.
`prompt_cache_scope.py` in full — its problem does not exist in a codebase
where the conversation key is already rotation-stable by construction; only
`context.py`'s own docstring should ever explain the omission, so a future
reader who does read this hermes file does not go looking for the same bug
that already can't happen here.

## Design

New module `src/sadana/context.py` — one flat module per block, the
convention `config.py`/`model_access.py` already set.

- `ContextState` (frozen dataclass): `total_prompt_tokens: int = 0`,
  `total_completion_tokens: int = 0`. Created once, alongside every other
  per-conversation field, at `create_conversation()` and at `run_child()`'s
  inline `Conversation` construction. Lives on `Conversation.context_state`.
  Never persisted — `conversation_store.py`'s `persist` seam only ever
  carries `messages` (CONV-08's own named, accepted limitation); this
  inherits that boundary rather than reopening it.
- `after_response(state: ContextState, usage: model_access.Usage) -> ContextState` —
  pure: `replace(state, total_prompt_tokens=state.total_prompt_tokens + usage.prompt_tokens, total_completion_tokens=...)`.
- `after_tool_result(state: ContextState, result: Message) -> Message` —
  identity today. Real call site: the TOOL_ROUND tool-result append in
  `run_turn`, immediately before `Message(role="tool", tool_call_id=tc["id"], content=result_text)`.
- `turn_complete(state: ContextState, history: tuple[Message, ...], system_prompt: str) -> tuple[ContextState, str | None]` —
  identity/`None` today. Replaces the removed `compress` callback 1:1 at
  its one call site — the `except ContextOverflow` branch in `run_turn`.
- `CacheHint` (frozen dataclass): `stable_prefix_len: int`, `trailing_marks: int`.
- `before_send(history: tuple[Message, ...], stable_prompt_len: int) -> CacheHint` —
  read-only against `ContextState` per B2 (takes none). **Corrected twice
  during build** (build-stage's own "if the plan turns out wrong, say so"):
  (1) this section originally also listed a `context_window_size: int`
  parameter, per B2's note that the checkpoint may consult it. Dropped —
  nothing in this item's real policy consumes it, and
  `model_access.context_window()` only resolves the one model this project
  has actually wired; calling it unconditionally for every turn raised
  `KeyError` for every other provider/model, including every test double in
  this codebase. (2) this section originally wrapped the return in a
  `BeforeSendResult` dataclass carrying `history`/`tool_specs` fields
  alongside `cache_hint`, matching B2's note that before-send may also
  reshape history and contribute tool schemas. Self-check (a `/simplify`
  pass, two independent review angles, plus this session's own
  `/ponytail-review`) converged on the same finding: neither field is read
  anywhere, since real compaction and CONTEXT-contributed tools are both
  non-goals of this item. Collapsed to a bare `CacheHint` return; either
  follow-up item widens the return type when it has something real to put
  in it — an addition, not a second rewrite of this signature. Real
  behavior here: always
  returns `CacheHint(stable_prefix_len=stable_prompt_len, trailing_marks=trailing_marks_from_config())`.
  `trailing_marks_from_config()` reads `SADANA_CONTEXT_CACHE_TRAILING_MARKS`
  (default 1) via `config.env_int` — CONTEXT's entire real contribution to
  the cache-boundary decision is *whether and how much*, not *how it lands
  on the wire*; see Rejected alternatives for why the wire-format half lives
  in `model_access.py` instead.

`Conversation` gains two fields: `stable_prompt_len: int` and
`context_state: ContextState`. Both are set once, at construction, and never
updated by anything but `after_response`/`turn_complete`'s returned values
(`context_state`) — `stable_prompt_len` is never updated at all in this item
(see Open questions). `create_conversation()` sets it to
`len(recipe.stable_prompt)`; `run_child()` sets it to `len(stable_prompt)`,
its own existing caller-supplied argument.

`model_access.py` gains one pure function, an addition to its already-named
set (`send()`, `context_window()`), not a change to either:

- `mark_cache_boundary(messages: tuple[dict, ...], hint: context.CacheHint) -> tuple[dict, ...]` —
  applies an OpenRouter-envelope-shaped `cache_control` marker
  (`{"type": "ephemeral"}`) to a content-part dict, never top-level, and
  never on a message whose content is empty (a marker there is silently
  dropped on OpenRouter's route and would waste it). Marks: the system
  message's stable prefix (`content[:hint.stable_prefix_len]`, split into a
  two-part `content` list exactly when the suffix is non-empty, matching
  hermes's own reason for that special case — an empty-suffix split puts an
  empty text block on the wire, which the API rejects) and the last
  `hint.trailing_marks` eligible non-system messages (skipping only empty
  content — sadana marks at most two spots total, never contending for
  Anthropic's 4-marker budget the way hermes's tool-schema-competing,
  4-provider layout does, so the fuller endpoint-scan/budget arithmetic has
  nothing to arbitrate here).

`conversation.py` changes:

- `run_turn`/`take_turn`/`run_child` drop the `compress: Callable[...]`
  parameter entirely (requirement 2). `run_turn` calls `context.turn_complete`
  directly at its one call site. `take_turn` threads `conversation.context_state`
  through and folds the returned state back via `replace()` — the same
  shape it already folds `iteration_budget` back.
- `complete()` gains `cache_hint: context.CacheHint | None = None`. When
  set, it applies `model_access.mark_cache_boundary` to `request_messages`
  before constructing `Request`. `run_turn` computes the hint once per turn
  (`context.before_send`, using `conversation.stable_prompt_len` and
  `model_access.context_window(provider, model)`) and passes it to every
  `complete()` call made that turn (MODEL_CALL and the EPILOGUE summary
  call).
- The TOOL_ROUND tool-result append calls `context.after_tool_result` first
  (identity today).
- After each successful `Completion`, `context.after_response` folds usage
  into a running value threaded through `run_turn` the same way
  `usage_total` already is, returned as a fifth value alongside
  `(result, messages, iteration_budget, system_prompt)`, and folded into
  `Conversation.context_state` by `take_turn`.

Every real caller (`eval_harness.py`, `prove_conversation_e2e.py`,
`tests/unit/test_conversation.py`, `tests/unit/test_conversation_store.py`)
drops its no-op `compress` argument; `create_conversation()`/`run_child()`
call sites gain the two new fields — mechanical, the same shape every prior
new `Conversation` field already got.

### Applied policies

- **testing-conventions**: new tests in `tests/unit/test_context.py`
  (module-named, per convention). `mark_cache_boundary` is tested against
  fixture dicts only — never a real request, since the unit tier forbids
  network and the model API. The one live proof runs through
  `prove_conversation_e2e.py`'s existing pattern: a script outside
  `make test`, run once, its output pasted as Deploy-stage Evidence, per
  CLAUDE.md's own rule — not a relaxation of the unit suite's network ban.
- No `project-structure` or `reference-lookup` skill file exists under
  `.claude/skills/` in this repo (confirmed: only `build-skill`,
  `deploy-skill`, `design-skill`, `plan-skill`, `testing-conventions` are
  installed) — design-skill's own procedure names both, but neither is
  installed here, so this section says so explicitly rather than silently
  skipping it. Conformance in their absence follows the concrete precedent
  `config.py`/`model_access.py` already set (one flat module per block) and
  CLAUDE.md's own reference-corpus methodology section, both cited inline
  above.

## Interface

**CONVERSATION → CONTEXT**, all four checkpoints, data in/data out, never a
shared reference (per B2):

- `before_send(history, stable_prompt_len) -> CacheHint`
- `after_response(state, usage) -> ContextState` (write)
- `after_tool_result(state, result_message) -> Message` (identity today)
- `turn_complete(state, history, system_prompt) -> (ContextState, str | None)`
  (write; `None` today carries the same meaning the removed `compress`
  callback's `None` already had: "not compressed")

**CONVERSATION → MODEL-ACCESS**, new addition: `mark_cache_boundary(messages, hint) -> messages` —
pure, no network, called from `complete()` before `Request` is built.

**`Conversation`** gains: `stable_prompt_len: int`, `context_state: ContextState`.
Both set once at construction; `context_state` updated only via
`after_response`/`turn_complete`'s returned values, matching B2's write
rule.

## Acceptance criteria

- [ ] `context.py` exists with all four checkpoint functions, each
      honoring the read/write rule B2 names.
- [ ] `compress: Callable[...]` no longer appears anywhere in
      `conversation.py`'s public signatures or any of its callers.
- [ ] A live round trip (`prove_conversation_e2e.py` or an equivalent
      script) shows an actual OpenRouter request body carrying
      `cache_control` on the system message's stable prefix and on the
      latest eligible message.
- [ ] `ContextState.total_prompt_tokens`/`total_completion_tokens` grow
      turn over turn across a multi-turn conversation and start at zero
      for a freshly created one.
- [ ] `after_tool_result`/`turn_complete` are real call sites (not absent,
      not a caller-injected stub) that are behaviorally a no-op, provable
      by a test that a compression-triggering `ContextOverflow` still
      exits `CONTEXT_OVERFLOW_UNHANDLED` exactly as it does today.
- [ ] `mark_cache_boundary` never emits a `cache_control` marker on
      content a provider's route would silently drop it from (empty
      content, empty `role:tool`) — the one `_can_carry_marker` check this
      item actually needs, ported at the scale this item needs it.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

Real compaction/summarization (turn-complete's real behavior). Real
result-spilling (after-tool-result's real behavior). Any multi-provider
cache-marker branching (native Anthropic / LiteLLM / Alibaba TTL clamps).
A process-global stable-prefix registry for repeated webhook/cron-style
invocation payloads. EXECUTION's, SESSION-STORE's, or LIFECYCLE's own
design. Extending `context_window()`'s static table beyond the one model
already wired.

## Open questions

Whether compaction, once built in a follow-up item, needs to also update
or invalidate `stable_prompt_len` — today it is set once and never touched
again, which is only safe because `turn_complete` stays a no-op in this
item (a real compaction rewriting `system_prompt` wholesale could silently
leave `stable_prompt_len` pointing at the wrong boundary). Belongs to that
follow-up item's own design stage, not this one; named here so it isn't
rediscovered as a bug later.

## Rejected alternatives

Keeping `compress` as an injected callable and simply changing what every
caller passes in, instead of removing the parameter: rejected. intent.md's
own problem statement is that an *injected* seam is exactly the pattern
that let real behavior get silently left out for this long; removing it in
favor of a direct, real function call is the one behavior change this item
exists to make, not an implementation detail to skip past.

Storing the literal `stable_prompt` string on `Conversation` instead of its
length: rejected as one avoidable redundant copy — `system_prompt[:stable_prompt_len]`
already recovers it, and `Conversation`'s existing `defer_invalidation`
design (C8) already goes out of its way to avoid holding template data
twice.

Putting `mark_cache_boundary` in `context.py` instead of `model_access.py`:
considered, since B2 names CONTEXT as the owner of "cache-boundary hints."
Rejected because the function's actual content — which provider route
drops a marker from where, and what shape the wire format needs — is
provider-transport knowledge, the same kind of fact B2's own Design section
says only MODEL-ACCESS should hold ("MODEL-ACCESS knows which errors are
transient and how many attempts a provider deserves"). CONTEXT decides
*whether and how much*; MODEL-ACCESS decides *how it looks on the wire* —
the same split B2 already draws between an outcome (CONVERSATION's to act
on) and its classification (MODEL-ACCESS's own job). This is the one design
decision in this spec broad enough to bind future work — see the note
below the Concerns section.

A configurable/pluggable cache-marking strategy object: rejected outright —
no second policy and no second provider exist to justify one;
`SADANA_CONTEXT_CACHE_TRAILING_MARKS` as one config integer is the entire
adjustable surface this item needs, addable to later without a redesign.

Deriving `ContextState.total_prompt_tokens` by summing usage off some other
existing structure instead of storing a running total: rejected — no
`Message` carries a usage figure, so there is nothing to derive it from. A
stored running total is B2's own explicit call on this state
("necessary... cannot be derived fresh from the message history alone"),
inherited here rather than re-argued.

Porting any part of `prompt_cache_scope.py` (the rotation-stable cache-key
lineage walk) "just in case": rejected outright, not narrowed — the
specific bug it fixes (a cache key fragmenting on every rotation) cannot
occur against a conversation key that is already rotation-stable by
construction. Porting a fix for a bug the design already prevents would be
importing dead code with a live-looking API.

## Concerns

**Guideline 2 (reduce the number of bets) vs. guideline 3 (least
step-cost) is the sharpest tension in this design, and it is real, not
decorative.** Removing `compress` from every signature and adding two new
`Conversation` fields touches four already-merged blocks (C6 model_access,
C7 turn-loop, C8 conversation-aggregate, C9 child-conversation) and every
real caller of them — a wide blast radius for one work item. The narrower
alternative (leave `compress` as an injected callable, just point every
caller at real functions) is a smaller diff and catches the same immediate
scenario. I judge the wider diff the right side of the tradeoff because it
is a mechanical, same-shape migration — every call site already threads
`iteration_budget`/`system_prompt` this same way — rather than new design,
and because leaving the injected seam in place preserves the exact failure
mode intent.md names as the problem (a caller can always quietly go back to
a no-op). A reviewer should look hardest here: this is the one place the
two guidelines pull in different directions, and step-cost won.

**No real consumer reads `ContextState.total_prompt_tokens` yet.**
OBSERVABILITY is an unbuilt block. This is accepted, not resolved:
intent.md explicitly chose this checkpoint as one of the two "real" ones
(cheapest — the data already exists in every `Completion`), overriding the
plain YAGNI reading guideline 4 would otherwise favor on its own. Flagging
this so a future reviewer does not mistake a deliberate, discussed choice
for an oversight.

**`stable_prompt_len`'s safety depends on `turn_complete` staying a
no-op**, a dependency nothing in the code enforces today beyond this
document and the Open question above. If a future item makes `turn_complete`
real without also revisiting this field, a rotated prompt could silently
carry a stale, wrong boundary — worth a code comment at the field's
definition pointing back here, not left as a fact only this file states.

**A candidate CLAUDE.md-level rule, surfaced by the `mark_cache_boundary`
placement decision above:** *provider-specific wire-format knowledge — what
a given transport's request shape accepts or silently drops — lives in
MODEL-ACCESS, never in CONTEXT or CONVERSATION.* This binds more than this
one work item (the next provider this project wires, or the next block
that wants to shape a request, would otherwise have to rediscover this
split from scratch) — flagged for the user to approve as a CLAUDE.md
amendment before build, per design-skill's own instruction, rather than
folded in silently.
