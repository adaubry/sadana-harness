# Spec: A conversation's history that cannot become malformed

Intent: docs/tasks/C2-keys-transcript/intent.md

## Requirements

1. A conversation is identified by a natural, human-readable key (a caller-supplied string), not an internal pointer. Traces to intent's "Proposed outcome" and CLAUDE.md's names-not-pointers rule.
2. Each message in a conversation's history is likewise addressable by a stable key derived from the conversation's key plus its position. Traces to intent's "Proposed outcome."
3. Appending a message that would leave the history unable to be sent to a model provider — a tool result with no matching outstanding call, or anything other than a tool result while a prior call is still unanswered — is refused outright, not silently accepted. Traces to intent's "Problem" (a provider flatly refuses a broken history) and "Proposed outcome" (a bad append is refused).
4. Exactly one repair operation exists, and it is the only mutation permitted on a conversation's history before a turn begins: it closes every call left unanswered by an interruption, and nothing else. Traces to intent's "Proposed outcome" (the one automatic repair step) and "Constraints" (this item only enforces the one invariant already settled).
5. Everything in this work item is provable without the turn loop, tool dispatch, or a model provider existing — fake messages only. Traces to intent's "Constraints."
6. The history lives in memory, for one running process, for this work item. No durability claim is made or implied. Traces to intent's "Constraints."

## Design

**Where this lives.** `src/sadana/conversation.py` — new file, matching the
destination B2's spec already named for the whole CONVERSATION block ("owning
the loop itself, importing both of the above and, later, EXECUTION" —
`docs/tasks/B2-cycle-contract/spec.md`). This work item is the first content
in that file; later C-series work items (tool surface, budgets, the loop
itself) add to the same file rather than starting new ones, per the flat-
module-per-block convention `config.py` and `model_access.py` already
establish. `docs/reference/conversation_block_blueprint.md` §3 proposes a
`conversation/` *package* of nine files instead — declined; see Rejected
alternatives.

**Types.**

- `ConversationKey = str` — a plain alias, not a wrapper class. A caller
  mints it (e.g. `"support/ticket-4821"`); this work item does not generate
  or validate one, and does not enforce it is unique — that needs a store,
  which is CONV-08's job, not this one's. Noted in Concerns.
- `MessageKey` — frozen dataclass, `conversation: ConversationKey`,
  `msg_seq: int`. `msg_seq` is a message's position in its conversation's
  history; nothing stores it independently (see "Sequences are derived,"
  Design guideline 4 below).
- `Message` — frozen dataclass: `role: Literal["user", "assistant", "tool"]`,
  `content: str | None = None`, `tool_calls: tuple[dict, ...] = ()`,
  `tool_call_id: str | None = None`. `tool_calls` reuses the exact shape
  `model_access.Response.tool_calls` already returns (`tuple[dict, ...]`,
  each dict carrying at least `id` and `name`) rather than inventing a
  second `ToolCall` type for the same data — see Rejected alternatives.
- `TranscriptInvariantError(Exception)` — raised by `append`; the message
  names which rule was violated and with which id, so a caller never has to
  read this module's source to understand a failure (testing-conventions'
  "never read source in a test" cuts the other way too: the error text has
  to carry the information a test would otherwise go looking for).

**`TurnKey` is not defined here.** The blueprint's §4.1 names it
`(ConversationKey, turn_seq)`, but nothing in this work item's two invariants
needs a turn boundary — pairing and role-alternation are checked purely from
message order. Introducing it now would be state with no reader yet
(Design guideline 2 and 4). CONV-05 (the turn loop) is where a turn boundary
first has a caller, and defines `TurnKey` then.

**Functions** (all pure — a value in, a value or an exception out; nothing
in this module holds a live conversation itself):

- `pending_tool_call_ids(messages: tuple[Message, ...]) -> frozenset[str]`
  — derived, not stored. Finds the most recent `assistant` message carrying
  `tool_calls`; returns whichever of its ids have no matching `tool` message
  after it in `messages`. Empty if the last such assistant message's calls
  are all answered, or if there has never been one.
- `append(conversation: ConversationKey, messages: tuple[Message, ...], message: Message) -> tuple[tuple[Message, ...], MessageKey]`
  — returns a new tuple with `message` on the end, and the `MessageKey` it
  was assigned. Raises `TranscriptInvariantError`, leaving `messages`
  untouched, when:
  - `pending_tool_call_ids(messages)` is non-empty and `message` is not a
    `tool` message whose `tool_call_id` is in that pending set;
  - `pending_tool_call_ids(messages)` is empty and `message` is a `tool`
    message (nothing outstanding for it to answer).
- `repair(messages: tuple[Message, ...]) -> tuple[Message, ...]` — if
  `pending_tool_call_ids(messages)` is empty, returns `messages` unchanged.
  Otherwise appends one `tool` message per pending id, in the order those
  ids were declared on the assistant message, each with a fixed content
  marker (`"[repaired] interrupted before execution"`), and returns the
  result — after which `pending_tool_call_ids` on the result is empty. This
  is the one function intent.md's "Proposed outcome" calls "the one
  automatic, defined step" — it is never invoked from inside `append`; a
  future caller (CONV-05's `PROLOGUE`) invokes it explicitly, once, before a
  turn starts. Making it implicit inside `append` would hide exactly the
  invariant this work item exists to make visible.

**Sequences are derived, not stored.** `msg_seq` for a message is its index
in the tuple at the time it is read back — nothing assigns or increments a
counter. This removes an entire item from the mutable-state inventory (see
guideline 4 below): a stored counter can drift from the list it is supposed
to describe; a derived one cannot.

**What this module trusts and does not re-check.** `MODEL-ACCESS` (B2's
`Outcome.response`, this project's `send()`) is already the boundary
responsible for handing back `tool_calls` with non-empty, distinct ids —
B2's spec requires the provider port to synthesize an id if a provider omits
one. This module does not re-validate id emptiness or uniqueness within one
assistant message; doing so would duplicate a check the boundary already
makes, for no caller this work item has. If that assumption ever proves
false, the fix belongs in MODEL-ACCESS, not here.

## Interface

**In:** a `tuple[Message, ...]` (a conversation's history so far, or `()`
for a new conversation) and, for `append`, one new `Message` plus the
`ConversationKey` it belongs to.

**Out:** `append` returns `(tuple[Message, ...], MessageKey)` or raises
`TranscriptInvariantError`. `repair` returns `tuple[Message, ...]`, never
raises. `pending_tool_call_ids` returns `frozenset[str]`, never raises.

**Errors:** `TranscriptInvariantError` is the only exception type this
module defines or raises. It is always raised before any mutation is
observable — `append` either fully succeeds and returns the new tuple, or
raises and the caller's original `messages` value is exactly what it was
before the call (tuples are already immutable; there is nothing to roll
back).

## Acceptance criteria

- [ ] `append` raises `TranscriptInvariantError` for a `tool` message whose
      `tool_call_id` is not in the current pending set (including: no
      assistant message with `tool_calls` ever preceded it).
- [ ] `append` raises `TranscriptInvariantError` for a `user` or `assistant`
      message appended while any id from the preceding assistant message's
      `tool_calls` is still unpaired.
- [ ] `append` accepts an `assistant` message with `tool_calls=()` (a plain
      text turn) whenever nothing is pending.
- [ ] `append` accepts a `tool` message whose `tool_call_id` matches a
      pending id, and that id no longer appears in `pending_tool_call_ids`
      of the result.
- [ ] `repair` is a no-op — returns its input unchanged — when nothing is
      pending.
- [ ] `repair` appends exactly one `tool` message per pending id, in
      declaration order, and `pending_tool_call_ids` of the result is empty.
- [ ] Neither `append` nor `repair` mutates the `messages` tuple passed in;
      both return a new tuple, provable by identity-checking the input
      afterward.
- [ ] `tests/unit/test_conversation.py` touches no network, wall clock, real
      filesystem, or source text — only `Message`/`tool_calls` values it
      constructs itself.
- [ ] Every test in that file is provable with no turn loop, tool dispatch,
      or provider code — construct `Message` values by hand.

## Non-goals

- Durable storage of any kind (sqlite, disk, a database constraint on
  `ConversationKey`'s uniqueness) — CONV-08.
- `TurnKey`, turn numbering, or anything about where one turn ends and the
  next begins — CONV-05.
- Tool schema validation, the tool surface, or anything about which tool
  names are allowed — CONV-02.
- Re-deriving or re-validating what MODEL-ACCESS already guarantees about
  `tool_calls` shape (non-empty ids, parsed arguments).

## Rejected alternatives

**A `conversation/` package of nine files (blueprint §3), declined.** The
blueprint itself flags this layout as "adapt names to your tree," not a
requirement. B2's spec already fixed the convention for this project as one
flat module per block — `config.py`, `model_access.py` — specifically to
avoid the failure hermes's own decomposition still shows scars of (many
files reading and writing one implicit shared object). Splitting
CONVERSATION into nine files now, before nine work items' worth of content
exists to justify it, is also a bet this work item doesn't need to take
(Design guideline 2): one file that grows, versus nine files and an import
graph between them, decided on work item one of nine.

**A second `ToolCall` dataclass, declined.** `model_access.py` already
defines the wire shape MODEL-ACCESS hands back (`tuple[dict, ...]`). A
parallel `ToolCall` type here would mean either a conversion step at the
CONVERSATION/MODEL-ACCESS boundary that does nothing but change a type, or
two types drifting apart over time. Reusing the dict shape is one fewer bet
and one fewer thing to keep in sync.

**Copying hermes's `close_interrupted_tool_sequence` verbatim, declined —
audited and it solves a different problem.** The blueprint's §4.2 and §7
cite `agent/message_sanitization.py:296` as this work item's repair
function. Reading it (and every one of its six call sites in
`agent/conversation_loop.py`) shows it fires when an interactive `/stop`
lands while the transcript's last message is an already-*completed* `tool`
row with no assistant text yet closing the turn — it appends one synthetic
**assistant** message ("Operation interrupted.") so the next `user` message
doesn't follow a bare `tool` row. That is the mirror-image scenario to this
work item's: here, the gap left by an interruption is a `tool_call` an
assistant already asked for that never got *any* tool row at all (a crash
mid tool-round, before or between the per-result appends the design commits
to in `docs/reference/conversation_block_blueprint.md` §5.3). No function
in the corpus's CONVERSATION or CONTEXT files repairs that specific gap —
this work item's `repair` is original, built to the invariant hermes's
naming convention gestures at, not to hermes's code. Keeping this finding
here rather than silently "adopting" the citation is what
reference-corpus-audit is for.

**A stateful `Transcript` protocol (blueprint §4.2: `append`/`read`/
`last_seq` methods), declined for this work item.** The blueprint specifies
`Transcript` as an object a caller holds and calls methods on. This work
item instead exposes three plain functions over a `tuple[Message, ...]`
the caller already holds — no `read` (the tuple already is the read value;
wrapping it in a method returns the same thing with an extra call), no
`last_seq` (one-line, `len(messages) - 1`, not worth a wrapper), no class.
A `Protocol` earns its keep once a second implementation exists to abstract
over — CONV-08's sqlite-backed store is that second implementation, and is
where `Transcript` as a named interface first has two callers who need to
be interchangeable (Design guideline 2's caveat: don't foreclose the seam,
but don't build it before it has a consumer). Until then, this shape costs
nothing to wrap in a class later — CONV-08 can define `Transcript` as a
thin object whose methods call `append`/`repair` from this module — while
building the wrapper now would be state and API surface with exactly one
implementation and zero callers who need the abstraction.

**Storing `msg_seq` as a counter, declined.** A counter is state that must
be kept in lockstep with the list it describes and can drift (double-
incremented on a retried append, stale after a crash). The list's own
length already answers "how many messages, and in what order" — deriving
`msg_seq` from position removes a whole failure mode instead of guarding
against it.

## Concerns

**`ConversationKey` uniqueness is not enforced here, and CLAUDE.md's own
rule asks for a database constraint behind any long-lived natural key.**
This work item is explicitly in-memory only (intent.md's Constraints), so
there is no database to put the constraint in yet. The gap is real between
now and CONV-08: nothing today stops two callers from independently
starting a conversation under the same key and silently interleaving two
different histories under it. Flagging this rather than closing it — CON-08
is where the constraint belongs, and building a premature in-memory
uniqueness check here (a module-level dict of seen keys, with a lifetime
nothing declares) is exactly the kind of stored state Design guideline 4
argues against taking on before it has a real owner.

**Reject-at-append versus a separate validator, and why reject-at-append
costs less (Design guideline 3).** An uncaught malformed-history scenario
has three possible fixes: add a validation pass as a new step, make every
caller of `messages` heavier by re-checking pairing before use, or make the
one existing step — appending — harder to misuse by refusing a bad append
outright. This spec takes the third: no new step for any future caller to
remember to run, no repeated re-validation cost paid by every reader of the
history, and the failure is caught at the single point where it is
introduced rather than discovered later by whichever caller happens to look.
The cost is paid once, in `append`, forever.

**No unresolved policy conflict.** `testing-conventions` is the only policy
skill present that applies; this design does not need `reference-lookup` or
`project-structure` as skills because neither exists yet in this project —
stated explicitly rather than left silent. Nothing here contradicts
`testing-conventions`: every acceptance criterion above is phrased as a
relationship (pairing holds, a set becomes empty), not a snapshot, and
nothing requires source-reading, the network, the clock, or a location
outside `tmp`.
