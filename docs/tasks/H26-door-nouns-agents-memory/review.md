# Review: door nouns for agents and memory (from plan.md 2026-09-14)

Reviewed: 2db0b06..working tree (uncommitted) — 12 tracked files changed
(+792/-30), 8 new files (4 `src/sadana/door/nouns/*.py`, 4
`tests/contract/nouns/test_*.py`).

Reviewer context: same session as build, but the three passes and the
compliance pass below were delegated to a fresh subagent with no memory of
this session — given only `intent.md`/`spec.md`/`plan.md` and told to
regenerate the diff itself. I independently re-read and re-verified every
Important finding it returned against the actual source before writing them
here (file/line citations below reflect my own re-read, not a copy of its
report).

Second opinion: none beyond the cold subagent pass — not repeated a third
time by design.

## Evidence

First pass, before either concurrency fix below (1356 passed):

```
$ make verify
docs/tasks/H26-door-nouns-agents-memory: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
shellcheck................................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 75 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
..........................................................
  two conversations, concurrently: 431 ms for two 200 ms turns
.  one conversation, twice: 457 ms for two 200 ms turns
............. [  5%]
........................................................................ [ 10%]
........................................................................ [ 15%]
........................................................................ [ 21%]
........................................................................ [ 26%]
........................................................................ [ 31%]
........................................................................ [ 37%]
........................................................................ [ 42%]
........................................................................ [ 47%]
........................................................................ [ 53%]
........................................................................ [ 58%]
........................................................................ [ 63%]
........................................................................ [ 69%]
........................................................................ [ 74%]
........................................................................ [ 79%]
........................................................................ [ 84%]
........................................................................ [ 90%]
........................................................................ [ 95%]
............................................................             [100%]
1356 passed in 111.80s (0:01:51)
TESTS OK
VERIFY OK
```

Re-run after fixing Important findings #1 and #2 below, plus one new
regression test (1357 passed):

```
$ make verify
docs/tasks/H26-door-nouns-agents-memory: all present artifacts valid
CHAIN OK
[...pre-commit hooks unchanged from above, all Passed...]
LINT OK
Success: no issues found in 75 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
..........................................................
  two conversations, concurrently: 370 ms for two 200 ms turns
.  one conversation, twice: 441 ms for two 200 ms turns
............. [  5%]
........................................................................ [ 10%]
........................................................................ [ 15%]
........................................................................ [ 21%]
........................................................................ [ 26%]
........................................................................ [ 31%]
........................................................................ [ 37%]
........................................................................ [ 42%]
........................................................................ [ 47%]
........................................................................ [ 53%]
........................................................................ [ 58%]
........................................................................ [ 63%]
........................................................................ [ 68%]
........................................................................ [ 74%]
........................................................................ [ 79%]
........................................................................ [ 84%]
........................................................................ [ 90%]
........................................................................ [ 95%]
.............................................................            [100%]
1357 passed in 99.66s (0:01:39)
TESTS OK
VERIFY OK
```

## Compliance pass

**File list vs. plan.md.** Exact match — every file plan.md's `## Files
that change` names is touched, and nothing else is (`git status --short`
diffed against the list by hand).

**plan.md Proof / narrow-check items.** Plan.md's "Order of work" names a
narrow `make test -k ...` check per step rather than a single `## Proof`
section; each is discharged by tests present in the diff: step 1 by
`tests/unit/test_memory.py:130-148`; step 2 by
`tests/unit/test_memory_store.py:299-358`; step 3 by
`tests/unit/test_builtin_plugin_memory.py:77-116`; step 4 by
`tests/unit/test_persona_store.py:290-405` (existing assertions
untouched, new ones added, which is the promised proof the extraction
changed nothing observable); steps 5-8 by the four new
`tests/contract/nouns/test_*.py` files, all marked `@pytest.mark.contract`.

**spec.md Acceptance criteria.** Walked each: agent create→activate
visible in `list` (`tests/contract/nouns/test_agents.py:73-90`);
`set-default` scoped to one agent, not the other
(`test_agents.py:94-145`); byte-stable prompt across an edit
(`test_agents.py:233-271`); `forget` state + recall-visibility split
(`tests/contract/nouns/test_memory_entries.py:101-121`); `memory_policies`
synthetic-default-row behavior (`tests/contract/nouns/test_memory_policies.py:66-70`);
cross-account access is `404` (`test_memory_entries.py:153-169`,
`test_memory_policies.py:135-151`). All satisfied.

**spec.md Rejected alternatives.** No drift found: `normalize_kind` only
labels and never gates capture (`src/sadana/memory.py:187-191`); no
`source="user"` was introduced; `update_character` writes via an inline
temp-file + `os.replace`, not a new shared helper
(`src/sadana/persona_store.py:701-711`); `activate` is its own
`set_agent_state`, never routed through `reconcile_agents`.

**Five design principles.** (a) Prior art: spec.md's reference audit
against hermes's profile router and memory plugins is specific and
adopts only the applicable pattern — no gap. (b) Reduce the number of
bets: no new registry introduced (`BUILTIN_TEMPLATES` stays one entry,
`MEMORY_KINDS` stays closed) — except see Important #3, an
un-sanctioned-by-intent capability (irreversible delete) was added at
design time without that scope having been asked for. (c) Plugins vs
core: nothing here is a plugin seam; correctly below that line. (d)
Least step-cost: see Important #1 — `agents.list`/`get` were given an
unconditional full reconciliation pass on every request, and the cost
question turns out to also be a correctness one. (e) Minimise mutable
state: `AgentRow`/`MemoryEntryRow`/`RubricRow` are frozen dataclasses
returned by value; no new mutable module state. Clean.

## Findings

Ran all three passes (Bugs, Security, Compliance) against the working-tree
diff described above and reconciled it against `intent.md`, `spec.md`
Acceptance criteria and Rejected alternatives, `plan.md` Files that change
and Proof/narrow-check items, and the five design principles (detail in
the Compliance pass section above). Four Important findings surfaced, all
independently re-verified against source before being written down here;
detail below.

### Important

**#1 and #2 fixed in this branch after Adam's decision below** — see the
`Fixed` note under each.

- [Bugs/Compliance] `agents.list` and `agents.get` call
  `persona_store.reconcile_agents(ctx.conns.writer, ...)` on every request
  (`src/sadana/door/nouns/agents.py:73-74`, called at lines 111 and 128).
  Inside `reconcile_agents`, the first read —
  `src/sadana/persona_store.py:304-307`, a raw
  `conn.execute("SELECT id, name, content_sha256, version FROM agents")`
  — runs directly on the passed connection *before* the function enters
  `write_txn`, taking no lock. The door passes `ctx.conns.writer`, the
  single process-wide writer connection (`src/sadana/stores.py:121`,
  `check_same_thread=False`), and `door/serve.py:106` serves requests on
  `ThreadingHTTPServer` — one thread per request. A `GET
  /v1/agents` on one thread can now run this raw `SELECT` on the writer
  connection while another thread is mid-`BEGIN IMMEDIATE` inside
  `write_txn` for an unrelated write on any noun. This is exactly the
  hazard CLAUDE.md names: "A read issued on the writer while another
  thread is inside `write_txn` runs inside that thread's transaction and
  sees rows it may roll back... A read on any path that can run
  concurrently goes through `stores.Connections.reader()`; `runtime.conn`
  is for writes." Before this diff `reconcile_agents` only ran at
  single-threaded startup or from the CLI; this call site is what turns a
  latent shape into a live race on the door's hot path. Consequence: under
  concurrent door traffic, `agents.list`/`get` can intermittently raise
  sqlite3 errors or read against a transaction that later rolls back.

  **Fixed.** `reconcile_agents` (`src/sadana/persona_store.py:278-332`)
  now takes its comparison read inside `write_txn` instead of a bare
  `conn.execute` beforehand — every call takes the write lock and opens a
  transaction, a no-op `BEGIN`/`COMMIT` when nothing changed. No test can
  assert the absence of a race deterministically; the existing
  `reconcile_agents` unit tests and every `test_agents.py` contract test
  still pass unchanged (1357 passed, Evidence above).

- [Bugs] `persona_store.update_character`'s no-op branch has the same
  hazard on a second path: when a `PATCH` body names neither
  `system_prompt` nor `description`, `src/sadana/persona_store.py:494-495`
  runs `conn.execute("SELECT id FROM agents WHERE name = ?", (name,))`
  directly on `conn`, which `door/nouns/agents.py:207-213` passes as
  `ctx.conns.writer` — again outside `write_txn`, unlocked, reachable by
  any client sending an empty-body `PATCH /v1/agents/{id}`. No test
  exercises this branch: every `test_update_character_*` case in
  `tests/unit/test_persona_store.py` passes `content` or `description`,
  never both `None`, so the gap is untested as well as unlocked.

  **Fixed.** The no-op branch (`src/sadana/persona_store.py:494-500`) now
  wraps its read in `write_txn` too, mirroring the branch immediately
  below it that already did. Added
  `test_update_character_with_neither_field_is_a_no_op`
  (`tests/unit/test_persona_store.py`) to close the "untested" half of
  this finding — it did not exist before this fix and fails without it
  in the sense that the branch it exercises previously ran unlocked.

- [Compliance] `memory_entries.remove` wires `DELETE
  /v1/memory_entries/{id}` to a genuine, irreversible purge
  (`src/sadana/door/nouns/memory_entries.py:107-116` →
  `memory_store.delete_entry`, exercised by
  `tests/contract/nouns/test_memory_entries.py:141-149`). intent.md's
  "Proposed outcome" asks only to "stop a remembered thing from being used
  **without erasing the record of it**" — that's `forget` — and the intent
  never asks for a hard-delete capability on memory entries; the only
  deletion it discusses at all is declining it for agents ("Deleting a
  voice outright is not part of this"). spec.md does sanction `remove`
  (`spec.md:244-245`, "already correct today"), so it isn't hidden from the
  chain, but it's a capability intent.md never asked for and that runs
  counter to the outcome intent.md promised. `docs/console/nouns.md`'s
  `memory_entry` section (lines 119-143) compounds this: it documents only
  `forget` under Actions and states "the row survives, it just stops being
  recalled" — an operator reading the console-facing doc this item itself
  updates would not learn that a real purge action exists. Either the
  intent needs to be revisited to actually cover this capability, or
  `remove` should be declined the same way `agents.remove` is, and the doc
  gap fixed either way.

- [Compliance] `docs/console/nouns.md` gained a duplicate, empty `##
  artifact` heading (lines 163-164, immediately before the real `##
  artifact` / `Served by: not yet (H20)` section at line 165). Small,
  but this file is a named deliverable of this work item and now ships
  with a doc bug.

### Nits

- [Bugs] Several contract tests
  (`tests/contract/nouns/test_agents.py:82-83,103-104,238`) pass
  `system_prompt` values that already contain their own `---`-delimited
  frontmatter into a field `persona_store.py:542-560` always wraps in a
  second frontmatter layer. It still parses, but the test data isn't
  representative of what a real client sends (`nouns.md` describes
  `system_prompt` as plain text), which slightly obscures what the test is
  proving.
- [Bugs] `agent_templates.py`'s `get`/`list` do an O(n) scan over
  `BUILTIN_TEMPLATES` to match a hashed id (`agent_templates.py:73-75`)
  rather than a dict lookup — trivial with one entry and explicitly
  sanctioned by spec.md as "no registry," just worth knowing it stops
  being O(1) if a second template ever lands.
- [Bugs] `memory_entries.py`'s render defaults (`row.kind or "fact"`,
  `row.source or "conversation"`) and `memory.normalize_kind` encode the
  same "NULL → default" mapping in two places — one read-time, one
  write-time, acceptable duplication today but worth collapsing if a
  fourth kind is ever added.

## Decision

Approved by Adam, 2026-09-14, with Important findings #1 and #2 (the two
unlocked-writer-read concurrency bugs) fixed in this branch — see the
`Fixed` notes above and the second `make verify` run in `## Evidence`.
Important findings #3 (`memory_entries.remove`'s undeclared purge
capability) and #4 (the duplicate `## artifact` heading in
`docs/console/nouns.md`) are left open, not fixed in this branch.
