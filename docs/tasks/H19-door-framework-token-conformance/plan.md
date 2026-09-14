# Plan: The door — grammar, tokens, and the framework every later noun plugs into (from intent.md 2026-09-14)

Author: Adam Aubry (maintainer). Status: approved.

## Files that change

**New, pure (no I/O), each with its own unit test:**
- `src/sadana/door/__init__.py` (new) — empty, matching `subcommands/__init__.py`'s own convention (0 lines beyond the package marker).
- `src/sadana/door/request.py` (new) + `tests/unit/test_door_request.py` (new)
- `src/sadana/door/problems.py` (new) + `tests/unit/test_door_problems.py` (new)
- `src/sadana/door/grammar.py` (new) + `tests/unit/test_door_grammar.py` (new)
- `src/sadana/door/filter.py` (new) + `tests/unit/test_door_filter.py` (new)
- `src/sadana/door/capabilities.py` (new) + `tests/unit/test_door_capabilities.py` (new)
- `src/sadana/door/nouns/__init__.py` (new) + `tests/unit/test_door_nouns.py` (new)

**New, I/O, each with its own unit test:**
- `src/sadana/door/idempotency.py` (new) + `tests/unit/test_door_idempotency.py` (new)
- `src/sadana/door/operations.py` (new) + `tests/unit/test_door_operations.py` (new) — the highest-risk file; see § Risks.
- `src/sadana/door/auth.py` (new) + `tests/unit/test_door_auth.py` (new)
- `src/sadana/door/nouns/harness.py` (new) + `tests/unit/test_door_nouns_harness.py` (new)
- `src/sadana/door/router.py` (new) — tested only via the contract suite below, deliberately: routing behavior is what the conformance test exists to prove, and a second, parallel unit suite for it would duplicate that proof against a fixture noun for no gain.
- `src/sadana/door/serve.py` (new) + `tests/unit/test_door_serve.py` (new, mirrors `tests/unit/test_editor_server.py`'s shape)

**New, CLI wiring:**
- `src/sadana/subcommands/door.py` (new) + a `TestDoorSubcommand` case added to `tests/unit/test_cli.py` (existing file, edited)
- `src/sadana/cli.py` (edit) — one import line, one `build_door_parser(subparsers)` call, matching every existing subcommand's registration.

**Edited, existing modules (one new line each, in an existing fixed list — never a new seam):**
- `src/sadana/stores.py` (edit) — `ensure_schemas` gains two calls: `door_idempotency.ensure_schema(conn)`, `door_operations.ensure_schema(conn)`, in the same fixed-list style as its existing six.
- `pyproject.toml` (edit) — `dependencies = ["jsonschema", "PyJWT[crypto]>=2.9"]`; a `[tool.mypy]` override added only if `make typecheck` is red without one.
- `scripts/check_reference_citations.py` (edit) — `CITATION_RE` extended to also match `.md`/`.json` files under the new `docs/console/` root; `tests/unit/test_check_reference_citations.py` (edited) gets a case for the new root.

**New docs (written once the shapes above are stable — see § Order of work):**
- `docs/console/grammar.json` (new)
- `docs/console/wire.md` (new)
- `docs/console/capabilities.md` (new)
- `docs/console/nouns.md` (new)

**New conformance test (the work item's own acceptance proof):**
- `tests/contract/fixture_noun.py` (new) — the `widgets` noun the test registers itself, per spec.md requirement 52.
- `tests/contract/test_console_grammar.py` (new), marked `contract`.

## Order of work

1. **`pyproject.toml`** — add `PyJWT[crypto]>=2.9`. Run `make typecheck` once, bare, to see whether a `[tool.mypy]` override is actually needed (spec.md requirement 47 says only add one if red). Lands something installable before anything imports it.

2. **`door/request.py`, `door/problems.py`** — the two leaf data-shape modules everything else returns. `problems.py`'s thirteen-code closed set raises at import per spec.md requirement 12; test that directly (a fourteenth code raises). Nothing here has a dependency inside `door/` yet, so this is the safest possible first real code.

3. **`door/grammar.py`, `door/filter.py`** — list params, cursors, timestamps, the filter-expression parser. Pure, but the largest pure surface (every operator, precedence, escapes, malformed input) — spec.md's own acceptance criteria enumerate this test list almost verbatim; write the test cases from that list directly rather than inventing new ones.

4. **`door/capabilities.py`** — the closed 16-name `ALL` tuple and the append-only `DECLARED` tuple set to `("grammar.v1", "changes", "inventory")` for this item. Test: a name outside `ALL` raises at import; `DECLARED` is a subset of `ALL`.

5. **`door/idempotency.py`** — `door_idempotency` table, `replay`/`store`, the `hmac.compare_digest` fingerprint check, the hourly sweep. Wire its `ensure_schema` into `stores.ensure_schemas` here (one line, existing fixed-list pattern — `stores.py`'s own docstring already names this as the correct extension point). Test: replay same-key/same-body, same-key/different-body → mismatch, no-key pass-through, 24h-old row swept.

6. **`door/operations.py`** — the riskiest file; see § Risks for the race this has to get right and how. `run_bounded`'s lazy-promotion split, the package-owned `ThreadPoolExecutor` (lazily constructed — see § Risks), `resume_on_start`. Wire its `ensure_schema` into `stores.ensure_schemas` (second new line). This step also directly exercises `ledger.record_change(noun="operations", ...)` and, later in this same step's test, `noun="harness"` — the two reservations H16 registered with empty `sources` tuples and nothing has ever written to. Test this directly, at the `ledger` boundary, before anything in `door/` depends on it working: a wrong id-prefix must raise (proving `ledger.py`'s own guard actually fires for these two nouns, not just for the six it's been exercised against since H16), a fast `fn` writes zero `operations` rows, a slow one (a fixture function with a real short `time.sleep`, timeout set far below it — no reliance on the production `2.0` default, so this test runs in well under a second and is not a quiet-machine timing assumption) promotes exactly once and completes exactly once even under the race described in § Risks.

7. **`door/auth.py`** — `Principal`, `JwksSource` (file-backed for tests, matching this project's no-network-in-tests rule), `verify`, `require_scope`, `account_key_for`. Test every refusal in spec.md requirement 29's order, against a locally generated ES256 test key and a `SADANA_DOOR_JWKS_FILE`.

8. **`door/nouns/__init__.py`** — `NounSpec`, `ActionSpec`, `SearchDoc`, the noun protocol as a `Protocol` class (structural, not a registry — spec.md already rejected a transport registry on the same CLAUDE.md ground; a noun protocol is not that, it is the interface every noun module already has to implement per spec.md's own requirement, but it is worth naming explicitly here that this is a `Protocol`, never an ABC with a registration step).

9. **`door/nouns/harness.py`** — `GET /v1/harness`, `/v1/changes` (the `created→changed` fold and `search_doc` derivation from spec.md requirements 32-33), `/v1/inventory`, and the `upgrade`-only action declaration (requirement 40 — `deregister`/`purge-account` are simply absent from `NounSpec.actions`). Test directly, calling the noun's functions with a fake `ctx`/`principal`, before the router exists to call them for us — this is the second direct exercise of the H16 ledger reservations, this time through the actual noun code path rather than a raw `ledger.record_change` call.

10. **`door/router.py`** + **`tests/contract/fixture_noun.py`** — the pure `handle()` pipeline (verify → route → scope → idempotency-replay → state/capability-gate → `run_bounded` dispatch → If-Match → ETag → idempotency-store), and a minimal in-memory `widgets` noun for the conformance test to exercise the framework against something that isn't `harness`. This is where every module built so far gets wired together for the first time — land it, then prove it with a first, narrow slice of the contract test (routing + one noun's CRUD) before writing the full test in step 12.

11. **`docs/console/grammar.json`, `wire.md`, `capabilities.md`, `nouns.md`** — written now, because every shape they describe (`ListParams`, `Problem`, `Operation`, `ResourceChangedEvent`, `SearchDoc`, `StandardFields`) is stable by this point. `scripts/check_reference_citations.py`'s `CITATION_RE` is extended in this same step, since it's the citation target these four files exist to be cited from.

12. **`tests/contract/test_console_grammar.py`** — the full conformance test: every behavior in spec.md's Acceptance criteria, jsonschema-validated against `docs/console/grammar.json`, against both `harness` and the `widgets` fixture noun. This is where the acceptance criteria checklist is worked item by item.

13. **`door/serve.py`, `subcommands/door.py`, `cli.py`** — the loopback listener (reusing `editor_server._from_this_machine`'s shape and `gateway_daemon.run`'s lifecycle, per spec.md requirements 48-49), `sadana door serve` and `sadana door token`, wired into `build_parser()`. Last, because everything it needs to be correct already is by this point, and because it is the thinnest layer — bytes in, `handle()`, bytes out — the same shape `editor_server.make_server` already proves works.

14. **Evidence** — the curl transcript (spec.md's own acceptance list: `GET /v1/harness`, `GET /v1/changes?since=0`, `GET /v1/inventory`, one `401`, one `403`, one `404`, all against a running `sadana door serve` authenticated with `sadana door token`), then `make verify`.

## Risks

**What could this change break, that already works?** Three named things, not the code being added:

- `stores.ensure_schemas` gains two lines in its fixed call list. Every existing `sadana` subcommand calls this on store open (it is `conversation_store.open_store`'s own dependency), so a bug in either new `ensure_schema` — not idempotent, or raising on a store that predates it — would break every CLI command, not just `door`. Mitigated by writing `door/idempotency.py` and `door/operations.py`'s own `ensure_schema` functions in the exact `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info`-guard shape every existing one already uses (spec.md requirement 16 already commits to this), and by running the full existing test suite (not just the new door tests) after this specific edit lands, before moving on.
- A module-level `ThreadPoolExecutor` in `door/operations.py`, if constructed eagerly at import time, would spin up worker threads the moment anything imports `door.operations` — including, transitively, `door.router` and eventually every test that imports it, inside the one shared pytest process `make test` runs in. That is a real risk to `make test`'s own hygiene (leaked threads across unrelated test modules), not just to `door`'s own tests. Mitigated by making the executor lazily constructed on first `run_bounded` call (a module-level `None` promoted once, behind a lock), never at import.
- `cli.py`'s `build_parser()` gains one call. Checked: no existing subcommand is named `door` (`grep -rn 'add_parser' src/sadana/subcommands/` — confirmed during planning, the ten existing names are `chat conversations editor gateway marketplace memory persona plugin runs setup`), so no collision.

**Which step is riskiest, and why?** Step 6, `door/operations.py`, for two independent reasons:

1. It is the first code to actually write through `ledger.record_change` for the `operations` and `harness` nouns — H16 reserved their prefixes and left their `sources` tuples empty specifically for this work item, and nothing has exercised that branch of `record_change`'s own id-prefix-matches-noun guard since it was written. Step 6's test hits this directly, before the router exists, rather than discovering a mismatch buried under the full pipeline in step 12.
2. `run_bounded`'s lazy-promotion split (spec.md requirements 22-24, rewritten during the design review) has a genuine race the spec's prose does not spell out at the implementation level: the router thread waits up to `timeout` on the pool thread's `Future`; if `Future.result(timeout=...)` raises `TimeoutError`, the router thread promotes (writes the `running` row). But the pool thread may finish *during* that same window, between the timeout firing and the promotion write landing — a naive implementation could write a `running` row for a call that has, by the time the write commits, already finished, and then never transition it. The mitigation: register the pool thread's completion (`Future.add_done_callback`) *before* waiting, not after promoting, so the completion write always happens exactly once regardless of ordering, and have it check-and-skip if the row was never promoted (the common, fast-path case — no row to transition) versus transition-if-promoted (the slow-path case). Step 6's test deliberately targets this exact interleaving with a short, real `time.sleep` past a short `timeout`, not a mocked clock, because the thing under test is genuine thread scheduling, not a pure function of time — the same category of choice H16's own spec.md made when it replaced a wall-clock-race assertion with a deterministic barrier; here a real sleep past a real (short) bound is the correct level of realism, not a race to be designed around.

**Where this plan could drift back toward something spec.md already rejected**, checked against spec.md's own § Rejected alternatives:

- Storing `search_doc` on the ledger row — step 9 (`nouns/harness.py`) computes it by re-fetching the row through `ctx.nouns`, never writes it anywhere. Step 6/`ledger.py` itself is not touched to add a column.
- A `Protocol`/registry seam for "the transport" — step 13 builds `serve.py` as one direct, concrete call (mirroring `editor_server.py`/`subcommands/editor.py` exactly), not an abstraction with `serve.py` as its first of many implementations.
- A web framework dependency — step 13 uses `http.server.ThreadingHTTPServer` only, same as `editor_server.py`; no new dependency beyond the one `PyJWT[crypto]` step 1 adds.
- SQL-backed keyset paging — step 3's `grammar.page()` is a Python scan over already-sorted rows, per spec.md requirement 9, with the same code comment spec.md already commits to about a later maintain item.
- Wrapping every verb call in an `operations` row regardless of duration — this is exactly the ambiguity the design review caught and spec.md's requirements 22-24 were rewritten to close; step 6 is built to the corrected reading (lazy promotion), and step 6's own test asserts the fast path writes nothing, specifically so a future edit can't quietly regress back to the rejected reading without a test noticing.

## Proof

- `make typecheck` — green after step 1, confirming whether the `[tool.mypy]` override is needed before anything else is built on top of the new dependency.
- `tests/unit/test_door_request.py`, `test_door_problems.py`, `test_door_grammar.py`, `test_door_filter.py`, `test_door_capabilities.py` — each new pure module's own behavior, covering spec.md's Acceptance criteria items for filtering/paging/cursors/error codes directly (steps 2-4).
- `tests/unit/test_door_idempotency.py` — replay/mismatch/pass-through/sweep (step 5).
- `tests/unit/test_door_operations.py` — the fast-path-writes-nothing assertion, the promotion race (step 6's own interleaving test), a wrong-prefix `ValueError` from `ledger.record_change` for both `operations` and `harness`, `resume_on_start` marking a stale `running` row `failed`.
- `tests/unit/test_door_auth.py` — every refusal in spec.md requirement 29's order, plus the JWKS cache's refresh-at-most-once-a-minute and stale-key-retention behavior (step 7).
- `tests/unit/test_door_nouns_harness.py` — `/v1/harness`, `/v1/changes` (`created→changed` fold, `search_doc` presence/absence), `/v1/inventory`, the `upgrade`-only action set (step 9).
- `tests/unit/test_door_serve.py` — `make_server`/loopback refusal/dev-key generation, mirroring `tests/unit/test_editor_server.py` (step 13).
- `tests/unit/test_cli.py`'s new `door` case — the subcommand is registered and `--help` works (step 13).
- `tests/unit/test_check_reference_citations.py`'s new case — a `docs/console/*.md` citation now resolves (step 11).
- `tests/contract/test_console_grammar.py` (`-v` output pasted in full) — every item in spec.md's Acceptance criteria checklist, against both `harness` and the `widgets` fixture noun, schema-validated against `docs/console/grammar.json` (steps 10-12).
- A curl transcript against a running `sadana door serve`, authenticated with `sadana door token`: `GET /v1/harness`, `GET /v1/changes?since=0`, `GET /v1/inventory`, one `401`, one `403`, one `404` (step 14).
- `make verify` ending `VERIFY OK` (step 14, final).
