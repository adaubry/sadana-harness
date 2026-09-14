# Spec: door nouns for agents and memory

Intent: docs/tasks/H26-door-nouns-agents-memory/intent.md

Author: Adam Aubry (product owner). Status: approved.

## Requirements

1. `door/nouns/agents.py` serves `agent`/`agents`/`agt`: fields `name`,
   `description`, `template_id?`, `system_prompt`, `rendered_prompt`
   (read-only), `is_default` (read-only); states `draft`/`active`; actions
   `activate` (`draft`→`active`) and `set-default` (from `active`,
   consequential); filterable `state`, `template_id`, `created_at`. (intent
   "Proposed outcome": see, start from a template, activate, default.)
2. `door/nouns/agent_templates.py` serves `agent_template`/`agent_templates`/
   `tmpl`, read-only: `name`, `description`, `system_prompt`. (intent
   "Proposed outcome": start a new voice from a template.)
3. `door/nouns/memory_entries.py` serves `memory_entry`/`memory_entries`/
   `mem`: fields `content`, `kind` (`decision`/`preference`/`fact`),
   `source`, `conversation_id?`; states `kept`/`forgotten`; action `forget`
   (`kept`→`forgotten`, consequential); filterable `state`, `kind`,
   `created_at`. (intent "Proposed outcome": browse, classify, stop using
   without erasing.)
4. `door/nouns/memory_policies.py` serves `memory_policy`/`memory_policies`/
   `rub`, one row per account: `rubric`, `updated_by`; `update { rubric }`.
   (intent "Proposed outcome": see and change what decides what gets
   remembered.)
5. An agent's `rendered_prompt` is computed at request time, never stored
   (intent constraint: a running conversation's prompt never changes under
   it — nothing here may create a second place that prompt text is cached).
6. Removing an agent is not offered: `agents.remove` answers
   `HARNESS_CAPABILITY_MISSING` (intent: "deleting a voice outright is not
   part of this ... told, honestly").
7. Every `memory_entries`/`memory_policies` read and write is scoped to the
   requesting account; another account's row is `404`, never `403` (intent
   constraint: memory never crosses accounts; wire.md's own visibility
   rule).
8. A `kind` label is attached to what the model already writes through
   `memory.remember`; it changes no capture behaviour and adds no second
   taxonomy (intent "Changed during planning": confirmed cosmetic).
9. `source` is recorded as `"conversation"` for every entry written from
   now on, with `conversation_id` naming the turn it came from; there is
   exactly one write path today, and no second source value is reserved for
   one that doesn't exist (intent "Changed during planning").
10. Legacy rows — written before this item — read `kind = "fact"`,
    `source = "conversation"` at render time, with no backfill migration
    (`console_fit_plan.md` §5(b), already this project's rule).

## Design

Four new noun modules under `door/nouns/`, wired into the existing
framework H19 already built; no new table, no new capability, and — H16
having already added every column this item needs — no schema migration
at all. The subsections below cover, in order: what the reference corpus
offered and what this item declined, where this sits relative to the
plugin seam, the one live-reconciliation decision H16 already flagged for
this item, then each noun in turn, then wiring and tests.

### Reference corpus, before proposing

`awk -F, '$1=="LEARNING" || $1=="PERSONA"' docs/reference/hermes_core_blocks_kind.csv`,
filtered to `production-code`.

- **`hermes_cli/web_routers/profiles.py`** (`GET`/`PUT /api/profiles/{name}/soul`,
  CLI-SHELL block) is the one place in the corpus that exposes a persona
  document over an API the way this work item exposes a character. Adopted:
  the read is a bare file read (`{"content", "exists"}` there; here, the
  standard-fields envelope PERSONA-01 already produces); the write goes
  through an atomic replace rather than a bare `write_text`, which matters
  once `update` can overwrite a file a person is actively editing — a crash
  mid-write must never leave a half-written character. Declined: hermes's
  `atomic_write_text` utility itself, and the whole "profile" concept it
  belongs to (a profile there is a separate installation directory —
  config, skills, gateways — not a voice; PERSONA-01 already settled on a
  single characters directory shared by one install, and reopening that is
  not this work item's business). `os.replace` after writing to a sibling
  temp file is this codebase's own existing idiom for an atomic replace
  (`plugin_install.py:389-396`); a new shared `atomic_write_text` helper for
  one call site would be exactly the registry-of-one CLAUDE.md's dispatch
  rule already declines for a different kind of seam, so `update` inlines
  the same two-line idiom rather than installing hermes's utility.
- **hermes's memory plugins** (`plugins/memory/{mem0,honcho,hindsight,
  holographic,openviking,retaindb,supermemory}`, `agent/memory_manager.py`,
  `agent/memory_provider.py`) — all already declined by
  `MEMORY-01/spec.md`'s own reference audit (external-SaaS backends, a
  provider abstraction with one real caller). Nothing here reopens that.
  None of them expose a REST surface a console reads from, and none tags a
  memory's provenance the way this item's `kind`/`source` does — this is
  sadana's own invention for the console, not adopted from anywhere.
- No production-code file in either block resembles a template gallery for
  a persona/character. `agent_templates` here is new, closed-list, one
  entry, matching `write_template`'s own single canned template — see
  guideline 2 below.

### Where this sits relative to the plugin seam (guideline 2)

Below the seam: nothing here is a plugin, and no registry is built for one.
`agent_templates` is a hardcoded, one-entry dict mirroring
`persona_store._TEMPLATE`, not a registry a second template "registers"
into — a second built-in template, if one is ever wanted, is a second dict
entry in a later work item, not infrastructure built now for a consumer
that doesn't exist. `memory_entries.kind` is a closed three-value set for
the same reason: a fourth value is a schema and grammar change, not a
plugin-time extension point, and nothing here pretends otherwise.

### Reconciliation: a decision H16 already flagged for this item

`stores.reconcile_indexes`'s own docstring: *"a character or plugin file
added while a long-running process is up is not indexed until that
process reopens the store. H24 and H26 own those surfaces and are where a
live refresh belongs."* The door is exactly that long-running process, so
`agents.list`/`agents.get` call `persona_store.reconcile_agents(ctx.conns.writer,
characters_dir)` before reading, inside the same `write_txn` that function
already opens — one extra write-connection round trip per read request,
cheap because reconciliation is a content-hash compare that skips every
unchanged file (guideline 3: this makes an existing step do slightly more
rather than adding a new step — a background poller, a file watcher — for
a scenario H16 already named and priced).

### `agents`: state, templates, and the file/row split

`persona_store.py`'s `agents` table (H16) already carries `description`,
`state`, `template`, `content_sha256` — added then, unwritten until now,
exactly the pattern `memory_entries.kind`/`source` also follows. No new
column, no migration.

`AGENT_STATES` widens from `{"active"}` to `{"draft", "active"}`. A
character found on disk by the ordinary directory scan (`persona new`, a
file dropped in by hand) is still `active` on sight — it isn't a draft
because nobody is mid-edit; `draft` exists only for a row this door itself
created and hasn't activated yet.

Two helpers promoted out of the file-writing and row-inserting logic
`write_template`/`reconcile_agents` already have, since both are now
needed from two call sites (`persona new`'s existing path and the door's
new one) rather than one:

- `_write_character_file(characters_dir, name, content) -> Path` — the
  path-allowlist-then-containment check and the write, factored out of
  `write_template`, which becomes a one-line caller passing `_TEMPLATE`.
- `insert_agent_row(c, name, digest, at, *, state, template) -> (id, version)`
  — the id-mint-and-INSERT-and-ledger-row branch factored out of
  `reconcile_agents`'s loop, which calls it with `state="active"`,
  `template=None` (unchanged behaviour).

`persona_store.create_character(conn, characters_dir, name, content, *,
template) -> Character` is the door's own entry point: write the file via
`_write_character_file` (refuses if it exists, same as `write_template`),
then `insert_agent_row(..., state="draft", template=template)` inside one
`write_txn`. Because the row's content hash already matches the file the
moment it's inserted, the next `reconcile_agents` pass — including the one
this same request just ran before dispatch — sees nothing to reconcile and
leaves `draft` alone; only `activate` ever moves it to `active`.

`persona_store.update_character(conn, characters_dir, name, content,
description) -> None` rewrites the file (temp file + `os.replace`, refuses
if the file is missing) and bumps the row: new hash, `version + 1`,
`updated_at`, a `changed` ledger row, plus writing `description` — the one
column reconciliation itself never touches, since a file has no
description slot to reconcile from beyond its own frontmatter, and this
item does not repurpose the frontmatter `description:` key for it.

`persona_store.set_agent_state(conn, agent_id, state) -> None` is what
`activate` calls: a version bump and a `changed` ledger row, no file
touched.

`door/nouns/agents.py`'s `act`:

- `activate`: state-gated by the router (`draft`→`active`, `consequential`
  left at its default `False` reading — no confirmation semantics attached,
  matching the intent's framing of this as the ordinary next step after
  writing a draft, not the intent's one "erases the record" caution, which
  is `remove`'s domain and `remove` is declined entirely).
- `set-default`: state-gated `active`→(no state change of its own) — calls
  `persona_store.set_selection(conn, principal_account, row.name, now=...)`.
  `consequential=True` (the intent's own word for this one), matching
  `ActionSpec`'s default.

`get`/`list` render, per row: `system_prompt` = the file's raw text;
`rendered_prompt` = `persona_store.resolve_voice(conn, principal_account,
characters_dir)` when `get_selection(reader, principal_account) == name`,
else the same raw text as `system_prompt` — computing what a
*non-selected* character would render as requires no new function
(`persona.render(persona_store.load_character(...))` would do it), but the
intent's own scope is "make what's already computed visible," not "predict
every character's counterfactual render," so this item does the cheaper
thing and says so; `is_default` = the same selection comparison. Neither is
stored (Requirement 5).

### `agent_templates`: one entry, a derived id

```python
_BUILTIN: dict[str, dict[str, str]] = {
    "default": {"description": "A blank character to customize.", "system_prompt": persona_store._TEMPLATE},
}
```

`id = "tmpl_" + hashlib.sha256(name.encode()).hexdigest()[:32]` — stable
across restarts because it's a pure function of the name, never minted via
`ids.make_id` (that would change on every call) and never stored (there is
no row). It still matches `ids.PREFIXES`'s and `ids._HEX32`'s shape, so it
validates as an ordinary id everywhere the wire format is checked, even
though `ids.make_id`/`parse_id` are never called to produce it — `nouns.md`
documents this exception explicitly, per the task's own instruction, since
it's the one id in this system that isn't uuid7-derived.

`agent.template_id` (on the `agents` noun, not this one) is rendered the
same way from the `agents.template` column's stored *name* — `console_fit_plan.md`
§5(a)'s rule applied here too: the column holds the name, the wire field is
a derived id, never the reverse.

### `memory_entries`: a second read path, because the model's is not the console's

`memory_store.list_entries` (existing) excludes `forgotten` rows on
purpose — it feeds what the model is told, and a forgotten fact must never
reach that. The door needs the opposite: a human deciding whether to
un-forget something has to be able to see it. Reusing `list_entries` for
the door would either leak forgotten facts back into recall (if the
exclusion were removed) or hide them from the one screen where "did I
actually stop this" is answered (if it stayed). Two functions, not one
with a flag threaded through two unrelated callers:

- `memory_store.list_entries_full(conn, account_key) -> tuple[MemoryEntryRow, ...]`
- `memory_store.get_entry_by_id(conn, account_key, entry_id) -> MemoryEntryRow | None`
  — `WHERE account_key = ? AND id = ?`; a row that exists under a
  *different* account and this id is `None`, which is what makes
  Requirement 7 a one-line `WHERE` clause rather than a check the door has
  to remember to run.

`MemoryEntryRow` (new, in `memory_store.py`) carries everything the door
renders (`id`, `entry_key`, `content`, `kind`, `source`, `conversation_key`,
`state`, `created_at`, `updated_at`, `version`) — kept separate from
`memory.MemoryEntry`, which stays exactly what `render_recall` needs and no
more; growing it for a door concern would make the pure recall module
carry fields recall never reads.

`kind`/`source` render with Requirement 10's defaults when the column is
`NULL` (a legacy row) — a plain `row["kind"] or "fact"` /
`row["source"] or "conversation"` at render time, not a write.

`forget` (`act`) reads the row via `get_entry_by_id`, 404s if absent
(covers both "doesn't exist" and "exists, wrong account" — Requirement 7),
412s on a missing/mismatched `If-Match` against `version`, then calls the
existing `memory_store.forget_entry`. `remove` does the same read/If-Match
dance and calls the existing `memory_store.delete_entry` — both already
correct today; this item only adds the door's own version check in front
of them, matching `approvals.act`'s own pattern (`door/nouns/approvals.py`
Design note above).

### `memory_policies`: rendering a row that might not exist

`get_rubric_override` returns `""` for "no override" today. The door
can't render an empty string as if it were a row: `get`/`list` render the
real `(id, version, updated_at, updated_by)` when
`memory_store.get_rubric_override_row(conn, account_key)` (new — the
existing function returns bare text, not enough for `id`/`version`/`ETag`)
finds one, else a synthetic row: `id = "rub_default"`, `version = 0`,
`updated_by = "system"`, `updated_at` = now (there is nothing truthful to
put there; documented here as the same kind of non-fact
`console_fit_plan.md` §5(b) already names for a legacy row's filled-in
timestamp, not a new pattern). `get`/`update` 404 if the path's `{id}`
doesn't match the account's current row id — the same one-row-per-account
shape `persona_selections` already has, reused rather than re-invented.

### `memory.py`/`builtin_plugins/memory`: tagging the one write path

`memory_store.DispatchContext` gains `conversation_key:
conversation.ConversationKey`, populated at the one call site
(`client_surface.py:475`, which already has `conversation` in scope).
`memory.py` gains one pure function:

```python
_MEMORY_KINDS = frozenset({"decision", "preference", "fact"})

def normalize_kind(kind: str | None) -> str:
    return kind if kind in _MEMORY_KINDS else "fact"
```

`schema/remember.json` gains an optional `kind` property, enum-restricted
to the same three words (`additionalProperties` stays `false`).
`builtin_plugins/memory/init.py`'s `write_entry` passes
`kind=memory.normalize_kind(value.get("kind"))`,
`source="conversation"`, `conversation_key=ctx.conversation_key` into
`memory_store.write_entry`, which gains those three keyword parameters
(defaults preserve every existing caller and every existing test).

### Wiring

`subcommands/door.py`'s `nouns={...}` dict gains four entries. No
capability entries (Requirement list above and the task's own scope: this
item adds no name to `capabilities.ALL`/`DECLARED` — none of these actions
gate on one, matching `approvals`' own `capability=None` actions).
`nouns.md` gets its four sections filled in, replacing "not yet (H26)".

### Tests

Following the precedent `H18-parked-approvals/plan.md`'s parallel-lane
amendment already set (`tests/contract/nouns/test_approvals.py`, not an
edit to the shared `tests/contract/test_console_grammar.py`): four new
files, `tests/contract/nouns/test_agents.py`,
`test_agent_templates.py`, `test_memory_entries.py`,
`test_memory_policies.py`, each a small local re-derivation of `schema`/
`door`/`_token`, exactly as `test_approvals.py` already does. This is what
"add the four nouns to the conformance run" means in a repo where two
lanes touch the door in the same window (Constraints, below) — one shared
file would be a guaranteed conflict for no reason, and the precedent
already exists.

Unit tests, named after the module they extend (testing-conventions):
`tests/unit/test_persona_store.py` (the two promoted helpers,
`create_character`, `update_character`, `set_agent_state`),
`tests/unit/test_memory_store.py` (`list_entries_full`, `get_entry_by_id`,
`get_rubric_override_row`, the three new `write_entry` kwargs),
`tests/unit/test_memory.py` (`normalize_kind`),
`tests/unit/test_builtin_plugin_memory.py` (the schema/kwargs
passthrough).

## Interface

**Inputs.** `agents.create`: `{name, system_prompt, template_id?,
description?}` — `template_id` is looked up against `agent_templates`'
own closed dict and resolved back to the built-in template's *name* before
it is stored (Requirement, `console_fit_plan.md` §5(a)); an id that
doesn't resolve is `400 VALIDATION`. `agents.update`: `{system_prompt?,
description?}`. `memory_policies.update`: `{rubric}`. `memory_entries.act
forget` / `remove`: no body, `If-Match` required.

**Outputs.** Every noun's `get`/`list`/`create`/`update`/`act` render the
standard fields (`id`, `created_at`, `updated_at`, `state`, `tags: {}`,
`harness_id`, `version`, `name` where applicable) plus the fields
Requirements 1-4 name. `agent_templates` and the `memory_policies`
synthetic-default row have no `tags`/no meaningful `updated_at`
respectively — documented in `nouns.md`, not hidden.

**Errors.** `agents.remove`, `agent_templates.create/update/remove/act`,
`memory_entries.create`, `memory_policies.create/remove/act` all answer
`HARNESS_CAPABILITY_MISSING` via the shared `nouns.unavailable()` helper.
A cross-account `memory_entries`/`memory_policies` `get`/`update`/`act` is
`404 NOT_FOUND`, never `403` (wire.md's own visibility rule, Requirement
7). `agents.create` with a name that already exists on disk is `409
CONFLICT` (mirrors `write_template`'s own `CharacterError`).

## Acceptance criteria

- [ ] An agent created through the door (`create`, then `activate`)
      appears in `persona_store.list_characters(characters_dir)`.
- [ ] `set-default` for account A changes `resolve_voice(conn, A, dir)`
      and does not change it for account B.
- [ ] A conversation created before an agent's `system_prompt` is edited
      keeps its original prompt byte-for-byte after the edit.
- [ ] `forget` moves `memory_entries.state` to `forgotten` and the entry no
      longer appears in `memory_store.list_entries` (what `memory.retrieve`
      reads) while still appearing in `list_entries_full`.
- [ ] `GET /v1/memory_policies` for an account with no override returns
      `id = "rub_default"`, `updated_by = "system"`.
- [ ] `GET`/`act` on another account's `memory_entries` row is `404`.
- [ ] `make verify` ends `VERIFY OK`.
- [ ] The four new contract-test files pass under the `contract` marker.
- [ ] A curl transcript: create agent → activate → set-default → memory
      list → forget, pasted into `review.md`'s Evidence.

## Non-goals

- Deleting an agent (Requirement 6).
- A second built-in template, or any registration mechanism for one
  (guideline 2).
- A second memory-entry write path, or a `source` value for one
  (Requirement 9; intent "Changed during planning").
- Retroactive reclassification of memory entries written before this item
  beyond the read-time default (Requirement 10).
- Recall refreshing mid-conversation, or any change to
  `create_conversation`'s byte-stability contract (Requirement 5;
  `MEMORY-01/spec.md`'s own non-goal, unchanged).
- Any new `capabilities.ALL` entry.
- `eval_harness.py` wiring.

## Rejected alternatives

1. **A fixed `kind` taxonomy that steers capture, not just labels it**
   (i.e., actually reopening `MEMORY-01`'s rejected alternative #4).
   Rejected in interview: the account's rubric stays the only thing
   deciding what's worth remembering; `kind` describes the model's output
   after the fact.
2. **A second, explicit `source="user"` write path** (a door `create` for
   `memory_entries`, or a new `sadana memory remember` CLI verb) to give
   the schema's `source` column a value besides `"conversation"` to hold.
   Rejected in interview (intent "Changed during planning"): nothing asked
   for a human-authored memory write path, and reserving a value for one
   is exactly the kind of state this project's guideline 4 says to avoid
   — it would be a bet on a feature nobody has designed yet.
3. **Hermes's `atomic_write_text`** for the character-file update.
   Declined for the reason given in Design above: one call site, this
   project's own two-line `os.replace` idiom already covers it, and a
   shared helper for one caller is a bet this repo's dispatch rule already
   declines for a related seam.
4. **Bumping `agents.state` through the generic `reconcile_agents` pass**
   instead of a dedicated `activate` write. Rejected: reconciliation's
   whole contract is "the file wins" — it has no way to learn that a
   *person*, not a file edit, decided a draft is ready, and teaching it one
   would couple a pure file-sync pass to a console-only concept it has no
   other reason to know about.
5. **A generic `PATCH`-only state machine with no `activate` action**
   (folding `draft`→`active` into `update`'s own body). Rejected: the wire
   grammar already has a dedicated actions verb for exactly this
   (state-gated, no body needed), and `update`'s job is already fully
   occupied by `system_prompt`/`description`; conflating the two would make
   an ordinary content edit silently able to also flip state depending on
   what fields happen to be present.

## Concerns

**Two parallel lanes edit `subcommands/door.py`'s `nouns={...}` dict and
`docs/console/nouns.md` in the same window** (this item and whichever
other H-item is running in the sibling worktree). Both are small,
disjoint-by-key edits to files neither lane's own plan.md called out as
contended, so the risk is a mechanical merge conflict, not a design
collision — named here so `deploy-skill`'s reconciliation pass checks it
rather than being surprised by it.

**The task names `memory.py` and `persona_store.py` as the files this item
edits; the design above also touches `memory_store.py`.** `CLAUDE.md`'s own
rule is what forces this: `memory.py` is pure, and `memory_store.py` is
where every SQL query for this block already lives (`write_entry`,
`forget_entry`, `delete_entry`, `get_rubric_override`, `set_rubric_override`
are all there, not in `memory.py`). A door noun that reads memory rows
cannot avoid calling into it. Flagged rather than silently expanded past
the stated file list.

**The `memory_policies` synthetic default row's `updated_at` is `time.time()`
at render time, not a stored fact.** It will appear to "change" on every
request until an account sets a real override, which is honest (nothing
happened at a fixed moment) but is also the one field in this whole item
that isn't stable across two reads of the same resource — worth a second
look at deploy time, though `console_fit_plan.md` §5(b) already established
that a synthetic timestamp is a floor, not a fact, for the adjacent legacy-row
case.

**No security/UX/brand skill applies.** This item has no browser surface of
its own (the console renders it, not this repo) and introduces no new
credential, secret, or subprocess boundary — `testing-conventions` and this
project's own file-placement rules (a noun module lives under
`door/nouns/`, a contract test per noun lives under `tests/contract/nouns/`)
are the whole of the applicable policy, and both are named and applied
above.
