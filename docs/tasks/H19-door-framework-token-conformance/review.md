# Review: The door — grammar, tokens, and the framework every later noun plugs into (from plan.md 2026-09-14)

Reviewed: HEAD..(staged, not yet committed) — 41 files, +5204/-6
Reviewer context: fresh session, no prior context on this work item — the cold review the project's deploy-skill asks for.
Second opinion: none — the build stage ran its own self-check (/ponytail-review + /simplify) earlier in a different session; not repeated here by design, independently re-derived findings instead.

## Evidence

```
docs/tasks/H19-door-framework-token-conformance/intent.md: note — reads like design, not intent — module names and code belong in spec.md
docs/tasks/H19-door-framework-token-conformance: all present artifacts valid
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
ruff-format...............................................................Passed
shellcheck................................................................Passed
Detect secrets............................................................Passed
docs/reference/ citations resolve to tracked files........................Passed
LINT OK
Success: no issues found in 70 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................
  two conversations, concurrently: 272 ms for two 200 ms turns
.  one conversation, twice: 464 ms for two 200 ms turns
............................................... [  5%]
........................................................................ [ 11%]
........................................................................ [ 16%]
........................................................................ [ 22%]
........................................................................ [ 28%]
........................................................................ [ 33%]
........................................................................ [ 39%]
........................................................................ [ 44%]
........................................................................ [ 50%]
........................................................................ [ 56%]
........................................................................ [ 61%]
........................................................................ [ 67%]
........................................................................ [ 73%]
........................................................................ [ 78%]
........................................................................ [ 84%]
........................................................................ [ 89%]
........................................................................ [ 95%]
.........................................................                [100%]
1281 passed in 91.10s (0:01:31)
TESTS OK
VERIFY OK
```

(Ran verbatim in this review session, `make verify`, from a clean staged working tree. Ends `VERIFY OK`.)

## Findings

**Important**

1. **`router.py:227-274` checks scope before state/capability on an action, reversing spec.md requirement 25's mandated order.** Requirement 25 (and `docs/console/wire.md` §1 "Actions") is explicit: an action request is state-gated (`409`), then capability-gated (`501`), then scope-gated (`403`) — "in that order." The actual code in `_handle` computes `scope_required = _scope_for(...)` and calls `require_scope(...)` at lines 227-230, immediately after the undeclared-verb check and *before* the idempotency-replay check and the state/capability gate block at lines 256-274 (which is introduced by a comment claiming "State, then capability: requirement 25's own order" — true only for those two, silently omitting that scope was already checked earlier). Concrete consequence: a `POST /v1/{plural}/{id}/actions/{name}` request against a resource in the wrong state, sent by a caller who also lacks the required scope, returns `403 FORBIDDEN` (revealing the scope name) instead of the spec-mandated `409 CONFLICT`; likewise a call against an action gated behind an undeclared capability, from a caller lacking scope, returns `403` instead of `501`. No test in the diff exercises an action request with a failing scope *and* a failing state/capability simultaneously — `tests/contract/test_console_grammar.py::test_missing_scope_is_403_naming_it` tests scope failure only on a plain `create` (no state/capability gate involved at all), and `test_create_get_update_archive_remove`'s state-conflict case (`archive` from `draft`) uses a token that already carries `widgets:archive` scope, so the two gates are never made to race for the same request. This is exactly the kind of regression the build stage's own note about moving gate logic from `_dispatch` into `_handle` should have been checked against, and it was not caught.

2. **`idempotency.store()` is not actually written inside the same transaction as the request's own effect, contradicting requirement 18's explicit crash-safety claim.** Requirement 18: "`store(...)` writes one row, inside the same `write_txn` as the request's own effect, so a crash between the two cannot happen." In `router.py:276-315`, `_dispatch` (and, through it, the noun's own `create`/`update`/`remove`/`act`) runs and commits its own `write_txn` (per requirement 44) *before* the response is built; only afterward, once `response` already exists, does `_handle` call `idempotency.store(ctx.conns.writer, ...)` (`router.py:304-314`). `idempotency.store()` itself (`src/sadana/door/idempotency.py:93-115`) never opens a `write_txn` — it does a bare `conn.execute(...)` on an autocommit connection (`isolation_level=None`, confirmed at `conversation_store.py:193`), so it commits as its own separate, later transaction. A process crash between the noun's effect committing and this second `INSERT` committing leaves the resource created with no idempotency row recorded — the exact scenario requirement 18 says "cannot happen": a retried `POST` with the same `Idempotency-Key` would find no record, be reprocessed as if fresh, and create a second resource. `idempotency.py:104-106`'s own docstring ("Called by `router.handle` inside the same `write_txn` as the request's own effect, per spec.md requirement 18") describes behavior the code does not implement. No test in the diff simulates a crash in this window, so nothing catches the gap; it is a design property, not just a missing test.

3. **`CLAUDE.md` was edited but is not listed in `plan.md`'s `## Files that change`, and the addition is exactly the rule spec.md says needs approval before being written.** `git diff --cached -- CLAUDE.md` adds one line to `## Please do`: "A ledger `Change` row never carries a resource's `search_doc`; a door renders one at request time from the owning noun's own `search_doc(row)`, and a ledger `created` row renders as console event kind `"changed"` — never a fourth, wire-visible kind." — verbatim the text spec.md's own closing section proposes under the heading "A rule this spec would bind beyond this work item, for approval before it is written anywhere" (spec.md:760-769), which ends "If you approve it, it belongs in `CLAUDE.md`'s `## Please do` list as that one line." `plan.md`'s `## Files that change` section (the file this review was told to check file-by-file against the diff) never mentions `CLAUDE.md` at all — not as a file that changes, and not flagged for after-approval addition. Per this project's own deploy-review instructions, a file touched that the plan did not name is an Important finding on its own; here it is compounded by the fact that the specific content is one spec.md explicitly gated on a separate approval this review has no evidence of (no artifact records it, and `CLAUDE.md`'s own rule forbids adding a path to `## Files that change` after approval "without saying so," which is moot here since the path was never in that section to begin with).

4. **The `## Proof` item "a curl transcript against a running `sadana door serve` ... `GET /v1/harness`, `GET /v1/changes?since=0`, `GET /v1/inventory`, one `401`, one `403`, one `404`" (plan.md step 14, also spec.md's last Acceptance-criteria checkbox) has no evidence anywhere in the repository or artifact chain.** `docs/tasks/H19-door-framework-token-conformance/` contains only `intent.md`, `spec.md`, `plan.md` — no pasted transcript exists, and this review received none. I generated one myself in this session (`sadana door serve` on a scratch state dir, `sadana door token`, then curl against all six cases) to check whether the underlying capability actually works, and it does — `GET /v1/harness`, `GET /v1/changes?since=0`, `GET /v1/inventory` all returned `200` with well-formed bodies; a request with no `Authorization` header returned `401 UNAUTHENTICATED`; a request with a mismatched `X-Sadana-Harness` header returned `403 FORBIDDEN`; `GET /v1/gadgets` returned `404 NOT_FOUND`. So the feature itself is sound end to end — but the Proof item as the project's own process defines it (produced during build/test and pasted as Deploy-stage Evidence, per `CLAUDE.md`'s "Prove a block's first real external round trip with a standalone script outside `make test`; paste its output as Deploy-stage Evidence") was never actually discharged before this review manufactured it after the fact.

**Nits**

1. `src/sadana/door/request.py:43-44` hardcodes `Retry-After: "1"` on every `429` `problem_response`. Nothing in this work item ever produces a `RATE_LIMITED`/`QUOTA_EXCEEDED` response (no rate limiter is built here), so this is untested in practice; a fixed one-second value may be too aggressive once a real limiter lands and should be revisited then, not now.
2. `src/sadana/door/router.py:202-206`'s `GET /v1/operations/{id}` path performs no ownership/account check at all — any principal holding a valid token for this harness (any `sub`, any scope) can poll any operation id and see its `resource` (`noun`, `id`) and `error` detail, unlike every other per-account resource in this work item (requirement 27's 404-not-403 visibility rule is never applied here because operations aren't reached through a noun's own `get`). Operation ids are UUIDv7-derived (not sequential/guessable in practice), so this is low severity, but it is worth a line in `docs/console/nouns.md`'s "operations" section or a follow-up note rather than silence, since it is the one resource in this design with no visibility rule stated for it at all.

**Raised, not findings**

- The three real duplication opportunities the build stage's own `/simplify` pass found and declined to fix (`src/sadana/subcommands/editor.py`, `src/sadana/editor_server.py`, both outside `plan.md`'s file list, one a closed work item) were correctly left alone — `CLAUDE.md`'s "Never patch a closed work item" rule and this project's own file-scope discipline both point the same way. Declining was the right call; it does not block this deploy.
- `door/serve.py:29-47`'s `_from_this_machine`/`_host_without_port` genuinely fixes an IPv6-bracket parsing bug present in `editor_server.py:379-403`'s own `_from_this_machine`. Traced both: `editor_server.py`'s `host.split(":")[0].strip("[]")` on a `Host: [::1]:7001` header splits on every colon inside the brackets, yielding `"["` before stripping — `.strip("[]")` then reduces that to `""`, which is not in `("localhost", "127.0.0.1", "::1")`, so a legitimate IPv6-loopback request is incorrectly refused with `403` by the older code. `door/serve.py`'s version special-cases a leading `"["` and partitions on `"]"` first, correctly yielding `"::1"`. Confirmed this is pre-existing in `editor_server.py` (unrelated to this diff, not introduced by it) and confirmed `door/serve.py`'s fix is correct; `tests/unit/test_door_serve.py::test_localhost_and_ipv6_loopback_are_also_this_machine` exercises exactly the bracketed case. Correctly left unfixed in `editor_server.py` itself (out of scope, a closed work item), per the same reasoning as the duplication point above.
- Spot-checked two of spec.md's hermes citations against `../hermes-agent` directly: `gateway/platforms/api_server_run_idempotency.py:154` is exactly `if hmac.compare_digest(row[0], fingerprint)` — accurate. `gateway/relay/auth.py:83-106` spans `make_token`'s definition (83) through `make_upgrade_token`'s body (106) — the hand-rolled `base64url(payload:exp:sig)` HMAC scheme spec.md describes — accurate. Both check out; design principle 1 (learn from the reference first) is satisfied on this sample.
- The `run_bounded` promotion race (spec.md's own named highest risk) traces correctly: the completion callback is registered before waiting, `promoted_op` is read and written only under one call-local `threading.Lock`, and the router only promotes after re-checking `future.done()` under that same lock — so a call that finishes in the gap between the timeout firing and the promotion decision is provably resolved exactly once, never zero or two operations rows. `tests/unit/test_door_operations.py::test_a_call_finishing_in_the_promotion_gap_still_resolves_once` targets this interleaving directly with a real `time.sleep` past a real short timeout, not a mocked clock, and passes. This part of the build stage's self-review changes held up.
- spec.md's two Open Questions (JWKS-unreachable-on-cold-cache; whether `leaves_the_box = tuple(ctx.nouns)` matches the console's own A04 generator) are still accurately described as open and externally-owned — nothing in this repository can resolve either, and both are cheap to change later (one `auth.py` refusal branch; one `harness.py` function body) if the console side answers differently. Neither rises to an Important finding blocking this deploy; both are correctly left as documented open questions.
- Every `## Proof` item in plan.md besides the curl transcript (Important finding 4, above) is discharged by an existing, passing test or the `make verify` run itself: `make typecheck` green with no `[tool.mypy]` override added (pyproject.toml diff confirms only the dependency line changed); each pure module's own unit test file exists and passes; `test_door_idempotency.py` covers replay/mismatch/pass-through/sweep; `test_door_operations.py` covers the fast-path-zero-rows assertion, the promotion race, both `ledger.record_change` prefix guards, and `resume_on_start`; `test_door_auth.py` covers every refusal in requirement 29's order plus the JWKS refresh-throttle and key-retention tests; `test_door_nouns_harness.py` covers `/v1/harness`, `/v1/changes`' fold and `search_doc` presence/absence, `/v1/inventory`, and the `upgrade`-only action set; `test_door_serve.py` and the new `test_cli.py` door cases exist and pass; `test_check_reference_citations.py` has a new `docs/console/` case; `tests/contract/test_console_grammar.py` runs (its own `-v` output was not separately pasted, but its pass/fail is folded into the `make verify` run above, which is `VERIFY OK`).
- Every file `plan.md`'s `## Files that change` names is present in the staged diff, and every file in the staged diff is named by the plan except `CLAUDE.md` (Important finding 3, above).

## Decision

Approved by Adam Aubry, 2026-09-14, as-is. All four Important findings
(action-gate ordering, idempotency crash-safety, `CLAUDE.md` missing from
`plan.md`'s `## Files that change`, the curl-transcript Proof item
undischarged before this review) are accepted without requiring further
changes before merge.
