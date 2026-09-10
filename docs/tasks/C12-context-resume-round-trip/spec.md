# Spec: context state survives a conversation resume

Intent: docs/tasks/C12-context-resume-round-trip/intent.md

## Requirements

1. A conversation reconstructed by `conversation_store.load()` gets back the
   `context_state` (running usage total) that was actually persisted for
   that conversation's key, not a fresh `ContextState()`. Traces to intent's
   Problem/Proposed outcome: the usage total must not snap to zero on
   resume.
2. A conversation reconstructed by `conversation_store.load()` gets back the
   `stable_prompt_len` that was actually persisted for that conversation's
   key, not a value derived from the full stored `system_prompt`. Traces to
   the same Proposed outcome: caching must not get less precise purely
   because the conversation was saved and reloaded.
3. Every `create()`/`save()` call persists the calling `Conversation`'s
   current `stable_prompt_len` and `context_state`, so that a `load()` on
   the same key afterward gets back requirements 1 and 2. This is the
   mechanism, not a separate outcome.
4. A conversation row written before this fix ships (missing the new
   columns) still loads without raising, and behaves exactly as it does
   today — `context_state` at zero, `stable_prompt_len` equal to the full
   `system_prompt` length — until it is next resumed and saved for real.
   Traces to intent's Constraints: existing persisted rows must keep
   loading.
5. The fix adds no dependency and no new storage backend; it stays inside
   the existing SQLite store. Traces to intent's Constraints.

## Design

**Root cause, precisely.** `conversation_store.load()`
(`src/sadana/conversation_store.py:213-261`) never had a column to read
`context_state`/`stable_prompt_len` back from — it wasn't discarding stored
data, it was reconstructing values that were simply never written by
`create()`/`save()` in the first place. Both fields already exist, computed
correctly, on the in-memory `Conversation` the whole time
(`conversation.py:1084-1085`, `1296-1297`) — `stable_prompt_len` is set once
at `create_conversation()`/`run_child()` time and never changes again (not
even `rotate_prompt()` touches it, confirmed by reading
`conversation.py:1090-1104`); `context_state` is folded forward every turn by
`context.after_response()`/`turn_complete()`. Nothing needs deriving. The fix
is wiring three already-correct in-memory values through the same
generic-column mechanism the table already uses for `next_turn_seq`,
`iteration_used`, and `next_child_seq`.

**Schema.** Three new columns on `conversations`:

- `stable_prompt_len INTEGER` — nullable. `NULL` means "written before this
  fix" (requirement 4's fallback trigger), not "zero, meaning nothing is
  cacheable."
- `context_total_prompt_tokens INTEGER NOT NULL DEFAULT 0`
- `context_total_completion_tokens INTEGER NOT NULL DEFAULT 0`

`0` is a safe default for the two token columns: it is exactly today's
existing (buggy) reset value, so a legacy row's behavior is unchanged either
way — no fallback branch is needed for those two, only for
`stable_prompt_len`, since `0` would be actively wrong there (it would mark
nothing as cacheable, worse than today's too-wide default).

**Migration.** `open_store()`'s `_SCHEMA` script (`CREATE TABLE IF NOT
EXISTS`) never alters a table that already exists, so a real dev box's
existing `conversations.sqlite3` needs an explicit, idempotent migration
step run after the schema script, adapted from hermes-agent's own guarded
`ALTER TABLE ... ADD COLUMN` pattern
(`../hermes-agent/gateway/delivery_ledger.py:130-141`, kind:
`production-code`): read `PRAGMA table_info(conversations)`, and for each of
the three columns not already present, run `ALTER TABLE conversations ADD
COLUMN ...`. Adopted the `PRAGMA table_info` pre-check; declined hermes's own
`sqlite3.OperationalError`/"duplicate column" exception guard, caught during
build-stage self-check as speculative: that guard exists in hermes only to
survive two connections racing the same first-use migration, and the
pre-check alone already makes a second, sequential `open_store()` call in
one process a no-op — the only case the guard would additionally catch is a
genuine concurrent race, which this store's own Non-goals (CONV-08
spec.md) already name out of scope (single-writer, single-process). Keeping
it would have been defending a scenario this project has explicitly
declined to support.

**Write path.** `_CONVERSATION_COLUMNS` gains the three column names;
`_conversation_row()` gains `conversation.stable_prompt_len`,
`conversation.context_state.total_prompt_tokens`,
`conversation.context_state.total_completion_tokens` at the matching
positions. `create()`/`save()` need no other change — both already build
their row and column list generically off these two constants.

**Read path.** `load()` reconstructs:

```
stable_prompt_len=row["stable_prompt_len"] if row["stable_prompt_len"] is not None else len(row["system_prompt"]),
context_state=context.ContextState(
    total_prompt_tokens=row["context_total_prompt_tokens"],
    total_completion_tokens=row["context_total_completion_tokens"],
),
```

replacing the two hardcoded reset lines and their now-stale comment
(`conversation_store.py:251-259`), which explained why the reset was
*deliberate* under C10/C11's own spec — that explanation is what this work
item revisits.

## Interface

No public function signature changes. `create()`, `save()`, `load()`,
`bind_persist()`, `search_conversations()`, `open_store()` all keep their
existing inputs and outputs. What changes is only what `load()`'s returned
`Conversation` actually contains, and what `open_store()` does once, on
first use, to an existing database file. `search_conversations()` needs no
direct change — it already delegates to `load()` per matched key
(`conversation_store.py:295`).

## Acceptance criteria

- [ ] `conversations` table gains `stable_prompt_len`,
      `context_total_prompt_tokens`, `context_total_completion_tokens`,
      added by an idempotent migration inside `open_store()`.
- [ ] `create()` and `save()` persist `stable_prompt_len` and both
      `context_state` fields from the `Conversation` passed in.
- [ ] `load()` reconstructs `context_state` from the two persisted token
      columns.
- [ ] `load()` reconstructs `stable_prompt_len` from the persisted column
      when non-`NULL`, falling back to `len(system_prompt)` when `NULL`.
- [ ] A `conversations` row inserted through today's pre-fix column set
      (simulating a database file that predates this fix) still loads
      without raising, with `context_state` at zero and `stable_prompt_len`
      equal to the full `system_prompt` length.
- [ ] A conversation created, mutated (context_state advanced,
      stable_prompt_len set to something narrower than the full prompt),
      saved, and then loaded through a **second, independently opened**
      connection to the same database file round-trips both fields exactly
      — not just survives an in-memory `dataclasses.replace()`.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Backfilling correct values into rows already saved before this fix ships.
  They keep today's behavior (requirement 4) until next resumed and saved
  for real — not retroactively corrected in place.
- `bind_persist()`'s intra-turn snapshot staleness (see Concerns) — already
  a named, accepted behavior, not something this item changes.
- The unrelated, already-named-elsewhere gap that `_insert_messages`'
  `INSERT OR IGNORE` assumes history only ever grows, which real compaction
  would violate — a different, currently-inert risk on the same module,
  tracked separately (project memory: "Revisit the moment anything real
  calls `conversation_store.save()`/`bind_persist()` on a conversation that
  also compacts").

## Rejected alternatives

1. **Store the template (or a name pointing back to it) and re-derive
   `stable_prompt_len` at load time by looking up its `recipe.stable_prompt`
   length.** Declined: a template's `recipe` can be replaced after a
   conversation is created (`defer_invalidation()`'s whole purpose), and
   `rotate_prompt()` never updates a conversation's `stable_prompt_len` to
   match — looking the boundary up "live" from whatever the template
   currently holds would silently return the *wrong* boundary for any
   conversation created under an earlier recipe. This would also reopen
   `Conversation`'s own settled design choice to hold `template_name`, never
   a live `ConversationTemplate` reference, for exactly this reason
   (CLAUDE.md's names-not-pointers rule, already argued in C8/C9's specs).
   The value needed is a plain int, already computed once, at the moment it
   is first known; storing that int directly is fewer bets than
   reconstructing it from a second, time-sensitive lookup.
2. **Store `ContextState` as a JSON blob**, matching `tool_surface_json`'s
   own precedent. Declined: `ContextState` is a flat two-int record, the
   same shape `IterationBudget`'s own `iteration_max_total`/`iteration_used`
   columns already use as plain columns — following that existing
   precedent keeps the two new numbers directly inspectable and queryable
   without a JSON parse. JSON in this table is for genuinely variable-length
   data (the tool surface, a message's tool-call list); two fixed ints
   aren't that.
3. **A general schema-migration engine**, the shape hermes-agent's own
   `hermes_state_schema.py` uses (reflection-driven `ALTER TABLE ADD COLUMN`
   across a declared schema diff, built up over years of incidents across
   dozens of tables). Declined outright, not narrowed: this store has one
   table needing three new columns, once; the guarded
   `PRAGMA table_info` + `ALTER TABLE` pattern
   (`delivery_ledger.py:130-141`) both models the same real production
   need at roughly a tenth the code, stdlib-only, and is easy to audit at a
   glance — a diff engine for one table is complexity with no second
   consumer, the same "registry earns its cost only once a second real
   member exists" reasoning CLAUDE.md already states for backend
   registries.
4. **Backfill `stable_prompt_len` for legacy rows** via
   `UPDATE conversations SET stable_prompt_len = LENGTH(system_prompt) WHERE
   stable_prompt_len IS NULL` at migration time, so every row ends up
   non-`NULL`. Declined: that would permanently bake today's already-wrong,
   too-wide boundary into old rows as if it were meaningful, correctly
   computed data — indistinguishable at read time from a row that got the
   real, narrow boundary. Leaving it `NULL` is honest about which rows are
   still degraded, and those rows self-correct the moment they are next
   genuinely resumed and saved, which requirement 4 already accepts as the
   bar.

## Concerns

`bind_persist()`'s own snapshot is taken once, at the start of a turn
(`template = replace(conversation, messages=())`,
`conversation_store.py:313`); a mid-turn `_persist` call therefore still
writes `context_state`/`stable_prompt_len` as of turn *start*, not
whatever `context.after_response()` has advanced them to by that point in
the same turn. This is not a new risk this item introduces — it is already
the exact, named behavior CLAUDE.md's own rule describes ("`bind_persist()`
is rebuilt fresh before every turn... its snapshot-at-bind-time budgets/
`next_turn_seq`/`context_state` go stale otherwise"), and both real callers
(`gateway_dispatch.py:104`, `subcommands/chat.py:74`) already make one more,
unconditional `save()` with the fully updated `conversation` after
`take_turn()` returns, which carries the correct final values. Flagging it
here only so a reviewer checking this work item's correctness doesn't
mistake existing, accepted intra-turn staleness for something this fix
should have closed and didn't.

No policy skill beyond `testing-conventions` exists as an actual file in
this repository's `.claude/skills/` — `project-structure` and
`reference-lookup`, named generically in design-skill's own instructions,
have no dedicated skill here. Applied CLAUDE.md's own layout section and
"methodology for learning from the reference" section in their place; no
conflict found between them and this design. `testing-conventions` is
applied directly: acceptance criteria require a real second-connection
round trip (not an in-memory replace), matching the "no source-reading,
assert invariants not snapshots" posture, and the new/changed tests belong
in `tests/unit/test_conversation_store.py`, the existing test file for this
module, not a new one — the change is additive to that module's own schema
and read/write path, not a new module of its own.

**Bets reduced, not added.** Per design guideline 2: this work item sits
well inside CONVERSATION/CONTEXT/SESSION-STORE, blocks that predate any
plugin seam — the "growth means more plugins" framing doesn't apply here,
and nothing about this fix forecloses it either. The fix itself removes a
bet rather than placing one: `load()` currently bets that "reset to zero,
widen to the full prompt" is an acceptable proxy for real persisted state;
this replaces that bet with actually storing the value, which needs no
further judgment calls as usage of this conversation grows.

**Least-cost scenario catch, per guideline 3.** The uncaught scenario is "a
resumed conversation's `load()` has no persisted value to read." The move
made is **add a step**: three more columns read and written through the
exact generic mechanism three existing columns already use. No existing
step gets heavier (the row-building/reading code doesn't change shape, only
length) or harder (no new invariant, no new failure mode — a legacy `NULL`
falls back to exactly today's existing behavior rather than raising).
Cheaper alternatives were considered and rejected above (deriving instead of
storing) because they are not actually cheaper — they trade a few lines
today for a live-reference bug later.

**State inventory, per guideline 4.** Two genuinely new pieces of mutable
state: `context_total_prompt_tokens`/`context_total_completion_tokens`
(cannot be derived — nothing else records a conversation's accumulated
provider usage; the only source of truth is the running total itself,
folded forward one turn at a time) and `stable_prompt_len` (cannot be
derived at load time either, per Rejected alternative 1 above — the
template it came from is not a stable enough source once
`defer_invalidation()` exists). Both are already-computed values on the
in-memory `Conversation`/`ContextState` today; this item stores what
already exists, it does not compute anything new.
