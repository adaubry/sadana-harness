# Spec: A conversation becomes one continuing thing, not one call

Intent: docs/tasks/C8-conversation-aggregate/intent.md

## Requirements

1. A conversation, once created, can be asked more than one question; between
   questions it keeps its message history, its currently allowed actions, and
   how much of its allotment remains, without the caller re-supplying any of
   that by hand. Traces to intent's Proposed outcome, paragraph 1.
2. A running conversation's prompt cannot change out from under a question in
   flight; the byte-stability check C7 already built continues to guard every
   turn, and nothing here gives any path to bypass or weaken it. Traces to
   intent's Constraints ("the care this project already takes... holds here
   too").
3. The prompt is assembled from a fixed identity part and a per-conversation
   context part; the context part carries a caller-supplied message plus a
   list of currently recorded external procedures, even though nothing yet
   populates that list from a real discovery mechanism. Traces to intent's
   Changed during planning, first fork.
4. A change to the shared starting point a conversation was created from (its
   identity part, its recorded procedures, its available actions) can be
   recorded, and it is provable — not just believed — that a conversation
   already running from that starting point never sees it. Traces to intent's
   Proposed outcome, paragraph 2, sentence 1.
5. A new conversation created from the same starting point as a prior one,
   after such a change was recorded, provably reflects it. Traces to intent's
   Proposed outcome, paragraph 2, sentence 2.
6. Nothing here discovers, loads, mounts, or runs an outside procedure; a
   caller supplies, as plain data, whatever facts about one it wants
   recorded. Traces to intent's Constraints, bullet 1.
7. Shrinking an overly long conversation is still not built; this work item's
   only lever for a prompt to legitimately change during a conversation's
   life remains the one seam C7 already established for it. Traces to
   intent's Constraints, bullet 2.
8. Nothing here saves a conversation anywhere durable; everything
   constructed lives only in the process's memory. Traces to intent's
   Constraints, bullet 3.
9. Nothing here lets one conversation create another, and nothing here
   decides what happens when more than one conversation — or the same one
   from two places — is in progress at once. Traces to intent's Constraints,
   bullet 4.
10. An allotment already consumed is never quietly restored, and a model's
    own claims about what happened are still checked rather than trusted,
    now across repeated turns rather than a single call. Traces to intent's
    Constraints, bullet 5.

## Design

**Where this lives.** `src/sadana/conversation.py` — the sixth work item in
a row to extend it (C2 transcript, C3 tool surface, C4/C5 budgets, C6
provider port, C7 turn loop). Same reasoning C2's spec already gave and every
later one reaffirmed: one flat module per block, not blueprint §3's
nine-file package, because the failure mode that layout guards against
(files silently reading and writing one shared implicit object) is exactly
what this project's function-signature discipline already prevents another
way. The file is now past 870 lines and this work item adds roughly 150–200
more; flagged once, honestly, in Concerns rather than re-litigated — CONV-07
(child conversations) and CONV-08 (a durable store) are real candidates for
new files, because a store is a genuinely different concern from an
in-memory aggregate, not more of the same one.

**Learning from the reference (guideline 1).** Two files bear on this work
item; both audited, both declined for stated reasons — nothing here is
adopted verbatim.

- `agent/conversation_loop.py:916` `_restore_or_build_system_prompt` and its
  callee `tools/bot_mode_probe.py:418` `stored_prompt_capability_stale`:
  hermes re-derives a live "capability fingerprint" from disk state
  (skills directory, MCP config, SOUL.md, roster) on every turn, compares it
  to a stamp embedded in the *stored* prompt, and — on mismatch — rebuilds
  the prompt for the *same, continuing* session, logging a warning
  (`conversation_loop.py:960-964`, cited in the blueprint audit for a
  different line range). Declined for two concrete reasons, not preference:
  first, it applies a capability change to a session already in progress on
  its very next turn, which is precisely the silent-mid-conversation-drift
  hazard `docs/reference/conversation_block_blueprint.md` §4.3 was written
  to close (this project raises `PromptDriftError` instead of rebuilding);
  second, the mechanism is reached only through god-object session state
  (`agent._session_title_hint`, `agent._bot_mode_protocol`,
  `agent._session_db`) that blueprint's own §2.3 audit already told this
  project not to transplant. Nothing from either file is copied.
- `agent/agent_init.py:536-620`: already audited and rejected in C7's own
  spec and blueprint's appendix (the ~90-argument constructor). Re-checked
  here specifically for a reusable "starting point" object a session is
  built from, since that is what this work item needs — there isn't one.
  hermes doesn't need it: a session is built directly from the agent's live
  config each time, and capability drift is handled by the stale-fingerprint
  rebuild above (which this project has already declined) rather than by
  deferring a change to a genuinely *new* session. There is nothing to adopt
  here; the "starting point" concept below (`ConversationTemplate`) has no
  hermes analogue and is designed from blueprint §4.3's own prose plus
  Design guidelines 2 and 4.

**CLAUDE.md's own line for this exact contract.** *"When it comes to the
system prompt, enforce a state contract that makes the principle 'the system
prompt is byte-stable for the life of a conversation' directly checkable,
maintaining compression as the single named exception and using deferred
invalidation as the default for any action that mutates prompt state."* This
work item is where that line gets a body, not just blueprint §4.3's prose.

**The central design choice, and which of the three moves it makes
(guideline 3).** The scenario to catch: a change recorded against a
conversation's starting point must never reach a conversation already
running from it. Three ways to catch that:

- *Add a step* — give `Conversation` a live reference to its
  `ConversationTemplate` and add a guard in `take_turn` that ignores any
  pending change on it. Extra code, an extra thing to test, and a guard that
  can be gotten wrong or forgotten at a second call site later.
- *Make a step heavier* — recompute the composed prompt fresh from the live
  template on every turn (hermes's own approach, audited and declined
  above). Heavier per turn, and reintroduces exactly the rebuild-on-drift
  hazard §4.3 exists to close.
- *Structural removal* — give `Conversation` no reference to
  `ConversationTemplate` at all, only its `name` (a plain `str`, the natural
  key). There is then nothing a later change to the template *could* reach
  through, on any conversation constructed through this module's own API.
  This isn't a lighter version of the guard above; it removes the channel
  the guard would have been guarding.

Chosen: structural removal. It is cheaper than "add a step" (one string
field instead of a nested object plus a check) and it turns requirement 4
from something proven by a passing test today into something a reviewer can
see is true from the type alone. This is also this work item's reading of
CLAUDE.md's existing "use names, not pointers for anything long-lived" line,
applied one step earlier than that line's own phrasing suggests (the line
says "with a database constraint behind it" — CONV-08's job; this work item
applies the same discipline to an in-memory reference, before any database
exists to enforce it). A CLAUDE.md amendment making that generalization
explicit is proposed at the end of this document for approval.

**Types added.**

```python
TemplateName = str  # a caller-minted natural key, same posture as
                     # ConversationKey: not generated, not validated, not
                     # enforced unique here — that needs a store (CONV-08).

@dataclass(frozen=True)
class PluginCatalogEntry:
    """One caller-supplied fact about an outside procedure. Nothing here
    discovers, loads, or validates one — a future PLUGINS block is the real
    producer of this data; this work item only defines the three fields
    blueprint §8 Open Question 1 named."""
    name: str
    purpose: str          # one sentence, rendered verbatim
    entry_tool: str        # a tool name, not resolved against tool_specs
                            # here — see Open questions.

@dataclass(frozen=True)
class TemplateRecipe:
    """Everything a starting point contributes to a conversation's prompt
    and tool surface, as one atomic bundle — not three separately-pending
    fields. See Rejected alternatives for why a single bundle, not a
    per-field patch."""
    stable_prompt: str
    catalog: tuple[PluginCatalogEntry, ...]
    tool_specs: tuple[ToolSpec, ...]

@dataclass(frozen=True)
class ConversationTemplate:
    """A starting point conversations are created from. Frozen, like every
    other value in this module — 'recording a deferred change' is a
    function that returns a new value, never an in-place mutation, matching
    IterationBudget's own consume()-returns-a-new-value-or-None shape and
    CLAUDE.md's explicit rule for a consumable resource."""
    name: TemplateName
    recipe: TemplateRecipe
    pending_recipe: TemplateRecipe | None = None

class PromptRotationReason(Enum):
    """The one member blueprint §4.3 names. A second member is a visible
    diff at every call site that already pattern-matches on this enum —
    that visibility, not the value itself, is what this type is for."""
    COMPRESSION = "compression"

@dataclass(frozen=True)
class Conversation:
    """One continuing conversation. Holds `template_name`, never a
    `ConversationTemplate` — the structural-removal choice above."""
    key: ConversationKey
    template_name: TemplateName
    system_prompt: str
    prompt_sha256: str
    prompt_epoch: int
    tool_surface: ToolSurface
    messages: tuple[Message, ...]
    next_turn_seq: int
    iteration_budget: IterationBudget
    wall_clock_budget: WallClockBudget | None
```

**Functions added**, each a free function over these plain values — no
class carries behavior, matching every prior work item's own choice and
C6/C7's explicit "declined: one implementation, nothing to abstract over yet"
reasoning for a Protocol/class bundling these:

```python
def defer_invalidation(
    template: ConversationTemplate, new_recipe: TemplateRecipe
) -> ConversationTemplate:
    """Records `new_recipe` as `template`'s pending replacement. Returns a
    new `ConversationTemplate` value; `template` itself, and every
    `Conversation` already built from it, are unreachable from this call —
    not merely untouched by this call's own code path, but unreachable by
    construction (Conversation holds no reference to `template` at all)."""

def create_conversation(
    template: ConversationTemplate,
    key: ConversationKey,
    system_message: str,
    *,
    iteration_budget: IterationBudget,
    wall_clock_budget: WallClockBudget | None = None,
) -> tuple[Conversation, ConversationTemplate]:
    """If `template.pending_recipe` is set, it is promoted to
    `template.recipe` and cleared — this call, not `defer_invalidation`'s
    call, is 'the next conversation created from the same template' per
    blueprint §4.3. Builds `tool_surface = build_surface(recipe.tool_specs)`,
    composes `system_prompt` from `recipe.stable_prompt` + `system_message` +
    one rendered line per `recipe.catalog` entry, and sets
    `prompt_sha256 = turn_prompt_hash(system_prompt, tool_surface)` — the
    same function C7 already defined, not a second hash. Returns the new
    `Conversation` (epoch 0, empty history, `next_turn_seq=0`) alongside the
    template value the caller should keep using next — `template` itself is
    never mutated."""

def rotate_prompt(
    conversation: Conversation, *, reason: PromptRotationReason, new_prompt: str
) -> Conversation:
    """The only function in this module that increments `prompt_epoch`.
    Recomputes `prompt_sha256` against `new_prompt` and the conversation's
    existing `tool_surface` so the result is self-consistent for the very
    next turn's own PROLOGUE check. `reason` is accepted and not stored —
    see Concerns."""

async def take_turn(
    conversation: Conversation,
    *,
    user_input: str,
    provider: str,
    model: str,
    dispatch: Callable[[str, dict], Awaitable[str]],
    compress: Callable[[tuple[Message, ...], str], Awaitable[str | None]],
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
    now: float,
) -> tuple[TurnResult, Conversation]:
    """Calls C7's `run_turn` with every value `run_turn` needs, read off
    `conversation` (its `key` as `conversation`, `next_turn_seq` as
    `turn_seq`, its own `messages`/`system_prompt`/`prompt_sha256`/
    `tool_surface`/`iteration_budget`/`wall_clock_budget`). `run_turn`'s
    returned `(TurnResult, messages, iteration_budget, system_prompt)` is
    folded back: if the returned `system_prompt` differs from
    `conversation.system_prompt` (compression rotated it mid-turn), the
    result goes through `rotate_prompt(..., reason=COMPRESSION, ...)` first,
    so epoch/hash always move together through the one sanctioned path; then
    `messages`, `iteration_budget`, and `next_turn_seq + 1` are folded in.
    Returns `(TurnResult, updated Conversation)` — never mutates
    `conversation`."""
```

**Prompt composition (requirement 3).** `stable_prompt` is joined with
`system_message` and the rendered catalog with `"\n\n"`, skipping any part
that is empty — a template with no catalog entries and a conversation with
an empty `system_message` still produces a valid two-part prompt. Each
catalog entry renders as one line: `f"{entry.name}: {entry.purpose} (start
with {entry.entry_tool})"`. No section is time-dependent, no section reads
live state — matching blueprint §4.3's own reason for dropping hermes's
volatile tier entirely.

## Interface

**New public names** (all in `src/sadana/conversation.py`, alongside the
existing ones from C2/C3/C4/C6/C7): `TemplateName`, `PluginCatalogEntry`,
`TemplateRecipe`, `ConversationTemplate`, `PromptRotationReason`,
`Conversation`, `defer_invalidation`, `create_conversation`, `rotate_prompt`,
`take_turn`.

**In:** `create_conversation` takes a `ConversationTemplate` value, a
`ConversationKey`, a per-conversation `system_message: str`, and the two
budgets C4/C5 already define. `defer_invalidation` takes a
`ConversationTemplate` and a full replacement `TemplateRecipe`. `take_turn`
takes a `Conversation` value and every seam `run_turn` already requires
(`dispatch`, `compress`, `persist`, `now`, `provider`, `model`) — no new
seam, no new config key.

**Out:** `create_conversation` and `defer_invalidation` never raise;
`take_turn` returns whatever `run_turn` returns and propagates
`PromptDriftError` exactly as `run_turn` already does — unreachable through
this module's own API (every `Conversation` this module builds has a
`prompt_sha256` that already matches its `system_prompt`/`tool_surface` by
construction), reachable only if a caller hand-builds a `Conversation` with
inconsistent fields, bypassing `create_conversation`/`rotate_prompt`
entirely — the same caller-discipline posture this module already takes for
`WallClockBudget.deadline`'s clock-matching requirement.

## Acceptance criteria

- [ ] `create_conversation` builds a `Conversation` whose `prompt_sha256`
      equals `turn_prompt_hash(conversation.system_prompt,
      conversation.tool_surface)` exactly.
- [ ] Two `ConversationTemplate`s whose `recipe.catalog` differ (all else
      equal) produce different `system_prompt` and `prompt_sha256` from
      `create_conversation` — proves the catalog is genuinely rendered, not
      ignored.
- [ ] `defer_invalidation(template, new_recipe)` returns a new
      `ConversationTemplate`; the `template` value passed in is unchanged
      (still shows its original `recipe`, `pending_recipe is None`).
- [ ] A `Conversation` created *before* `defer_invalidation` is called keeps
      an unchanged `system_prompt`/`prompt_sha256`/`tool_surface` after
      `defer_invalidation` runs against the template it came from — the
      "invisible to the running conversation" half of requirement 4.
- [ ] `create_conversation` called again with the `ConversationTemplate`
      value `defer_invalidation` returned produces a `Conversation` whose
      `system_prompt` reflects the new recipe, and the *returned* template
      from that second call has `pending_recipe is None` again — the
      "visible to the next one, and only once" half of requirement 5.
- [ ] A third `create_conversation` call against the promoted template (no
      further `defer_invalidation`) keeps using the promoted recipe, not the
      original — proves promotion persists rather than applying once and
      reverting.
- [ ] `take_turn` on a fresh `Conversation`, given a fake provider returning
      a text-only completion, returns `TurnResult.exit_reason ==
      ExitReason.COMPLETED` and an updated `Conversation` whose `messages`
      contain both the user input and the assistant reply,
      `next_turn_seq == 1`, and `iteration_budget.used == 1`.
- [ ] `take_turn` with a fake `compress` that returns a new prompt string
      (forced by one `ContextOverflow`) returns a `Conversation` with
      `prompt_epoch == 1` and `prompt_sha256 == turn_prompt_hash(new
      system_prompt, tool_surface)`; a second `take_turn` call against that
      returned `Conversation` does not raise `PromptDriftError`.
- [ ] Two `take_turn` calls in sequence, threading the returned
      `Conversation` from the first into the second, produce one history
      containing both turns' messages — not two independent single-turn
      histories. The concrete "asked more than one question" proof from the
      intent.
- [ ] `rotate_prompt` is the only function that changes `prompt_epoch`:
      `create_conversation` always starts a `Conversation` at
      `prompt_epoch == 0`, and a `take_turn` call whose `compress` is never
      invoked (no `ContextOverflow`) leaves `prompt_epoch` unchanged.

## Non-goals

- A real mechanism that discovers, loads, or mounts an outside procedure —
  `PluginCatalogEntry` and `ToolSpec` are supplied as plain data by the
  caller; no loader, no manifest file, no registry.
- Enforcing `ConversationTemplate.name` or `ConversationKey` uniqueness —
  same posture C2 already took for `ConversationKey`; a real constraint
  needs a store, CONV-08's job.
- Durable storage of any kind. Every value this work item builds lives only
  in the process's memory for as long as it runs.
- Child conversations (CONV-07), an `Observer` (declined in every prior
  spec, no test infrastructure needs one yet), streaming.
- Per-model prompt variation. `provider`/`model` stay `take_turn`-call
  parameters exactly as C7 already fixed them — not part of a template's
  recipe, not hashed into `prompt_sha256`, unaffected by
  `defer_invalidation`. blueprint §4.3's "a model switched" example is
  therefore not one of the changes this work item's `defer_invalidation`
  actually carries; noted so a reviewer doesn't look for it.
- `timeouts.*` config keys — same reasoning C7's own spec already gave
  (config without enforcement behind it is worse than no config); this work
  item adds no timeout enforcement either.
- Any change to `run_turn` itself (C7). This work item is a caller of it,
  not a modification to it.

## Open questions

1. **Should `PluginCatalogEntry.entry_tool` be validated against
   `recipe.tool_specs`'s resolved names, rather than stored as a literal
   string?** CLAUDE.md asks that a tool description referencing another
   tool go through the resolved-name mechanism C3 already built
   (`ToolSpec.describe(ResolvedNames)`), so no cross-reference is ever a
   literal. `entry_tool` is exactly a cross-reference, and this work item
   stores it as a plain string instead, because there is no real plugin
   producing mismatched values yet to justify the check. Recommendation:
   leave it unvalidated here, and require whichever work item first has a
   real `PluginCatalogEntry` producer (the PLUGINS block) to either resolve
   `entry_tool` through the same mechanism or explain why a plain string is
   fine once there's a real case to look at. Does not block this work item.

## Rejected alternatives

**`Conversation` embedding the full `ConversationTemplate` value it was
created from, declined.** Covered in Design's guideline-3 discussion above:
storing only `template_name` doesn't just happen to leave the running
conversation untouched by a later `defer_invalidation` call, it makes that
outcome structural. Embedding the value would need a test to prove the
invariant every time this module changes; a `str` field makes the test
almost redundant with the type.

**Per-field pending changes (`pending_system_message`,
`pending_catalog`, `pending_tool_specs` as three independently-settable
fields) instead of one atomic `pending_recipe`, declined.** Three fields
need an answer to "what if only one of three is pending when
`create_conversation` runs" that nothing today needs answered — no real
caller composes a partial change. One atomic bundle is fewer bets
(guideline 2) and less state to reason about (guideline 4): a single
`Optional[TemplateRecipe]`, not three.

**A mutable `ConversationTemplate` class with in-place `.defer_invalidation()`
and `.create()` methods, declined.** Would break the one pattern every
value in this module already follows — `IterationBudget.consume_iteration`,
`Message` history's `append`/`repair`, now `defer_invalidation` and
`create_conversation` — a function taking a value, returning a new one or
raising, never mutating in place. CLAUDE.md states this explicitly for a
consumable resource ("never a mutable counter guarded by a lock"); this
work item applies the same discipline to a value that isn't literally a
budget but has the same shape (something recorded now, consumed later).

**Recomputing the composed prompt fresh from a live template reference on
every `take_turn` call (hermes's own approach), declined.** Audited above
in "Learning from the reference." Reintroduces the rebuild-on-drift hazard
blueprint §4.3 exists to close, and moves `prompt_sha256` from "true by
construction" to "must be re-verified every turn."

**Storing `PromptRotationReason` (or a free-text `what`/reason string) on
`Conversation` or `ConversationTemplate` for later inspection, declined.**
No reader exists — no `Observer` (still not built, declined again in
Non-goals), no logging spec for this project. Stored, unread state is
exactly what Design guideline 4 asks this work item to avoid; `reason` is
accepted by `rotate_prompt` only to keep blueprint §4.3's "the reason is an
enum with one member so adding a second is a visible diff" property true at
every call site, not to be retained.

## Concerns

**The whole "invisible to the running conversation" guarantee rests on one
fact: `Conversation` never holds a live reference to anything mutable.** If
CONV-07 (child conversations) or CONV-08 (a durable store) ever gives
`Conversation` a handle back to its template — for a legitimate reason, e.g.
a store needs to join them — the guarantee stops being structural and
becomes a rule someone has to remember to uphold by hand. Flagging this now,
explicitly, so whoever does that later reads this section first rather than
rediscovering the reasoning.

**`ConversationTemplate.name` and `ConversationKey` are both unenforced
natural keys at this stage** — nothing here stops two callers from building
two templates, or two conversations, with the same name. Same posture C2
already took and already flagged; repeating it here because this work item
is the first place a *template* name exists at all, and it will look, to a
reviewer skimming only this file, like an oversight rather than a repeated,
deliberate deferral to CONV-08.

**Policy conformance.** `testing-conventions` applies and is satisfied
throughout: every acceptance criterion above is stated as a relationship
between values (a hash matches a recomputation, a template's returned value
differs from the one passed in, an epoch increments exactly once) rather
than a snapshot of current data, no test needs the network, the real clock,
or a real filesystem, and `take_turn`'s tests reuse C6/C7's own
fake-provider/fake-`dispatch`/fake-`compress`/fake-`persist` style rather
than inventing a second one. `project-structure` and `reference-lookup`
still don't exist in this project, matching every prior spec's own note.
No security, brand, or UX policy skill applies to an in-memory data
structure with no user-facing surface. No policy conflict was found to
report — the one real tension in this work item (blueprint's literal
`conversation.defer_invalidation(what)` shape vs. the structural-removal
design chosen above) is a tension with blueprint's own prose, not between
two applicable policies, and is resolved and argued in Design and Rejected
alternatives rather than here.

---

## Proposed CLAUDE.md amendment (for approval)

This work item generalizes an existing rule rather than inventing a new one.
The existing line:

> Use names, not pointers for anything long-lived: Anything long-lived a
> user returns to should be addressed by a unique natural key with a
> database constraint behind it

reads, on its own, as a rule about persistence — "once there's a database."
This work item applies the identical discipline to an **in-memory**
reference, before any database exists, because doing so is what makes
requirement 4 (a deferred change is provably invisible to a conversation
already running) true by construction instead of true by a test that
happens to pass today. Proposed one-line addition, to sit directly under
the existing bullet:

> Apply the same rule in memory, before a database exists: a value should
> hold another long-lived value's name, never a live reference to it,
> whenever something else might change independently of what already holds
> the reference.

Please confirm whether to add this line to CLAUDE.md before I continue to
the build stage.
