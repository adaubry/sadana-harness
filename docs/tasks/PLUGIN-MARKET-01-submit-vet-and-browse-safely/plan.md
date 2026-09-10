# Plan: A plugin nobody can find is a plugin nobody uses (from intent.md 2026-09-10)

Intent: `docs/tasks/PLUGIN-MARKET-01-submit-vet-and-browse-safely/intent.md`
Spec: `docs/tasks/PLUGIN-MARKET-01-submit-vet-and-browse-safely/spec.md`

## Context

Creators today have no way to let anyone find a plugin they built, and
nobody vetting a plugin can see what it does without running its code on
their own machine. This work item builds a submit → review → browse
workflow: a creator submits a repo+tag, sadana fetches and verifies it
exactly like installing already does, and captures the plugin's declared
shape (name, entries, node graph) *without ever executing a line of the
plugin's own code* — then one or more trusted reviewers decide whether
ordinary browsers get to see it. Tagging a new release on an already-known
repository triggers this automatically via a real webhook listener. This
is not a web application; it produces the data and functions a later,
separate web app will read.

spec.md found a real, load-bearing safety gap while designing this:
`plugin_manifest.validate()` (already closed, D2) executes a plugin's
`init.py` via `importlib` to confirm a `body` reference resolves — fine
for `discover_plugins()`'s only caller (already-installed, trusted
plugins), unsafe for anything unreviewed. This plan's first real step
fixes that, before any marketplace-specific code is written on top of it.

## Files that change

**New:**
- `src/sadana/marketplace.py` — the `marketplace_releases` table, outcome
  dataclasses, `submit()`/`decide()`/`latest_approved()`/
  `pending_releases()`/`approved_plugin_names()`.
- `src/sadana/marketplace_webhook.py` — payload parsing + HTTP server,
  mirrors `channel_webhook.py`'s shape.
- `src/sadana/subcommands/marketplace.py` — `submit`/`list`/`show`/
  `approve`/`reject`/`serve-webhook`.
- `tests/unit/test_marketplace.py`
- `tests/unit/test_marketplace_webhook.py`
- `tests/unit/test_subcommands_marketplace.py`
- `scripts/prove_plugin_marketplace.py` — standalone, real-network proof
  (mirrors `scripts/prove_plugin_install.py`'s shape); not run by
  `make test`.

**Modified — the three closed-file extensions spec.md's Design names:**
- `src/sadana/plugins.py` — add `manifest_to_dict(manifest) -> dict`
  (pure). `tests/unit/test_plugins.py` gains its coverage. Self-check
  later also added `describe_manifest_outcome(outcome) -> str` here
  (moved from a first draft in `marketplace.py`, per the altitude
  review's finding that prose for a type family belongs beside the type),
  with its own coverage in the same test file.
- `src/sadana/plugin_manifest.py` — `validate()` gains
  `check_bodies: bool = True`; the `_check_body()` loop is wrapped in
  `if check_bodies:`. `tests/unit/test_plugin_manifest.py` gains the
  safety-proving test (see Risks).
- `src/sadana/plugin_install.py` — extract `fetch_verified_tag(repo_url,
  tag, dest) -> FetchedTag | TagMismatch | FetchFailed` from `install()`'s
  own resolve→clone→verify sequence; `install()` calls it instead of
  inlining the same steps. `tests/unit/test_plugin_install.py` keeps
  passing unchanged (same public behavior) plus new direct tests for the
  extracted function. Self-check also added
  `describe_fetch_failure(outcome) -> str` here (a reuse finding: both
  `subcommands/plugin.py` and `subcommands/marketplace.py` were
  independently re-deriving the same `TagMismatch`/`FetchFailed` message
  text), with its own coverage.

**Modified — generalizing the webhook-hosting daemon (also closed
files):**
- `src/sadana/gateway.py` — add `header_value(headers, name) -> str`,
  moved from `channel_webhook.py`'s private `_header()`.
  `tests/unit/test_gateway.py` gains its direct coverage.
- `src/sadana/channel_webhook.py` — import `gateway.header_value` instead
  of keeping its own copy. `tests/unit/test_channel_webhook.py` unchanged
  in behavior, still green.
- `src/sadana/subcommands/plugin.py` — not anticipated when this plan was
  first drafted; updated during self-check to call the new
  `plugin_install.describe_fetch_failure()` instead of its own inline
  `TagMismatch`/`FetchFailed` message formatting (the reuse finding
  above). No behavior change; its existing tests pass unchanged.
- `src/sadana/gateway_daemon.py` — `run()`'s signature becomes
  `run(*, make_server: Callable[[], ThreadingHTTPServer], lock_filename:
  str = "gateway.lock") -> int`; `host`/`port`/`secret`/`on_message` and
  the "secret unset" refusal move to the caller.
- `src/sadana/subcommands/gateway.py` — `cmd_gateway_run` builds its own
  server via a closure, does its own secret-unset check, then calls
  `gateway_daemon.run(make_server=..., lock_filename="gateway.lock")`.
- `tests/unit/test_gateway_daemon.py` — its "refuses when secret unset"
  test moves to `test_subcommands_gateway.py` (that check now lives in
  `cmd_gateway_run`); its "refuses when lock already held" test is
  rewritten against the new generic signature with a fake `make_server`.
- `tests/unit/test_subcommands_gateway.py` — gains the migrated
  secret-unset test, adapted to call `cmd_gateway_run` with
  `gateway_daemon.run` monkeypatched to fail the test if reached.

**Wiring:**
- `src/sadana/cli.py` — import + register `build_marketplace_parser`.

**Already modified at design stage (not part of this plan's own diff, but
present in the working tree before this plan was drafted):**
- `CLAUDE.md` — the new rule about a check that can execute code as a side
  effect needing a no-execution mode.

## Reference corpus, reused directly

`hermes_cli/webhook.py`'s incoming-webhook-gateway shape was checked
(spec.md's Design section); its HMAC-over-body signing was declined in
favor of reusing `channel_webhook.py`'s own already-proven bare
shared-secret-header scheme — one way to authenticate a webhook in this
project, not two. Hermes has no real plugin-release-webhook/publish
pipeline anywhere to adopt (`plugin_index.py` is a read-only, centrally
curated index, not something a tag push writes to).

From this project's own code: `plugin_install.py`'s
clone-into-temp-then-inspect shape, `plugin_manifest.py`'s existing
`Manifest`/`Entry`/`Node` types (already the round-trippable data
`plugin_blueprint.md` §5.2 designed for), and `channel_webhook.py`'s
`ThreadingHTTPServer`/JSON-reply/`hmac.compare_digest` shape are all
reused as-is or by direct extraction, never re-derived.

## Order of work

1. **`plugins.py`: `manifest_to_dict()`.** Pure, additive, zero existing
   callers touched. Test immediately.
2. **`plugin_manifest.py`: `check_bodies` parameter.** The safety-critical
   step, done early so everything built afterward can rely on it. New
   test: a fixture plugin whose `init.py` has an observable top-level
   side effect (e.g. writes a sentinel file on import) is validated with
   `check_bodies=False` and the sentinel file must **not** appear —
   proving zero execution, not just asserting the return value shape.
   Existing `test_plugin_manifest.py` cases must keep passing with no
   changes (default `check_bodies=True` preserves current behavior).
3. **`plugin_install.py`: extract `fetch_verified_tag()`.** Refactor,
   behavior-preserving. Full existing `test_plugin_install.py` suite is
   the regression check — it must pass with zero changes to its own
   assertions.
4. **`gateway.py`/`channel_webhook.py`: `header_value()` extraction.**
   Refactor, behavior-preserving. Full existing `test_channel_webhook.py`
   suite must pass unchanged.
5. **`gateway_daemon.py`/`subcommands/gateway.py`: generalize `run()`.**
   The riskiest step in this whole plan (see Risks) — done after steps
   1-4 already work, so if this needs rework it doesn't put anything else
   in question. Migrate the two affected tests as described above.
6. **`marketplace.py`: schema + `submit()`/`decide()`/query functions.**
   The genuinely new, load-bearing business logic — depends on steps 2
   and 3 already existing. Test every outcome variant named in spec.md's
   Acceptance criteria.
7. **`marketplace_webhook.py`: payload parsing + server.** Mirrors
   `channel_webhook.py`; test `parse_webhook_request()` directly, no
   socket involved (same posture as `channel_webhook.py`'s own tests).
8. **`subcommands/marketplace.py` + wire into `cli.py`.** Thin CLI layer
   once `marketplace.py`'s functions are proven. Tests mirror
   `test_subcommands_plugin.py`'s shape.
9. **`scripts/prove_plugin_marketplace.py`.** One real submission against
   a genuine public repository, printed output — run by hand, output
   consumed at Deploy, not part of this stage's proof.
10. **Self-check**: `/ponytail-review` and `/simplify` against the full
    diff, then `make verify`.

## Risks

**What could this break?** Everything in steps 1-5 touches already-closed
files, so the honest risk surface is real, not hypothetical:
`plugin_manifest.validate()`'s one caller (`discover_plugins()`, used by
every dispatch path that builds a live tool surface) must see byte-
identical behavior at its default `check_bodies=True`; `plugin_install.py`
`install()`'s existing 12+ passing tests are the direct regression check
for the extraction in step 3; `channel_webhook.py`'s existing webhook
tests and `gateway_daemon.py`'s own tests are the regression check for
steps 4-5. None of GATEWAY-DAEMON-01's or CLI-SHELL-05's own *artifacts*
(`plan.md`/`spec.md`/`review.md`) are touched — only the code, the normal
way later work extends earlier work.

**Riskiest step, and why**: step 5, generalizing `gateway_daemon.run()`.
It is the only step changing a public function's *signature* (not just
its internals) with one real caller to migrate, and it moves a
security-relevant check (the secret-unset refusal) to a new location
(`cmd_gateway_run`) that today has zero direct unit tests of its own
(`test_subcommands_gateway.py`'s own docstring explains why: `run()`
binds a real socket and blocks). Mitigation: add exactly one new test to
`test_subcommands_gateway.py` that monkeypatches `gateway_daemon.run` to
fail the test if it's ever reached, proving the secret-unset guard fires
*before* any daemon logic — the same shape of proof
`test_gateway_daemon.py`'s current test gives, relocated rather than
lost. Ordered after steps 1-4 so if this needs rework, the marketplace's
own new code (steps 6-8) hasn't been built on a shaky foundation yet.

**Second-riskiest, and why it's still lower risk than step 5**: step 2,
the `check_bodies` parameter. Structurally simple (one boolean gating one
existing loop), but it is the one piece a Deploy-stage reviewer must be
able to verify independently, not just trust — the new test's sentinel-
file proof is designed specifically to be that independent evidence, not
just a return-value assertion a reviewer would have to take on faith.

**Rejected-alternatives drift check** (re-read against spec.md's
`## Rejected alternatives`): this plan does not reuse
`plugin_manifest.validate()` unchanged for the marketplace path; does not
build a second, duplicate daemon-lifecycle module (generalizes the
existing one instead); does not host the marketplace webhook on
`channel_webhook.py`'s existing `/webhook` endpoint; does not parse a
specific git host's native webhook format; does not persist a
submission's raw fetched files; does not add a second table tracking
name-ownership (derived by query instead); does not record a reviewer's
identity; and does not adopt HMAC-over-body webhook signing. None of these
appear in the design above.

## Proof

- `bash scripts/run_tests.sh tests/unit/test_plugins.py
  tests/unit/test_plugin_manifest.py tests/unit/test_plugin_install.py
  tests/unit/test_gateway.py tests/unit/test_channel_webhook.py
  tests/unit/test_gateway_daemon.py tests/unit/test_subcommands_gateway.py
  tests/unit/test_marketplace.py tests/unit/test_marketplace_webhook.py
  tests/unit/test_subcommands_marketplace.py` green — every existing
  suite touched by a refactor, plus every new one, run together.
- The `check_bodies=False` sentinel-file test (step 2) passes — the
  concrete proof nothing is ever executed.
- `marketplace.py`'s tests cover, by name, every `SubmitOutcome`/
  `DecideOutcome` variant spec.md's Acceptance criteria lists: `Pending`,
  `NameOwnedByAnotherRepo`, `AlreadySubmitted`, `TagMismatch`,
  `FetchFailed`, `InvalidManifest` (auto-rejected, never queued for
  review), and `Decided`/`UnknownRelease`/`ReasonRequired`/
  `AlreadyDecided`.
- A test proves an approved release stays the one `latest_approved()`
  returns while a newer submission for the same name sits `pending`.
- `sadana gateway run` and `sadana marketplace serve-webhook` use
  different lock filenames — proven by a test that holds one lock and
  confirms the other daemon's `run()` doesn't refuse because of it.
- `make verify` ends `VERIFY OK` (pasted in the conversation before
  reporting done).
- `scripts/prove_plugin_marketplace.py`'s manual run against a real
  public repository is produced in step 9 and consumed by the Deploy
  stage's `review.md`, not proof for this stage.
