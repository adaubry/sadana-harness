# Spec: The conversation cycle — CONTEXT, MODEL-ACCESS, CONVERSATION as one contract

Intent: docs/tasks/B2-cycle-contract/intent.md

## Requirements

1. MODEL-ACCESS presents exactly one interface — a small, closed set of
   named operations — and every caller, including a future CONTEXT
   compaction call, goes through it. No caller reaches into MODEL-ACCESS's
   internals (provider selection, credentials, transport).
2. There is exactly one lifecycle interface for CONTEXT, called only by
   CONVERSATION, at a small named set of checkpoints. CONTEXT never reaches
   into CONVERSATION's loop state directly.
3. For each boundary (CONVERSATION↔MODEL-ACCESS, CONVERSATION↔CONTEXT), the
   contract names what data crosses in each direction, as inputs and return
   values — never as a mutation of state shared between the two sides.
4. The contract names the point where control passes to EXECUTION for a tool
   call and what returns from it, without designing EXECUTION's own
   interface.
5. The contract names a closed set of outcomes MODEL-ACCESS can hand back on
   failure, and which block is responsible for acting on each one.
6. The contract names a point in the cycle where a resumability checkpoint
   would be written, without designing SESSION-STORE.
7. Everything above is stated as named operations and data shapes — not as
   concrete classes, and not as anything copied from hermes's code.
8. Nothing here designs EXECUTION, SESSION-STORE, or LIFECYCLE. This work
   item only names the points where the cycle touches them.

## What the reference corpus showed

Two research passes read hermes-agent's CONTEXT, MODEL-ACCESS and
CONVERSATION production code — first a sample of the files closest to the
cycle, then every remaining production file in those three blocks, to
verify the sample and map actual file-to-file relationships. Findings, at
the decision level:

**The coupling is real, not just measurement noise.** `conversation_loop.py`
directly imports five separate MODEL-ACCESS files and six separate CONTEXT
files — narrow utility calls that bypass hermes's one clean interface
(`ContextEngine`, a lifecycle ABC with `compress()` / `select_context()` /
`on_turn_complete()` hooks) rather than going through it. MODEL-ACCESS turns
out to have at least three independent callers in production: the main
loop, CONTEXT's own compaction engine (which makes its own summarization
model call, entirely outside the loop), and a separate plugin-facing
facade. None of the three coordinate through a shared seam. CONTEXT can
also contribute tool schemas into what the model sees, so tool-schema
assembly is a three-way merge (EXECUTION's tools, CONTEXT-engine tools, MCP
tools), not the two-way CONVERSATION/EXECUTION handoff the intent assumed
going in. (MCP-registered tools, in hermes, arrive through the same
declarative tool registry every other EXECUTION-side tool registers
through — so for this contract they are EXECUTION's concern to surface, not
a third named contributor alongside it.)

**Failure handling is real design, layered onto incidents, one at a time.**
Every mechanism found — a dependency-free per-attempt retry-state value
kept apart from loop-control counters, an iteration budget with refund, a
unified deadline (explicitly built to replace six separate ad hoc timeout
mechanisms), an empty-response guard, a typed error classifier — traces to
a named incident number in the source. One classification mechanism
(inspecting exception traceback frame module names to guess retryability)
caused two of those incidents itself — a transient API error misclassified
as a non-retryable bug, and later an unbounded retry loop that pegged a CPU
core and overwrote days of logs within minutes. Nothing in this cycle reads
as designed in advance; `conversation_loop.py` is under an active, named
decomposition effort, and the files being pulled out of it say in their own
docstrings that the move is behavior-neutral — the coupling itself hasn't
been redesigned, only relocated.

**Decisions adopted from this reading:** a lifecycle interface as the one
seam a block presents (hermes's own `ContextEngine`, at its best, is exactly
this — the problem was that it was optional, not that it was wrong);
separating one-shot per-attempt recovery state from loop-control counters;
returning a *result*, not raising and pattern-matching an exception, for a
classified failure; persisting a resumability point before running a
side-effecting tool call; distinguishing tool results that have no lasting
effect (safe to drop on resume) from ones that mutated something (need a
landed-proof check before retrying).

**Decisions declined, with the specific cost named:** letting more than one
block reach into another's internals directly, the pattern responsible for
`conversation_loop.py` importing eleven other files' internals piecemeal;
classifying retryability by inspecting exception call-stack frames, which
produced two incidents in hermes directly; returning control to a caller
through side-channel mutation of shared state (flags set on a shared
object) rather than a return value, which is harder to unit-test in
isolation and is exactly the kind of implicit state guideline 4 below
argues against; adding a new bespoke timeout or retry mechanism per
incident instead of one mechanism designed to be extended.

## Design

**One entry point per block, not many.** MODEL-ACCESS exposes a single
call — conceptually `send(request) -> Outcome` — and that is the only way
anything in sadana-harness talks to a model. `request` carries the message
history as data (a value, not a reference into CONVERSATION's live state),
the merged tool schemas, and provider/model selection sourced from CONFIG.
`Outcome` is either a successful response (assistant message, any tool
calls it names, usage) or one of the closed failure cases in `## Interface`
below. CONTEXT's compaction path, if it ever needs to summarize via a model
call, is just another caller of this same entry point — not a second door
into MODEL-ACCESS's internals. This directly answers what hermes's own
history got wrong: not that CONTEXT calling MODEL-ACCESS is bad, but that
there was no single seam for it to call through. MODEL-ACCESS keeps nothing
between calls: `send()` is a function of its arguments, and anything that
must survive from one attempt to the next travels in `request`. The only
fact it holds is the one it owns by definition — the active model's context
window, which it reports rather than accumulates.

**One lifecycle interface for CONTEXT**, called only by CONVERSATION, at
four checkpoints: before a request is sent (CONTEXT may return a compacted
or reshaped history, cache-boundary hints, and any tool schemas it wants to
contribute); after a response returns (CONTEXT updates whatever internal
accounting it keeps, using the real usage figures MODEL-ACCESS reported);
after a tool result is appended (CONTEXT may reshape or bound the result
for storage, e.g. spilling an oversized result to disk with an in-context
reference — a size/storage concern, distinct from CONVERSATION's own
resume-safety classification of the same result, which stays in
CONVERSATION); and at turn completion (CONTEXT decides and performs any
compression). CONTEXT never receives a reference to CONVERSATION's loop
state — every checkpoint takes plain data in and returns plain data out.

**CONTEXT keeps internal state, and this design names it instead of
hiding it.** Guideline 4 asks for an inventory of necessary mutable state,
not for none — and CONTEXT's own running account of usage and compression
history is necessary; it cannot be derived fresh from the message history
alone, because compression discards the data it would need to derive from.
That state has a declared lifetime: it is created when a conversation
begins — the whole exchange, not a single turn — lives across every turn in
it, and is discarded when the conversation ends. It is not itself
persisted; surviving a process restart is SESSION-STORE's concern, not this
state's. The lifetime has to be the conversation rather than the turn for
the same reason the state exists at all: a compression that happened three
turns ago is precisely the fact the message history no longer carries. Only
two of the four checkpoints may write to it: after-response, using the real
usage figures a completed exchange reported, and turn-complete, recording
that a compression happened. before-send is read-only against it — it may
consult the state to help decide what to compact, but never writes. That
one rule resolves the two failure shapes an undeclared stateful sequence
would otherwise raise: an aborted turn cannot drift the accounting,
because only checkpoints tied to an exchange that actually completed write
to it; and a retry after `needs-context-compression`, which calls
before-send twice in one turn, cannot double-count anything, because
before-send never writes.

**Where this would live**, following the existing convention in
`src/sadana/config.py` (one flat module per block, no shared growing
object, a block imports another block's module directly rather than a
common settings blob): `src/sadana/model_access.py` exposing `send()` and
the `Outcome` shapes; `src/sadana/context.py` exposing the four checkpoint
functions and their data shapes; `src/sadana/conversation.py` owning the
loop itself, importing both of the above and, later, EXECUTION. None of
these three files exist yet — this section names where the next work item
would put them, not code to write now.

## Interface

**CONVERSATION → MODEL-ACCESS.** In: message history (data), merged tool
schemas, model/provider selection, and the attempt number for this
request — zero on a first attempt, otherwise whatever a prior retry handed
back. Out: `Outcome`, one of —
`response` (assistant message, tool calls, usage); `retry` (the same
request is safe to attempt again, e.g. a transient transport error);
`needs-credential-or-provider-change` (this attempt cannot succeed as
configured); `needs-context-compression` (the request is too large; the
caller should ask CONTEXT to compress and retry); `degenerate` (a response
that technically succeeded but carries no usable content); `abort` (this
attempt should not be retried at all). CONVERSATION is the only block that
acts on an `Outcome` — MODEL-ACCESS never calls CONTEXT itself, even for
`needs-context-compression`; it only names the outcome and hands it back.

`retry` is bounded, and the bound is carried as data rather than held as
hidden state. MODEL-ACCESS owns the cap — a small fixed number it states as
part of its interface — and compares it against the attempt number arriving
in `request`. Below the cap, a transient transport error returns `retry`
carrying the incremented attempt number for the caller to pass back
unchanged; at the cap it returns `abort`, and no further attempt is
offered. CONVERSATION's only job is to pass the number through.

This keeps ownership where it belongs — MODEL-ACCESS knows which errors are
transient and how many attempts a provider deserves — without a counter
whose lifetime nothing declares, and it keeps `send()` a function of its
arguments, which is what makes it stub-testable under `testing-conventions`.
It closes off, by construction, the failure hermes hit directly: an
unbounded retry loop that pegged a CPU core and overwrote days of rotated
logs within minutes.

MODEL-ACCESS is also the single owner of one fact every caller needs but no
caller should duplicate: the active model's context window size. It
exposes that alongside `send()` — not only reactively, after
`needs-context-compression` comes back once a request already proved too
large, but as a fact CONTEXT can ask for before ever building a request.
Both sides then compact against the same number MODEL-ACCESS would enforce
anyway, instead of each guessing at it, or both reaching into CONFIG for a
value neither of them owns — the same shared-fact coupling this work item
exists to prevent, just arriving through configuration instead of an
import, and harder to see for it.

**CONVERSATION → CONTEXT.** In, at each checkpoint: the current message
history as data, plus whatever is specific to that checkpoint (the
about-to-be-sent request and the active model's context window size, from
MODEL-ACCESS, at the before-send checkpoint; the response and its usage at
the after-response checkpoint; the newly appended tool-result message at
the after-tool-result checkpoint). Out: a value for that checkpoint only —
a possibly-reshaped history and contributed tool schemas before send;
nothing required after response, beyond CONTEXT's own internal bookkeeping
(see `## Design`); a possibly-reshaped tool-result message after a tool
result; a possibly-compressed history at turn completion. CONTEXT never
mutates CONVERSATION's history in place — every checkpoint takes a value
and returns a value. Being read-only against CONTEXT's own state (see
`## Design`), before-send may be called more than once in one turn — e.g.
once, then again after a `needs-context-compression` retry — without side
effects.

**The tool-call plug point.** When `Outcome.response` names one or more
tool calls: CONVERSATION validates them against the merged schema set
(EXECUTION's registered tools plus whatever CONTEXT contributed at the
before-send checkpoint); classifies each by expected effect (no lasting
effect, safe to drop if interrupted, versus mutating, which needs a
landed-proof check before being treated as done); writes a resumability
checkpoint — the moment SESSION-STORE, once it exists, hooks in; then
hands the validated calls to EXECUTION's single dispatch entry point
(assumed to exist, not designed here) and gets back, as a return value, one
result per call. Nothing about running the tool calls or their results
reaches CONVERSATION through shared-state mutation; the dispatch call
returns data, and CONVERSATION appends it to the history and re-enters the
CONTEXT after-tool-result checkpoint before looping.

## Acceptance criteria

- [ ] MODEL-ACCESS's interface is a closed set of named operations, listed
      in full, and every caller mentioned in this spec (CONVERSATION,
      CONTEXT's compaction path) reaches it only through that set.
- [ ] Exactly one interface is named for CONTEXT, as a fixed, small set of
      checkpoints — not an open-ended set a caller can extend ad hoc.
- [ ] For every boundary named, what crosses it is described as data in,
      data out — no shared mutable object appears in either direction.
- [ ] The tool-call plug point names what CONVERSATION hands to EXECUTION
      and what it gets back, without specifying how EXECUTION runs a tool.
- [ ] The failure outcomes MODEL-ACCESS can return are enumerated as a
      closed set, and each one names which block acts on it.
- [ ] A resumability checkpoint's position in the cycle is named, without
      specifying how it is persisted.
- [ ] Any mutable state a block keeps behind its interface is named
      explicitly, with a declared lifetime and a stated rule for which
      operations may write to it — none is left implicit.
- [ ] The `retry` outcome names a bound, and the model's context window
      size has exactly one named owner that both sides read.
- [ ] A person could start implementing any one of CONTEXT, MODEL-ACCESS,
      or CONVERSATION against the other two as stubs, using only this
      document — no further design decision is required to begin.

## Non-goals

EXECUTION's own interface and implementation (tool dispatch, sandboxing) —
only the plug point where CONVERSATION hands off to it. SESSION-STORE's own
design — only the point in the cycle where a checkpoint would be written.
LIFECYCLE's own design — this cycle is a unit LIFECYCLE will call
repeatedly; how LIFECYCLE drives it is not decided here. MODEL-ACCESS's own
provider-selection and credential-routing internals (hermes's equivalent
sits behind its single request entry point, and so does ours) — only the
one boundary call it presents. A plugin system for swapping CONTEXT's or
MODEL-ACCESS's implementation — no consumer exists yet to justify one; the
interfaces are shaped as named operations specifically so that swap is not
foreclosed later. How MCP-registered tools reach EXECUTION's own tool
registry — assumed to be EXECUTION's concern, entering through the same
declarative registration point every other tool uses, not a third named
contributor to the two-way schema merge (EXECUTION, CONTEXT) this contract
defines. Any touchpoint with LEARNING — hermes's own CONVERSATION code
reaches into LEARNING-tier modules directly despite the tier ordering,
which is worth naming as a real future seam, but nothing here designs it.

## Open questions

Intent's open question — whether SESSION-STORE or LIFECYCLE depend on this
design — is now partly answered: both do, by construction (LIFECYCLE calls
the cycle as a unit; SESSION-STORE implements the checkpoint this spec
reserves a place for). What isn't resolved is whether that checkpoint needs
to capture anything beyond "a tool call is about to run with these
arguments" — that depends on decisions SESSION-STORE's own design stage
hasn't made yet, and belongs there.

## Rejected alternatives

Hermes's actual shape — any block reaching directly into another's
internals when convenient, with no single seam — was rejected outright: it
is the pattern directly responsible for the coupling this whole work item
exists to avoid, and hermes's own multi-year decomposition effort is
evidence it costs more than it saves, not less.

Returning control from a tool-call dispatch (or from MODEL-ACCESS) through
side-channel mutation of a shared object, the way hermes's loop checks
flags set on a shared `agent` object after a call, was rejected in favor of
plain return values. Cost of the alternative: it is materially harder to
unit-test a component whose effect is "sets a flag on an object it was
handed" than one whose effect is "returns a value" — and this project's own
testing convention restricts a unit test to the module under test and its
declared dependencies, which a side-channel mutation strains.

Classifying MODEL-ACCESS failures by inspecting the exception's call stack
(which module frame raised it) was rejected, not on stylistic grounds: it
produced two separate incidents in hermes directly, one where a transient
error was misclassified as non-retryable and one where an unbounded retry
loop pegged a CPU core. A closed, named set of outcomes returned instead of
an exception hierarchy to pattern-match avoids the whole class.

A plugin system letting CONTEXT's or MODEL-ACCESS's implementation be
swapped at runtime was considered and rejected for now — sadana-harness is
one step past its first built block, with no second implementation of
either to justify one. The interfaces here are still shaped as a fixed set
of named operations rather than a concrete class hardwired into the loop,
so introducing that later is an addition, not a rewrite.

Designing SESSION-STORE's or EXECUTION's interface now, since the tool-call
plug point touches both, was considered and rejected: intent scoped this
work item to the three-block cycle specifically, and stubbing both as
opaque dependencies was confirmed as sufficient during planning.

An attempt counter held inside MODEL-ACCESS between calls was considered
and rejected. It reads simpler at the call site, but it is undeclared
cross-call state in a block whose interface is otherwise a pure function of
its arguments — the same shape this spec spends a section naming explicitly
for CONTEXT — and it would make retry either unobservable to the caller or
dependent on state no caller can see. Carrying the attempt number in the
request costs one field and keeps `send()` testable against a stub, which
this project's testing convention requires.

## Concerns

The sharpest tension in this design is between reducing the number of bets
and catching the coupling scenario at the least step-cost. Defining two new
interface layers before either CONTEXT or MODEL-ACCESS has a body is itself
an added step with no caller yet. I judge this the right side of the
tradeoff — CLAUDE.md's own position is that a callerless interface is only
ever this cheap before something is built against it — but the risk is
real: if either interface's shape turns out wrong once CONTEXT or
MODEL-ACCESS is actually implemented, revising it costs the same kind of
rework this work item exists to prevent, one level up. The mitigation is
that both interfaces are described here as behavioral contracts — named
operations and data shapes — closer to hermes's own least-regretted piece
(`ContextEngine`) than to a framework, specifically so a wrong detail is
cheap to correct without reopening the whole shape.

Second: `testing-conventions` restricts a unit test to the module under
test and its dependencies, with no network and no real model calls. MODEL-
ACCESS's whole purpose is to talk to a model, which means the `send()` seam
this spec proposes has to be narrow and stub-friendly by construction, or
CONVERSATION's tests cannot honor that rule without reaching through to a
real provider. That's not a contradiction, but it is a hard requirement on
the eventual build that this design stage is surfacing rather than
resolving.

No `project-structure` or `reference-lookup` skill exists yet in
`.claude/skills/` — CLAUDE.md's own "Layout and ownership" and "The
reference corpus" sections were used in their place, and no tension was
found between them and this design. No security, brand, or UX skill
applies: this work item has no user-facing surface and moves no secret or
credential data itself.

One process finding, corrected after review: `agent/turn_liveness.py` first
read as a missing file, and is not one. `../hermes-agent` is checked out at
`29112bef` (2026-08-31), 453 commits behind the `5d4aa4fc` (2026-09-02)
revision the reference index was built from; `git ls-tree -r 5d4aa4fc` shows
the file present there and `git log --diff-filter=D` finds no deletion. The
cause is clone drift, not an unreliable index. Updating the clone to the
pinned revision is step 1's own requirement and is being handled outside
this work item; until a clone is confirmed pinned, a design or build stage
should verify a listed file exists before reasoning from its name, since the
drift can hide files in either direction.
