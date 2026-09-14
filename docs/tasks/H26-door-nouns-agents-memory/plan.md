# Plan: door nouns for agents and memory (from intent.md 2026-09-14)

Author: Adam Aubry (product owner). Status: approved.

## Files that change

- `src/sadana/memory.py` — `MEMORY_KINDS`, `normalize_kind()`.
- `src/sadana/memory_store.py` — `write_entry()` gains `kind=`, `source=`,
  `conversation_key=` kwargs (defaulted, backward compatible); new
  `MemoryEntryRow`, `list_entries_full()`, `get_entry_by_id()`; new
  `RubricRow`, `get_rubric_override_row()`; `DispatchContext` gains
  `conversation_key: str = ""`.
- `src/sadana/builtin_plugins/memory/init.py` — pass `kind`/`source`/
  `conversation_key` through to `memory_store.write_entry`.
- `src/sadana/builtin_plugins/memory/schema/remember.json` — optional
  `kind` enum property.
- `src/sadana/client_surface.py` (~line 475) — one line:
  `memory_store.DispatchContext(account_key=account, conn=conn,
  conversation_key=conversation)`.
- `src/sadana/persona_store.py` — `AGENT_STATES` widens to
  `{"draft", "active"}`; `_write_character_file()` and `insert_agent_row()`
  extracted from `write_template()`/`reconcile_agents()` (both keep their
  own external behaviour); new `create_character()`, `update_character()`,
  `set_agent_state()`, `AgentRow`, `list_agent_rows()`, `get_agent_row()`,
  `read_character_text()`.
- `src/sadana/door/nouns/agent_templates.py` (new).
- `src/sadana/door/nouns/agents.py` (new).
- `src/sadana/door/nouns/memory_entries.py` (new).
- `src/sadana/door/nouns/memory_policies.py` (new).
- `src/sadana/subcommands/door.py` — `nouns={...}` gains the four entries.
- `docs/console/nouns.md` — the four `not yet (H26)` sections filled in,
  including the two documented exceptions (`agent_templates`' hash-derived
  id and non-fact timestamps; `memory_policies`' synthetic default row).
- `tests/unit/test_memory.py` — `normalize_kind`.
- `tests/unit/test_memory_store.py` — new kwargs, `list_entries_full`,
  `get_entry_by_id` (including cross-account isolation), `get_rubric_override_row`.
- `tests/unit/test_builtin_plugin_memory.py` — kind/source/conversation_key
  passthrough.
- `tests/unit/test_persona_store.py` — the two promoted helpers,
  `create_character`, `update_character`, `set_agent_state`,
  `list_agent_rows`; a regression check that `write_template` and
  `reconcile_agents`'s existing behaviour is byte-identical after the
  extraction.
- `tests/contract/nouns/test_agent_templates.py` (new).
- `tests/contract/nouns/test_agents.py` (new).
- `tests/contract/nouns/test_memory_entries.py` (new).
- `tests/contract/nouns/test_memory_policies.py` (new).

## Order of work

1. **`memory.py`: `normalize_kind`.** Pure, zero dependents yet. Narrow
   check: `make test -k test_memory`.
2. **`memory_store.py`: the new read functions and `write_entry` kwargs,
   `DispatchContext.conversation_key`.** Additive; existing callers
   (`write_entry(conn, account, key, content, now=...)` with no new kwargs)
   keep working because every new parameter defaults. Narrow check:
   `make test -k test_memory_store`, and re-run the full existing
   `test_memory_store.py`/`test_subcommands_memory.py` suites unchanged to
   prove nothing already shipped moved.
3. **Wire the write path**: `builtin_plugins/memory/init.py`,
   `schema/remember.json`, `client_surface.py`. This is the one place a
   mistake reaches the live model-facing write path MEMORY-01 already
   shipped. Narrow check: `make test -k "memory or client_surface"`.
4. **`persona_store.py` refactor and additions.** The riskiest step: it
   changes the inside of `write_template()` and `reconcile_agents()`,
   which `stores.reconcile_indexes()` runs on every process start and
   `subcommands/persona.py` calls directly. Land it alone, prove it alone,
   before any door noun depends on it. Narrow check:
   `make test -k "persona_store or subcommands_persona"` — every existing
   test in both files must still pass with no edits to their assertions
   (only new tests added), which is the proof the extraction changed
   nothing observable.
5. **`door/nouns/agent_templates.py`** (new) + its contract test. No
   dependency beyond `persona_store._TEMPLATE`; simplest noun, proves the
   read-only/no-states/hash-id shape before the stateful ones.
6. **`door/nouns/agents.py`** (new) + its contract test. Depends on step 4.
7. **`door/nouns/memory_entries.py`** (new) + its contract test. Depends
   on step 2.
8. **`door/nouns/memory_policies.py`** (new) + its contract test. Depends
   on step 2.
9. **Wiring**: `subcommands/door.py`'s registry, `docs/console/nouns.md`.
10. Self-check (`/ponytail-review`, `/simplify`) against the whole diff,
    then `make verify`.

## Risks

**What could this break?** `memory_store.write_entry`'s SQL is the one
existing, already-shipped production write path (the model's own
`memory.remember` call) — a malformed `ON CONFLICT ... DO UPDATE` clause or
a mismatched positional-parameter count would break every memory write, not
just new ones. Guarded by keeping every new parameter defaulted and by
running the *existing* `test_memory_store.py`/`test_builtin_plugin_memory.py`
suites unmodified (only appended to) as the proof nothing already-shipped
moved. `DispatchContext` gaining a field could break any test that
constructs it positionally rather than by keyword; grepped
(`client_surface.py` is the only production constructor) and the new field
defaults to `""` specifically so a keyword-only existing test call needs no
edit. `persona_store`'s refactor could disturb `stores.reconcile_indexes()`
(runs at every store open) and `subcommands/persona.py` (the CLI); both are
covered by step 4's own narrow check running unmodified.

**Most risky step, and why.** Step 4, `persona_store.py`. It is a
refactor of two functions with production callers outside this work item
(`stores.reconcile_indexes`, `subcommands/persona.py`) that this work item
did not intend to change at all — the extraction must be behaviour-
preserving, not just "still passes its own new tests." It is ordered
before the door noun that depends on it (step 6) specifically so a
regression here is caught in isolation, against `persona_store`'s own
existing suite, rather than surfacing as a confusing failure three steps
later inside a door contract test that exercises five things at once.

**Rejected-alternatives check** (`spec.md` § Rejected alternatives, walked
against this plan): (1) no coupling of `kind` to what gets captured —
`normalize_kind` only labels, the rubric prompt text is untouched; (2) no
second memory write path and no `source="user"` anywhere in this plan;
(3) no new `atomic_write_text` helper — `update_character` uses this
codebase's own inline temp-file + `os.replace` idiom (`plugin_install.py`'s
own pattern), written once, not extracted into a shared utility for its one
caller; (4) `activate` is `persona_store.set_agent_state`, never routed
through `reconcile_agents`; (5) `activate` stays its own action, not folded
into `agents.update`. No drift found.

## Proof

- `make test -k test_memory` — `normalize_kind` covers the three valid
  words, an unknown string, and `None`.
- `make test -k test_memory_store` — `write_entry` with and without the
  new kwargs (legacy-call-shape still works); `list_entries_full` returns
  both `kept` and `forgotten` rows where `list_entries` returns only
  `kept`; `get_entry_by_id` returns `None` for a real id under the wrong
  account; `get_rubric_override_row` returns `None` with no override and a
  real row with one, `version` incrementing on a second `set`.
- `make test -k test_builtin_plugin_memory` — a `write_entry` call with an
  explicit `kind` in `value` reaches `memory_store.write_entry` with that
  kind; an absent/invalid one reaches it as `"fact"`.
- `make test -k test_persona_store` — every pre-existing test in this file
  passes with no assertion changed; new tests cover `create_character`
  (writes the file, row is `draft`, appears in `list_characters`),
  `update_character` (rewrites content, description independent of the
  file's own frontmatter, version bumps), `set_agent_state` (draft→active,
  version bumps, rejects an unknown state).
- Four new `tests/contract/nouns/test_*.py` files, `@pytest.mark.contract`,
  covering per `spec.md` § Acceptance criteria: agent create→activate→
  set-default changes `resolve_voice` for the acting account and not
  another; a conversation created before an edit keeps its prompt
  byte-for-byte; `forget` moves state and disappears from
  `memory_store.list_entries` while staying in `list_entries_full`; the
  `memory_policies` no-override default row; cross-account `404` on
  `memory_entries`/`memory_policies` `get`/`act`.
- `make verify` ends `VERIFY OK`.
- A curl transcript (create agent → activate → set-default → memory list →
  forget) against `sadana door serve`, captured for `review.md`'s Evidence
  at the Deploy stage — not part of this stage's own proof, named here so
  it isn't forgotten.
