# Review: You shouldn't have to reintroduce yourself every time (from plan.md 2026-09-10)

Reviewed: working tree (uncommitted) vs. HEAD (c299819) — 9 tracked files
modified, 8 new source/test files (plus the artifact chain itself). No base
branch/commit exists yet for this item; `git diff HEAD` is the whole change.

+231/-14 across tracked files, plus new files:
`src/sadana/memory.py`, `src/sadana/memory_store.py`,
`src/sadana/builtin_plugins/memory/{plugin.toml,init.py,schema/remember.json}`,
`src/sadana/subcommands/memory.py`,
`tests/unit/test_memory.py`, `tests/unit/test_memory_store.py`,
`tests/unit/test_builtin_plugin_memory.py`,
`tests/unit/test_subcommands_memory.py`.

Reviewer context: **fresh session** — no prior conversation, no memory of
writing this code. This review was run cold, exactly as deploy-skill asks
for when a genuinely separate reviewing session is available; nothing here
was carried forward from build.
Second opinion: none — ran during build (self-check, per project convention),
not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/MEMORY-01-write-recall-and-forget: all present artifacts valid
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
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 36 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 12%]
........................................................................ [ 25%]
........................................................................ [ 38%]
........................................................................ [ 51%]
........................................................................ [ 64%]
........................................................................ [ 77%]
........................................................................ [ 90%]
....................................................                     [100%]
556 passed in 14.83s
TESTS OK
VERIFY OK
```

## Scope check against plan.md § Files that change

Plan.md names 17 files (10 source, 7 test). The diff touches all 17, plus
two the plan does not name:

- `CLAUDE.md` — a new glossary/rule line defining `AccountKey` vs.
  `ConversationKey`.
- `src/sadana/subcommands/gateway.py` — `cmd_gateway_run` gains
  `memory_store.ensure_plugin_seeded(plugins._plugins_root())` and
  `memory_store.ensure_schema(conn)` before the daemon starts serving.

Both are real, load-bearing changes (the second is required for the gateway
path to work at all — `gateway_dispatch.handle_inbound`'s own new docstring
says its `memory_store` schema is "assumed already present, ensured once by
`cmd_gateway_run`"), not accidents. See Findings.

One pre-existing untracked file, `docs/audits/2026-09-10/findings.md`, is
unrelated to this item and is excluded from this review as instructed.

## Findings

Three passes run: Bugs, Security, Compliance. Bugs is clean — no incorrect
logic found beyond the approval-gate consequence folded into the Compliance
findings below. Security is clean — every SQL statement in `memory_store.py`
uses parameterized `?` placeholders, the identity channel's merge-last
security property has its own direct test
(`test_plugin_dispatch.py::test_build_dispatch_memory_context_wins_over_a_model_supplied_value`),
and nothing new touches the network or shells out. Four Important findings
below, all Compliance; two Nits.

### Important

- **[Compliance]** Two files are touched that plan.md's § Files that change
  does not name: `CLAUDE.md` and `src/sadana/subcommands/gateway.py`. Per
  this project's own deploy policy, a file touched that the plan did not
  name is always an Important finding regardless of how small or
  reasonable the change is. `gateway.py`'s change is not cosmetic — it is
  the only place `cmd_gateway_run` (the real gateway entrypoint) ensures
  the memory schema and seeds the plugin before `handle_inbound` (which
  `gateway_dispatch.py:handle_inbound`'s own new docstring assumes already
  ran) starts being called. The plan's own dependency/risk analysis in
  step 7 and the "What could this change break?" section never mentions
  `gateway.py`, so nothing in Build's own risk accounting covers it either.

- **[Compliance]** Requirement 3 ("no explicit ask... has to work from the
  ordinary flow of a conversation") is not actually met by the two real
  callers this item wires up. Neither `src/sadana/subcommands/chat.py`'s
  `_chat_loop` nor `src/sadana/gateway_dispatch.py`'s `handle_inbound`
  passes a custom `approve=` into `plugin_dispatch.build_dispatch(...)`;
  both fall through to `plugin_manifest._default_approve`
  (`src/sadana/plugin_manifest.py:271-281`), which blocks on a real `input()`
  y/N prompt for every `call` node — including `memory.remember`. spec.md's
  own § Concerns names this exact conflict and states the fix explicitly:
  "whoever wires `build_dispatch()` for a real deployment supplies their
  own `approve` — permissive for the first-party `memory` plugin,
  interactive for everything else... the single thing a build-stage
  implementer should not quietly paper over by making `_default_approve`
  permissive in general." The diff does neither — it leaves `approve`
  unset at both real call sites, so it isn't "papered over in general" (a
  worse mistake spec.md warned against) but the narrower fix spec.md
  actually asked for was never made. In `chat.py` this means every memory
  capture blocks the terminal on a y/N prompt, exactly the friction
  intent.md rules out. In `gateway_dispatch.py` — a headless daemon with
  no attached terminal — `input()` raises `EOFError` immediately; that
  propagates through `run_graph`'s own blanket `except Exception`
  (`plugin_manifest.py:388-389`) as a `failed_node`, so it doesn't crash
  the turn (requirement 5 still holds), but it means the memory plugin can
  never successfully write in this path at all. No test in the diff
  exercises capture through either real caller's actual `approve` wiring
  (all plugin/dispatch tests supply their own permissive stub), so nothing
  caught this at the level spec.md was worried about.

- **[Compliance]** Acceptance criterion 4 in spec.md — "A simulated
  `sqlite3.Error` from `memory_store.write_entry` inside the plugin's
  `call` node results in a `failed_node` DagResult, not a raised exception
  out of `dispatch()`/`run_turn()`" — is not discharged by any test.
  `tests/unit/test_builtin_plugin_memory.py` has a test for a *missing*
  `_sadana_memory_ctx` (a `KeyError`), but no test monkeypatches
  `memory_store.write_entry` to raise `sqlite3.Error` and asserts
  `run_graph`'s result. `run_graph`'s blanket `except Exception` almost
  certainly does catch it (it would catch any `sqlite3.Error`, a subclass
  of `Exception`), but this criterion asked to be proven, not inferred,
  and plan.md's own § Proof list for `test_builtin_plugin_memory.py`
  (lines 174-180 of plan.md) only lists the missing-context case too — the
  gap originates in plan.md silently narrowing spec.md's criterion, not
  only in what got built.

- **[Compliance]** Acceptance criterion 2 in spec.md — "Deleting an entry
  via `sadana memory forget` removes it from a subsequently created
  conversation's recall text" — has no single test that exercises it
  end-to-end. `test_memory_store.py::test_delete_entry_removes_it` proves
  the store layer; `test_subcommands_memory.py::test_cmd_memory_forget_removes_an_entry`
  proves the CLI command removes an entry from a later `list`. Neither, nor
  any test in `test_subcommands_chat.py` or `test_gateway_dispatch.py`,
  forgets an entry and then creates a new conversation to check it is gone
  from `system_prompt`. The criterion is satisfiable only by composing
  three separately-tested units (`delete_entry` empties `list_entries`,
  `render_recall(())` is `""`, and a non-deleted entry does reach
  `system_prompt` per the existing recall tests) — reasonable, but it means
  nobody can point at one test and say "this is criterion 2," which is what
  the compliance pass is supposed to be able to do.

### Nits

- `memory.system_message_for()` (`src/sadana/memory.py:63-68`) is a
  combinator not named anywhere in spec.md's § Interface or plan.md's
  Files-that-change description of `memory.py` (both list only
  `render_recall`/`render_capture_guidance`, joined at each call site via
  `"\n\n".join(filter(None, [...]))`). What's shipped is a reasonable
  dedup of that exact join logic across `chat.py` and `gateway_dispatch.py`
  rather than a real design change, but it is still an undocumented
  addition to the pure module's public surface.
- `tests/unit/test_builtin_plugin_memory.py:26` imports a private name,
  `memory_store._BUILTIN_PLUGIN_SOURCE`, to locate the shipped plugin
  directory for `validate()`/`run_graph()` calls. Not a source-reading
  violation (testing-conventions' actual concern), just a test reaching
  past the module's public API for a path it could instead take as a
  fixture-local constant.

## Compliance pass detail

**Proof items in plan.md**: all discharged except the `sqlite3.Error`
simulation named above (test_builtin_plugin_memory.py's own § Proof entry
never asked for it either — the narrowing happened before code was
written). Every other Proof line has a matching test: `test_memory.py`
(recall/guidance/account-key/rubric-fallback), `test_memory_store.py`
(natural-key upsert, cross-account isolation, delete, rubric override,
plugin seeding + idempotency), `test_builtin_plugin_memory.py` (validates +
writes through `run_graph` + fails safely without context),
`test_plugin_dispatch.py` (merge-last wins over a model-supplied value;
`None` leaves `arguments` untouched), `test_subcommands_memory.py`
(list/forget/set-rubric + nothing-stored message),
`test_subcommands_chat.py` and `test_gateway_dispatch.py` (per-account
recall, cross-account isolation, thread-vs-chat account scoping).

**spec.md § Acceptance criteria**: checked item by item.
1. Satisfied — `test_subcommands_chat.py::test_cmd_chat_new_conversation_recalls_the_given_accounts_memories`
   / `..._never_recalls_a_different_accounts_memories`;
   `test_gateway_dispatch.py::test_handle_inbound_shares_recall_across_threads_of_the_same_account`
   / `..._never_recalls_a_different_chat_ids_memories`.
2. Not directly tested — see Important findings.
3. Satisfied via composed unit tests: `test_memory.py::test_render_capture_guidance_*`
   (default always present, override on top) +
   `test_memory_store.py::test_rubric_override_never_leaks_across_accounts` +
   `test_subcommands_memory.py::test_cmd_memory_set_rubric_persists`. No
   single test proves the full chain, but the seam is three lines
   (`system_message_for`) and each half is proven; weaker than 1 but not
   flagged as its own finding.
4. Not tested — see Important findings.
5. Satisfied — `test_memory_store.py::test_write_entry_same_key_updates_not_duplicates`
   / `test_rubric_override_same_account_updates_not_duplicates` (both via
   `ON CONFLICT`, i.e. the primary key doing the work, not app-level dedup).
6. Satisfied — no test in the diff reads `.py` source; the one file-content
   read (`test_ensure_plugin_seeded_is_idempotent`) is a `.toml` marker
   checked for a copy operation's idempotency, not code inspected for
   behavior.
7. Satisfied — `conversation.py`, `plugins.py`, `plugin_manifest.py` are
   untouched (confirmed via `git status`); the only kernel-adjacent diff is
   `plugin_dispatch.build_dispatch()`'s one new `memory_context` parameter.
   (spec.md's own criterion 7 text says "two new optional... parameters" —
   a pre-existing wording artifact in the approved spec, not a code issue;
   the diff and plan.md both correctly describe one.)

**spec.md § Rejected alternatives**: re-checked against the diff, none
reopened. No `MemoryProvider`/external SaaS call anywhere in the diff. No
kernel hook analogous to `record_turn`/`record_plugin_run` — capture is
exclusively the plugin's own `call` node. `plugin.toml` has one node,
`write`, kind `call`, no `ask`/`route` — no judge-then-write DAG.
`schema/remember.json` has exactly `entry_key`/`content` — no type/kind
field, no fixed taxonomy. The rubric override lives in its own table,
`memory_rubric_overrides`, read separately from `default_rubric()`'s env
var — not folded into the plugin's shipped file.

**The five design principles**:
1. *Learn from the reference first* — spec.md's own reference-corpus
   section (read during design, not re-litigated here) audits
   `memory_provider.py`, `curator.py`, and `honcho/session.py`, and the
   code matches what was declined/adopted there (no external memory
   provider, no turn-loop hooks, capture as a plugin call).
2. *Reduce the number of bets* — recall rides an already-unused parameter;
   capture rides the existing plugin/dispatch seam; the rubric is one env
   var. The one new mechanism, `ensure_plugin_seeded`'s in-repo
   `builtin_plugins/` + copy-into-`plugins_root` idiom, is flagged as new
   ground in plan.md's own Risks section (not a spec.md rejected
   alternative reopened) and is a small, reversible bet (a directory copy
   behind an existence check), not an architectural one.
3. *More plugins, not more core* — capture is entirely a plugin; the only
   core-adjacent change is one new optional `build_dispatch()` parameter,
   the same shape OBSERVABILITY-01 already used for `record_turn`/
   `record_plugin_run`.
4. *Catch the scenario at the least step-cost* — the recall/rubric lookup
   in `chat.py`/`gateway_dispatch.py` only runs on the new-conversation
   branch, not every turn; `memory_context` construction on every turn is
   a cheap dict merge, not a lookup.
5. *Minimise mutable state* — `DispatchContext` is frozen; no new mutable
   kernel state; `AccountKey` is recomputed at each call site, never cached.

## Decision

Approved by Adam, 2026-09-10. The four Important findings (two files outside
plan.md's scope, the unresolved approval-gate friction on both real callers,
and the two undischarged acceptance-criteria tests) were presented and are
shipping unresolved, by this decision, not by omission.
