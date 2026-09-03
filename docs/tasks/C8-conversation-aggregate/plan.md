# Plan: A conversation becomes one continuing thing, not one call (from intent.md 2026-09-03)

## Context

Build stage for **C8-conversation-aggregate**, CONV-06 from
`docs/reference/conversation_block_blueprint.md` §7 — the `Conversation`
aggregate and a "starting point" (`ConversationTemplate`) a conversation is
created from, wired to C7's `run_turn`. `intent.md` and `spec.md` are
already written and checked; `spec.md` is unusually thorough (every type,
signature, and rejected alternative already fixed), so this plan skips
Explore/Plan subagents — I already read `src/sadana/conversation.py` in
full during design (870 lines, functions through `run_turn` at line 691),
and spec.md's own "Learning from the reference" section already audited
the reference corpus and found no hermes analogue for the template concept
to adopt. C7's own plan.md set this precedent for the same reason.

The problem this closes: `run_turn` (C7) runs exactly one turn given every
value it needs as plain parameters — nothing remembers a conversation's
identity or state between turns, and there is nowhere to record a change
(a plugin catalog entry, a stable-prompt edit) that should apply to the
*next* conversation created from a shared starting point without ever
touching a conversation already running from it.

## Files that change

- `CLAUDE.md` — one line added under "Please do", generalizing the
  existing "names, not pointers" rule to in-memory references. Not new
  build-stage work: `spec.md`'s Design section proposed this amendment as
  the direct consequence of the "structural removal" choice below, and
  Adam approved it explicitly in conversation, before this plan was
  drafted — the edit was already made at that point. Listed here so the
  diff and this plan agree; omitted from an earlier draft of this section,
  caught in review.
- `src/sadana/conversation.py` — append after `run_turn` (currently ends
  ~line 870): `TemplateName`, `PluginCatalogEntry`, `TemplateRecipe`,
  `ConversationTemplate`, `PromptRotationReason`, `Conversation`,
  `defer_invalidation()`, `create_conversation()`, `rotate_prompt()`,
  `take_turn()`. No existing function's signature changes.
- `tests/unit/test_conversation.py` — same file every prior C-series work
  item added its tests to, extended with tests for the above.

## Order of work

1. **Template half** — `TemplateName`, `PluginCatalogEntry`,
   `TemplateRecipe`, `ConversationTemplate`, `Conversation` (dataclasses),
   `defer_invalidation()`, `create_conversation()`. No `run_turn`
   involvement yet — proves the novel part (the pending/promoted recipe
   semantics, and the "no live reference" structural guarantee, spec.md's
   central design choice) in isolation, before wiring it to anything
   already working.
   - Tests: `create_conversation` builds a `Conversation` whose
     `prompt_sha256 == turn_prompt_hash(system_prompt, tool_surface)`;
     differing `recipe.catalog` produces a differing hash; `defer_invalidation`
     returns a new `ConversationTemplate` leaving the original untouched;
     a `Conversation` created before a `defer_invalidation` call keeps its
     own prompt/hash/surface unchanged after that call; a second
     `create_conversation` against the returned template reflects the new
     recipe with `pending_recipe` cleared; a third call (no further defer)
     keeps using the promoted recipe.
2. **Turn wiring** — `PromptRotationReason`, `rotate_prompt()`,
   `take_turn()`. Built on top of step 1's now-tested `Conversation`, and
   C7's already-tested `run_turn` — the riskiest step, because it's the one
   place new code branches on whether `run_turn`'s returned `system_prompt`
   changed (compression fired) and must route through `rotate_prompt` only
   in that case, while folding `messages`, `iteration_budget`, and
   `next_turn_seq + 1` back into a new `Conversation` value every time.
   - Tests: `take_turn` on a fresh `Conversation` with a fake
     text-completion provider reaches `ExitReason.COMPLETED`, appends both
     messages, `next_turn_seq == 1`, `iteration_budget.used == 1`; a fake
     `compress` forcing one `ContextOverflow` leads to `prompt_epoch == 1`
     and a self-consistent hash (a following `take_turn` call does not raise
     `PromptDriftError`); two sequential `take_turn` calls, threading the
     returned `Conversation`, accumulate one shared history; a `take_turn`
     call where `compress` is never invoked leaves `prompt_epoch == 0`
     (proves `rotate_prompt` is the only epoch-mutating path).
3. `make verify` (chain, lint, typecheck, test) — paste the output ending
   `VERIFY OK` as this stage's Proof.

## Risks

**What could this change break?** Nothing existing — every addition is new
dataclasses/functions appended to the module; no existing signature
(`run_turn`, `append`, `repair`, `build_surface`, `consume_iteration`,
`wall_clock_remaining`, `complete`) changes. The existing test suite in
`tests/unit/test_conversation.py` should pass unmodified; this plan adds
tests, it does not touch any existing ones.

**Which step is riskiest?** Step 2 (`take_turn`'s compression-branch
wiring), argued above — the one place this work item's own new logic has a
conditional that must exactly track `run_turn`'s internal behavior. Landed
after step 1's `Conversation` type is already tested and stable, and its
own acceptance criteria explicitly exercise both branches (compression
fired / not fired), not just the happy path.

**Is this plan drifting back toward anything spec.md already rejected?**
Checked against spec.md's five Rejected alternatives directly:
1. `Conversation` holds `template_name: TemplateName` (a `str`) — never a
   `ConversationTemplate` field.
2. `ConversationTemplate.pending_recipe` is a single `TemplateRecipe | None`
   — no per-field pending slots.
3. `ConversationTemplate` and `Conversation` are both frozen dataclasses;
   `defer_invalidation`/`create_conversation`/`rotate_prompt`/`take_turn`
   all return new values via `dataclasses.replace` or a fresh constructor
   call — nothing mutates a field in place.
4. `take_turn` never reads a template or recomputes `system_prompt` from
   one — it only ever forwards `conversation`'s own already-stored
   `system_prompt`/`prompt_sha256` into `run_turn`.
5. `rotate_prompt`'s `reason: PromptRotationReason` parameter is accepted
   and used only to select behavior at the call site (there being only one
   member today); it is not written into any dataclass field.

## Proof

`make verify` output, pasted in full, ending `VERIFY OK`. Matching
spec.md's Acceptance criteria checklist one-for-one:
- prompt_sha256-at-creation invariant
- catalog-changes-the-hash
- defer_invalidation returns new value / original untouched
- invisibility of a deferred change to an already-created Conversation
- promotion on next create_conversation, and persistence across a further
  create with no new defer
- take_turn COMPLETED path (messages, next_turn_seq, iteration_budget.used)
- take_turn compression path (epoch 1, self-consistent hash, no
  PromptDriftError on the next call)
- two sequential take_turn calls share one history
- rotate_prompt is the only epoch-mutating path (no-compression case stays
  at epoch 0)
