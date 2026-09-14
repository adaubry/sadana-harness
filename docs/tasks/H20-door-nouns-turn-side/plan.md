# Plan: Serving the console's first screens through the door (from intent.md 2026-09-14)

Author: Adam Aubry (maintainer). Status: approved.

Spec: docs/tasks/H20-door-nouns-turn-side/spec.md (approved)

## Context

spec.md is already approved and highly detailed — this plan turns its 31
requirements into a concrete file-by-file build order. Six new door nouns
(`conversations`, `messages`, `runs`, `traces`, `spans`, `artifacts`), one new
client (`turn_client.py`), one small additive router.py check (resolved
during planning, not assumed — see below), two small edits to already-shared
files (`capabilities.py`, the H19 noun registry in `subcommands/door.py`),
one additive column (`conversations.name` in `conversation_store.py`,
explicitly authorized during design after a real schema gap was found), and
doc updates. No CSV-indexed reference-corpus block applies here (spec.md
already did that consultation during design — nothing in `../hermes-agent`'s
core is a token-scoped REST noun layer); the load-bearing prior art is
entirely internal: `tests/contract/fixture_noun.py`'s `WidgetsNoun` (the
proven noun-module idiom: gather account-visible rows as dicts, hand them to
`grammar.page`) and the H16/OBSERVABILITY-01 schemas already read during
design.

One finding surfaced only during this planning pass, by reading the live
code rather than trusting spec.md's own open question: `artifacts.py`'s
`download` action genuinely cannot return through `router.py`'s existing
`_handle` without one small router change — traced and resolved below.

## Files that change

**Own files (new, no coordination needed):**
- `src/sadana/door/nouns/spans.py` (new) + `tests/contract/nouns/test_spans.py` (new) + `docs/console/nouns/span.md` (new)
- `src/sadana/door/nouns/conversations.py` (new) + `tests/contract/nouns/test_conversations.py` (new) + `docs/console/nouns/conversation.md` (new)
- `src/sadana/door/nouns/artifacts.py` (new) + `tests/contract/nouns/test_artifacts.py` (new) + `docs/console/nouns/artifact.md` (new)
- `src/sadana/door/nouns/runs.py` (new) + `tests/contract/nouns/test_runs.py` (new) + `docs/console/nouns/run.md` (new)
- `src/sadana/door/nouns/traces.py` (new) + `tests/contract/nouns/test_traces.py` (new) + `docs/console/nouns/trace.md` (new)
- `src/sadana/door/turn_client.py` (new) — one function, `send()`
- `src/sadana/door/nouns/messages.py` (new) + `tests/contract/nouns/test_messages.py` (new) + `docs/console/nouns/message.md` (new)
- `tests/contract/nouns/test_golden_journey.py` (new) — cross-noun scenario
- `tests/contract/nouns/_scaffold.py` (new, added after approval — see plan.md's own note in the conversation; named `_scaffold.py` rather than `conftest.py` because a bare `from conftest import ...` from a nested test file would silently resolve to this file instead of the repo-wide `tests/conftest.py`, losing access to its `make_runtime`/`EMPTY_PLUGIN_SET` — a real collision, found while writing the first test that needed both) — shared token/`DoorContext`-building and conversation-seeding helpers for this directory's own test files only; does not edit `test_console_grammar.py`'s or `tests/conftest.py`'s scaffolding

**Shared files (edited, minimal, additive-only):**
- `src/sadana/door/router.py` — two changes, both additive:
  (1) one check in `_handle`'s trailing response branch, resolved during
  planning (confirmed against the live code, not assumed): before
  `json_response(status, result, etag=etag)`, if `isinstance(result,
  door.request.DoorResponse)`, return it unmodified. No `NounModule`
  protocol change, no signature change on any noun's `act()` — only
  `artifacts.py`'s `download` action ever returns this type, so every other
  noun (this lane's and the other lane's) is unaffected. User-approved
  explicitly, distinct from an operations-resource change declined during
  design, because this one doesn't touch the protocol shape every noun
  module satisfies.
  (2) `_route()` gains the two child-nesting cases the grammar already
  reserves but H19 left unimplemented (its own comment: "nothing registered
  in H19 declares a `parent`... Reserved, not implemented" — a real,
  load-bearing gap found while implementing `traces.py`, not anticipated by
  spec.md): 3 segments (`{plural}/{id}/{child_plural}`) routes to `list`/
  `create` on the **child** noun; 4 non-`"actions"` segments
  (`{plural}/{id}/{child_plural}/{cid}`) routes to `get` on the child. Both
  reuse every existing field/lookup/gate on `_Route`/`_handle` unchanged —
  `route.plural` becomes the child's own plural (so `ctx.nouns.get(
  route.plural)` needs no new logic), and one new field, `parent_id: str |
  None = None`, is threaded through to `noun.list`/`get`/`create` (the only
  three `NounModule` verbs that already accept it). `PATCH`/`DELETE`/an
  action on a nested child path stay unimplemented (still `404`) — no noun
  in this work item needs them, and the Protocol has no `parent_id` slot for
  those verbs to receive one anyway. User-approved explicitly as its own
  question, separate from (1).
- `src/sadana/door/capabilities.py` — one line appended to `DECLARED`: `"artifacts.download"`.
- `tests/unit/test_door_capabilities.py` (added after approval — see plan.md's own note in the conversation) — H19's own `test_this_work_item_declares_exactly_grammar_changes_inventory` asserted `declared() == ("grammar.v1", "changes", "inventory")` by exact equality; updated (renamed, assertion extended to the fourth entry) since `DECLARED` is documented as append-only and this test's whole premise is "what does `declared()` return today" — not a test being fixed, the next expected update to an append-only sequence's own test, same posture the file's own docstring anticipates.
- `tests/unit/test_door_nouns_harness.py` and `tests/contract/test_console_grammar.py` (added after approval — same reason as the capabilities test immediately above, forced by the same, already-approved `DECLARED` change): both assert `harness`'s own rendered `capabilities` field by exact tuple/list equality against the old three-entry `DECLARED`. One-line assertion update each, extending to the fourth entry — not an edit to either file's scaffolding (`door` fixture, `_token`/`_req` helpers untouched), which is what spec.md's design decision not to edit `test_console_grammar.py` was actually about.
- `src/sadana/subcommands/door.py` — `nouns={"harness": harness}` (line 98) becomes a 7-entry dict, alphabetical by plural: `artifacts, conversations, harness, messages, runs, spans, traces`. Needs new imports of all six modules.
- `src/sadana/conversation_store.py` — `_MIGRATED_COLUMNS["conversations"]` gains one tuple: `("name", "TEXT")`. `create()` also gains one optional keyword, `id: str | None = None` (added after approval — see plan.md's own note in the conversation): a real gap found during implementation, not anticipated by spec.md — `create()` always minted its own conversation id internally, independent of `conversation.key`, so "key == id for console-created conversations" (spec.md's own "Decisions already made") could not hold through the existing call path at all. Defaults to `None` (auto-mint, unchanged for every existing caller); H20's door path is the only caller that ever passes one.
- `src/sadana/client_surface.py` — `open_conversation()` and `_create()` each gain the same optional `id: str | None = None` keyword (added after approval, same reason as above), threaded straight through to `conversation_store.create(..., id=id)`. No other line in this file changes; every existing caller (CLI, webhook, `take_turn`'s own `create_as` path) keeps calling both functions with no `id` argument and is completely unaffected.
- `src/sadana/door/nouns/__init__.py` (added after approval, during the build-skill self-check pass — see plan.md's own note in the conversation) — one new function, `check_if_match(row, if_match)`, appended after the existing `unavailable()`. Found by the `/simplify` reuse review: `conversations.py`'s own `_check_if_match` was byte-identical to `tests/contract/fixture_noun.py:WidgetsNoun._check_if_match` (H19's own fixture); this is the one module every noun may already depend on (`unavailable()` already lives here), so moving the shared precondition check here — rather than a third, separate copy — is a pure reuse fix, purely additive, no existing line touched.
- `docs/reference/console_fit_plan.md` — one new bullet appended to §6, matching the existing three's style, on the `operations.resource: null` finding (user-authorized during design).
- `docs/console/wire.md` — the same bullet appended to its own §6 (the file's own docstring commits to staying in sync with `console_fit_plan.md` §6 "in the same commit").

**Not touched:** `docs/console/nouns.md` (H30 concatenates the new `docs/console/nouns/*.md` files into it later — left exactly as H19 wrote it), `operations.py`, `grammar.py`, `ledger.py`, `observability.py`, `stores.py` (no new table), `persona_store.py`, `pyproject.toml` (no new dependency), `test_console_grammar.py` (not edited — every new test file builds its own `DoorContext`).

## Order of work

1. **`conversation_store.py`** — add `("name", "TEXT")` to `_MIGRATED_COLUMNS["conversations"]`. Lowest-risk, foundational change; run the existing conversation-store tests narrowly right after, before anything depends on it, to catch a migration mistake here rather than four steps later.

2. **`spans.py`** — the simplest noun (always `[]`/`404`, no table). Proves the `NounSpec`/`NounModule` shape compiles and a minimal contract test (`list` empty, `get` 404) passes before anything more complex is attempted. `docs/console/nouns/span.md` written alongside.

3. **`router.py`'s one-line pass-through, then `artifacts.py`** — land the `isinstance(result, DoorResponse)` check first (narrow, tested with a trivial fixture `act()` returning a `DoorResponse` directly, before `artifacts.py` exists, to isolate the router change from the noun that uses it). Then `artifacts.py`: `list`/`get` over the existing `artifacts` table (scoped through `conversation_accounts` via `conversation_key`), `run_id` derived via the `plugin_runs` join (spec.md requirement 28), `create` → `HARNESS_CAPABILITY_MISSING`, and the `download` action's guard (spec.md requirement 30: `artifact_store.contains(run_dir, str(run_dir / row["ref"]))` before any open, `400 VALIDATION` on escape, then a real `DoorResponse` built directly by `artifacts.py` and returned through the new pass-through). Test: the three escape refusals (`..`, absolute, resolved-outside) plus a real download of a small seeded file, asserting the actual response body bytes and headers — not just a 200 status.

4. **`conversations.py`** — `list`/`get`/`create`/`update` (PATCH `{name}`)/`act` (`archive`, `unarchive`, `rename`), account-scoped via `conversation_accounts`, `agent_id` resolved both directions through the `agents` table (spec.md requirements 3, 6), `message_count`/`last_message_preview` derived. `create` calls `client_surface.open_conversation` — the first place this plan touches `client_surface`, ahead of the turn-side work in step 7, so a wiring mistake there surfaces on the simpler, synchronous path first. Test: create, list, get, rename (stale `If-Match` → 412, correct → 200 + ledger row), archive → unarchive, archive twice → 409, cross-account 404.

5. **`runs.py`** — `list`/`get` over `turn_runs` only, every derived field from spec.md requirement 22 (`conversation_id`, `message_id` via the `messages.run_id` reverse lookup, `agent_id`, `plugin_id: null`, `exit_reason` mapped with the `failed_node` override, `duration_ms`, `iterations`), `stop` action declared but capability-gated off (501). Built and tested against **hand-seeded** `turn_runs`/`messages`/`plugin_runs` rows (direct SQL inserts in the test, not a live turn) — this noun's own correctness does not depend on `messages.py`/`turn_client.py` existing yet, so it is proven standalone first.

6. **`traces.py`** — `list`/`get` over `plugin_runs`, scoped via the parent run id (resolved to `(conversation_key, turn_seq)` through `turn_runs`), `root_node` rendered as the row's own `entry` value (spec.md requirement 25). Same hand-seeded-rows testing strategy as step 5.

7. **`turn_client.py` + `messages.py`** — the highest-risk step; see § Risks. `turn_client.send()` first, alone, with a one-line unit test (calls `client_surface.take_turn` and nothing else — checked by grepping the file's own imports, not just reading it). Then `messages.py`: `list`/`get` (scoped via the parent conversation), `create` (the turn: load-and-validate-state, call `turn_client.send` via `asyncio.run` inside the thread-pool worker, the before/after `run_id` backfill per spec.md requirement 15, the failure path setting `state="failed"` with no diagnostic storage, returning `problems.make("INTERNAL", outcome.diagnostic)` on failure and the user-message row on success). `turn_seq` rendered via the `run_id → turn_runs.turn_seq` join (requirement 19). Test with a **stubbed `model_access.send`** (this project's own established pattern for exercising a real turn without a network call — confirm the exact stub shape used by existing turn-path tests before writing this one, rather than inventing a new one) so the whole path runs for real, including `client_surface.take_turn`, `conversation_store`, and `observability.py`'s `turn_runs` write — only the model call itself is faked.

8. **`capabilities.py` + `subcommands/door.py`** — the two shared-file edits, done together since they are both "wire the finished nouns in," now that all six exist. `capabilities.py`: append `"artifacts.download"` to `DECLARED`. `subcommands/door.py`: import all six modules, expand the `nouns={}` dict to seven entries, alphabetical. Narrow check: `sadana door serve --help` still parses; `capabilities.declared()` still passes its own subset-of-`ALL` assertion at import.

9. **`test_golden_journey.py`** — builds its own seven-noun `DoorContext` (reusing the `auth.generate_dev_keypair`/`Verifier`/`stores.Connections` idiom from `test_console_grammar.py`'s `door` fixture, re-declared locally per the amendment, **not** imported from that file — see spec.md § Design). Works the full acceptance-criteria checklist from spec.md in one scenario: list (empty) → create conversation → list (one, `message_count` 0) → create message → operation `running` → wait → operation `succeeded`, **`resource` asserted `is None` explicitly** → messages list shows user+assistant in order, both `run_id`-tagged → run `done` with correct `message_id` → rename (stale `If-Match` → 412, correct → 200) → archive → second archive → 409 → foreign account's token → 404 → message on archived conversation → 409 → the three artifact-guard refusals → a failed turn's assistant row (`state: "failed"`, operation `error.detail` carries the diagnostic).

10. **Doc sync** — `docs/reference/console_fit_plan.md` §6 and `docs/console/wire.md` §6 both gain the same new bullet, in this same step, matching `wire.md`'s own stated sync rule. Written last, once the golden-journey test has actually proven `resource: null` is the real, observed behaviour rather than a prediction.

11. **Self-check + verify** — `/ponytail-review` and `/simplify` against the full diff; apply what's worth taking now, note what isn't (per build-skill's own triage); then `make verify`, pasted as Evidence.

## Risks

**What could this change break, that already works?**
- `conversation_store.py`'s `_MIGRATED_COLUMNS` dict — an existing, already-guarded pattern (`migrate_columns` is idempotent, `PRAGMA table_info`-checked); every existing caller of `conversation_store.open_store`/`ensure_schemas` runs this on every open, so a malformed tuple here would break every CLI command, not just the door. Mitigated: step 1 is isolated and narrow-tested before anything else is built on top of it, exactly as H19's own plan did for its own `stores.ensure_schemas` edit.
- `capabilities.py`'s `DECLARED` tuple and `subcommands/door.py`'s `nouns={}` dict are both files the parallel lane may also be editing right now, from the same starting commit. Mitigated: both edits are pure appends (one line, six lines respectively) in the amendment's prescribed alphabetical-by-plural order, so a conflict — if the other lane also appends — resolves as "keep both sides, sort," per the amendment's own protocol. Neither file's existing lines are touched.
- `router.py`'s `_handle` gains one `isinstance` check ahead of its existing `json_response` call, in its trailing response branch — the single most central file in the door. Every other noun's `act()` (this lane's `conversations.archive`/`unarchive`/`rename`, `runs.stop`; the other lane's, whatever it declares) flows through the exact same branch today and must keep doing so unchanged. Mitigated: the check is purely additive (`if isinstance(...): return result` before the existing line, never intercepting the `Mapping` case), and step 3 tests it in isolation with a throwaway fixture noun before `artifacts.py` exists, so a regression in the common path shows up immediately rather than being masked by `artifacts.py`'s own tests.
- `messages.create()` running inside the door's thread pool via `asyncio.run()` — if a *second* event loop is somehow already running on that worker thread (it shouldn't be; each `ThreadPoolExecutor` worker is a plain OS thread with no loop of its own), `asyncio.run()` raises rather than silently misbehaving, so this fails loudly if the assumption is wrong, not quietly.

**Which step is riskiest, and why?** Step 7, `turn_client.py` + `messages.py` — for three independent reasons, which is exactly why it is scheduled *after* every simpler, standalone-testable noun (steps 2-6) rather than first:
1. It is the only step that drives a real `client_surface.take_turn()` call end-to-end from door code — everything upstream of it (`conversation_store`, `plugin_dispatch`, `observability.py`) is existing, already-shipped machinery this artifact must not modify, so a mistake here is entirely on the calling side, not the callee's.
2. The `run_id` backfill (spec.md requirement 15) has the narrow, accepted race already named in spec.md § Concerns — implemented exactly as specified there (before/after `conversation_store.load` reads, no lock held across the `send()` call), not "improved" into something spec.md didn't approve.
3. It is the one place this plan's own novel finding (operations.resource: null) becomes observable, not just theoretical — step 9's golden journey is what actually proves it, which is why the doc-sync step (10) is sequenced *after* it rather than done speculatively earlier.

**Where might this plan drift back toward something spec.md already rejected?** Checked against spec.md's own § Rejected alternatives:
- No router.py/operations.py protocol change is planned anywhere in this order — steps 3 and 7 both work within the existing `_dispatch`/`run_bounded` shape spec.md's adopted design requires; step 3's router.py change is additive to `_handle`'s response construction, not a protocol change spec.md rejected (that rejection was specifically about a `NounModule`/`resource`-hint protocol addition, a different thing).
- No `messages.tags_json` column, anywhere in this plan — step 7 writes only `state = "failed"` on the message row, per spec.md requirement 16.
- No re-minting of `plugin_runs` ids under `trc_` — step 6 (`traces.py`) declares `prefix="run"`, matching the real data, per spec.md's own resolution.
- No custom default order for `messages.list` — step 7 leaves `grammar.parse_list_params`'s default untouched.

## Proof

- `tests/contract/nouns/test_spans.py`, `test_artifacts.py`, `test_conversations.py`, `test_runs.py`, `test_traces.py`, `test_messages.py` — each covering its noun's own list/get/create/update/act paths and error cases named in spec.md's per-noun requirements, all green under the `contract` marker.
- `tests/contract/nouns/test_golden_journey.py` — the full scenario in step 9 above, green, with the `resource is None` assertion present and passing (not merely "no crash").
- `docs/console/nouns/{conversation,message,run,trace,span,artifact}.md` — each filled per its noun's spec.md requirements; `scripts/artifact.py check` clean.
- `docs/reference/console_fit_plan.md` §6 and `docs/console/wire.md` §6 — both carry the same new bullet; a diff of the two sections after this step is otherwise identical (the sync rule holds).
- `make verify` ending `VERIFY OK`, pasted in full as this stage's Evidence.

## Verification

- `make test` (never a bare `pytest` — this repo's one door for tests).
- `make typecheck` — every new noun module's `NounModule` protocol conformance is a static check, not just a runtime one; the cheapest way to catch a missing/mis-typed method before running anything.
- `make verify` for the full gate.
- No manual/browser verification applies — this is a server-side API with no UI in this repository; the console side is a separate repository this one does not run.
