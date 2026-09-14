# Spec: The door — grammar, tokens, and the framework every later noun plugs into

Intent: docs/tasks/H19-door-framework-token-conformance/intent.md

Author: Adam Aubry (maintainer). Status: approved.

## Requirements

Each group traces to `intent.md`. Promise ids (`P2`, `P4`, `P10`) are
`docs/reference/console_fit_plan.md` §4; register ids (`B7` etc.) are the
console's own frozen plan, cited verbatim per `intent.md`'s trace and not
independently verifiable from this repository.

**Paths and routing** (intent § Proposed outcome ¶1; P2)

1. `router.handle(request, *, ctx) -> DoorResponse` answers exactly these
   shapes: `GET /v1/{plural}`, `GET /v1/{plural}/{id}`, `POST /v1/{plural}`,
   `PATCH /v1/{plural}/{id}`, `DELETE /v1/{plural}/{id}`,
   `POST /v1/{plural}/{id}/actions/{name}`, one level of child nesting —
   `/v1/{plural}/{id}/{child_plural}[/{cid}[/{grandchild_plural}[/{gid}]]]` —
   and the fixed paths `GET /v1/operations/{id}`,
   `GET /v1/changes?since=<cursor>&limit=<n>`, `GET /v1/inventory`,
   `GET /v1/harness`, `POST /v1/harness/actions/{name}`.
2. An unknown plural is `404 NOT_FOUND`. A method not defined on a known path
   shape is `404 NOT_FOUND` (not `405`) — the grammar has no method-not-allowed
   case; a path either exists in the six-verb shape or it does not.
3. `handle` never raises. An unexpected exception is caught once, at the top
   of `handle`, logged with its traceback, and returned as `500 INTERNAL`
   with no traceback text in the body.

**Ids** (P1, carried from H16)

4. `ids.parse_id` and `ids.make_id` are reused unchanged. `ids.PREFIXES`
   already contains `hrn` and `op` (`src/sadana/ids.py:38`, `:59`) — no new
   prefix is registered by this work item.

**List params, filtering, paging** (intent § Proposed outcome ¶1; P2)

5. `grammar.parse_list_params(query, *, filterable, orderable) -> ListParams |
   Problem` accepts exactly `filter`, `order_by`, `page_size`, `page_token`,
   and tolerates `count=true`. Any other query key is `400 VALIDATION` naming
   the key.
6. `order_by` is `<field> asc|desc`; default `created_at desc`. A field not in
   the noun's `orderable` tuple is `400 VALIDATION`.
7. `page_size` is an integer `1..200`; default `50`. Out of range is `400
   VALIDATION`.
8. `filter.py` parses: fields `a` or `a.b`; operators `= != < <= > >= :`
   (`:` is string-only "contains"); boolean `AND OR NOT` with precedence
   `NOT > AND > OR`; parentheses; double-quoted strings with `\"` escapes;
   numbers; ISO-8601 dates; `true`/`false`. A field not in the noun's
   `filterable` tuple, or a malformed expression, is `400 VALIDATION`.
9. `page_token` is `base64url(json([<sort value>, <id>]))` of the last row
   under the `order_by` it was minted for. A token presented under a
   different `order_by` is `400 VALIDATION`. Paging is a Python keyset scan
   over already-sorted rows — no SQL keyset query — with a code comment
   marking a SQL keyset as a later maintain item once a store's row count
   makes the scan slow.
10. A list response is `{"data": [...], "next_page_token": <token or null>}`,
    plus `"count": <n>` only when the caller passed `count=true`.

**Standard fields** (P1)

11. Every resource in every `data` array and every single-resource body
    carries `id`, `created_at`, `updated_at` (both ISO-8601 UTC,
    millisecond precision, trailing `Z`), `state`, `tags` (object),
    `harness_id`, `version` (integer), and `name` where the noun has one.
    `grammar.render_ts`/`grammar.parse_ts` are the one place this shape is
    produced and read.

**Problem Details and error codes** (P2; P4)

12. `problems.py` defines exactly these thirteen codes and no others:
    `UNAUTHENTICATED 401`, `NOT_FOUND 404`, `PAYMENT_REQUIRED 402`,
    `FORBIDDEN 403`, `VALIDATION 400`, `CONFLICT 409`,
    `PRECONDITION_FAILED 412`, `RATE_LIMITED 429`, `QUOTA_EXCEEDED 429`,
    `HARNESS_OFFLINE 503`, `HARNESS_CAPABILITY_MISSING 501`,
    `IDEMPOTENCY_MISMATCH 422`, `INTERNAL 500`. A code outside this set
    raises `ValueError` at import (`problems.CODES` is checked once, at
    module load).
13. Every error body is `{"type": "https://sadana.dev/errors/<CODE>",
    "title", "status", "code", "detail"?, "instance"?, "errors"?: [{"field",
    "code"}]}`, `Content-Type: application/problem+json`.

**Headers** (P2; P4)

14. In: `Authorization: Bearer <token>` (required on every request, else
    `401`); `X-Sadana-Harness: hrn_…` (required on every request; must equal
    both the token's `hrn` claim and this box's own id — an absent header is
    simply the empty string, which fails that equality check the same way a
    wrong one does, so this needs no separate "missing" case); `Idempotency-Key`
    (**not** enforced-required — a `POST` the console sends always carries
    one, but a `POST` without one is processed normally and never stored for
    replay, exactly the operator-curl case the brief names); `If-Match:
    "<version>"` (required on `PATCH`/`DELETE`/an action against an existing
    resource — see requirement 25 for what an absent one does).
15. Out: `ETag: "<version>"` on every single-resource response;
    `Retry-After` on every `429` the router emits.

**Idempotency** (P2; register B12)

16. `idempotency.py` owns `door_idempotency(key, sub, method, path,
    request_sha256, status, headers_json, body, created_at)`, primary key
    `(key, sub)`, added via the `PRAGMA table_info` + guarded `ALTER TABLE`
    pattern in `stores.ensure_schemas`.
17. `replay(conn, *, key, sub, method, path, body) -> Replay | None`: same
    key + same body hash for that `sub` returns the stored status and body
    byte-for-byte and performs no write. Same key + a different body hash is
    `422 IDEMPOTENCY_MISMATCH`. No key is a pass-through — the request is
    processed and nothing is stored.
18. `store(conn, *, key, sub, method, path, body, response)` writes one row,
    inside the same `write_txn` as the request's own effect, so a crash
    between the two cannot happen.
19. A row is 24 hours old or older is deleted on the first write of each
    clock hour (a cheap `DELETE ... WHERE created_at < ?` guarded by an
    in-process last-swept-hour marker, not a background thread).
20. `request_sha256` is compared with `hmac.compare_digest`, not `==` — the
    same constant-time discipline `RunIdempotencyStore.reserve` already uses
    for its own fingerprint (`../hermes-agent/gateway/platforms/
    api_server_run_idempotency.py:154`).

**Operations** (P2; register B14)

21. `operations.py` owns `operations(id, state, resource_noun, resource_id,
    error_json, detail_json, created_at, updated_at, version)`.
22. `run_bounded(conns, fn, *, timeout=2.0, resource=None) -> BoundedResult`
    submits `fn` to a package-owned `ThreadPoolExecutor(max_workers=
    config.env_int("SADANA_DOOR_WORKERS", 8))` and waits up to `timeout`.
    **If `fn` finishes within `timeout`, `run_bounded` returns its result
    inline and writes nothing to `operations` at all** — no row, no
    `write_txn` on the door's own tables, no extra ledger row; the only
    writes made are whatever `fn` itself made through its own
    `ctx.conns.reader()`/`write_txn(ctx.conns.writer)` calls, exactly as if
    it had been called directly. **Only when `timeout` elapses without `fn`
    finishing** does `run_bounded` promote the call: one `write_txn` inserts
    a `running` row together with its `ledger.record_change(noun=
    "operations", kind="created", …)` row, and `run_bounded` returns that
    `Operation` immediately. This split is what a synchronous `list`/`get`
    never pays for — see § Design's state inventory.
23. The still-running `fn` keeps executing on its pool thread after
    promotion; when it finishes, a second `write_txn` transitions the row
    (`running → succeeded` or `running → failed`), bumping its version, with
    its own `ledger.record_change` row in the same transaction. `get(conn,
    id)` reads current state. `resume_on_start(conns)` — called once, at
    process start — marks every row still `running` as `failed` with
    `detail_json = {"detail": "the process restarted"}`; H30 refines this
    once upgrade makes a restart plannable rather than a crash.
24. `router.handle` never calls a noun's verb function directly — every
    dispatch goes through `run_bounded` (requirement 22), which is what lets
    the router give up waiting at the two-second mark without killing the
    work. A call that resolves within the bound answers synchronously with
    the verb's own result and continues through the If-Match/ETag/
    idempotency-store stages below. A call that does not is answered as
    `202 {"operation": {...}}` immediately, bypassing If-Match/ETag for that
    response — there is no resource yet to attach an `ETag` to, and a
    `PRECONDITION_FAILED` the verb discovers only after promotion surfaces
    later, in the operation's own `error` field, never as an HTTP-level
    `412` on a response that has already gone out.

**Actions** (P2)

25. An action request is state-gated (`409 CONFLICT`, detail `"<action>
    requires state <from-list>; current state is <state>"`), then
    capability-gated (`501 HARNESS_CAPABILITY_MISSING`, detail naming the
    capability), then scope-gated (`403 FORBIDDEN`, detail `"requires scope
    <noun>:<verb>"`), in that order. A `PATCH`/`DELETE`/action against an
    existing resource with no `If-Match` header at all — as opposed to one
    that mismatches — is `412 PRECONDITION_FAILED`, detail `"If-Match is
    required for this request"`. The closed thirteen-code list has no
    separate "precondition required" code; `412` is the nearer of the
    thirteen to what actually happened, and the detail text is what tells
    the two cases apart for a caller that logs it.
26. A verb (`list`/`get`/`create`/`update`/`remove`/an action name) a noun's
    `NounSpec` does not declare is `501 HARNESS_CAPABILITY_MISSING` with
    detail `"<noun>.<verb> is not available on this harness"` — never `405`.

**Visibility** (P2)

27. An id that does not exist and an id that exists but the principal may not
    read are both `404 NOT_FOUND`. No code path returns `403` for existence;
    `403` is reserved for a request whose id resolution already succeeded but
    whose verb the principal's scope does not cover.

**Token verification** (P4; register B7, B8)

28. `auth.verify(headers, *, box: BoxIdentity) -> Principal | Problem` decodes
    an ES256 JWT via `PyJWT[crypto]`, pinning `algorithms=["ES256"]` on every
    call, with `leeway=30`. Claims: `sub, org, ws, hrn, scope, iat, exp`, and
    `exp - iat <= 300` is checked explicitly (PyJWT does not check claim
    *relationships*, only expiry against now).
29. Refusals, in this order once a token decodes structurally: missing or
    unparseable → `401`; expired → `401`; `hrn` claim ≠ `box.harness_id` →
    `403`; `org` claim ≠ `box.org` (unset `box.org` means "any", until H30
    persists enrollment) → `403`; `X-Sadana-Harness` header ≠ `hrn` claim →
    `403`; the route's required scope not in the token's `scope` array →
    `403` naming the missing scope.
30. Keys come from a `JwksSource` — a URL (`urllib`) or a local file
    (`SADANA_DOOR_JWKS_FILE`). An unknown `kid` triggers at most one refresh
    per rolling minute; a key already fetched is kept until every token it
    could have signed has necessarily expired (`now - fetched_at >
    300 + 30`), so a JWKS outage does not immediately break already-warm
    verification.
31. `account_key_for(principal) -> memory.AccountKey` returns
    `"console:" + principal.sub` (console_fit_plan.md §5(d)). This is the
    only place a console request's account is decided; nothing in a request
    body ever names one.

**Events** (P2; register B3, B10, carried from H16's ledger)

32. `GET /v1/changes` renders `ledger.changes_since` rows as `{"kind":
    "changed"|"deleted", "harness_id", "noun", "id", "state"?, "updated_at",
    "version", "search_doc"?}`. `ledger.Change.kind` is three-valued
    (`created`/`changed`/`deleted`, `ledger.py:53` `KINDS`, field at `:157`);
    the console's event
    grammar is two-valued. A ledger `created` row renders as event kind
    `"changed"` — the console's ingest is idempotent to "this now exists or
    was updated" either way, and `intent.md`'s constraint that this item
    implement the console's contract verbatim binds the *response shape*,
    not the ledger's own three-way bookkeeping, which H16 already fixed.
33. `search_doc` is never read from a stored column — there is none on
    `changes` (`ledger.py:37`). It is computed at render time by calling the
    owning noun's own `search_doc(row)` after re-fetching the current row
    through `ctx.nouns[noun]`; a `"deleted"` event has no row left to fetch
    and always omits `search_doc`.
34. `tenant_id` is never included; the console resolves it from the harness
    that delivered the event (console_fit_plan.md §5, decisions list).

**Capabilities** (P10)

35. `capabilities.ALL` is the closed 16-name tuple from the brief
    (`grammar.v1 changes inventory artifacts.download plugins.install
    plugins.inspect plugins.write schedules.write approvals.wait
    approvals.call streaming runs.live runs.stop settings.write secrets.write
    upgrade`). A name outside `ALL` raises `ValueError` at import.
36. `capabilities.DECLARED` is an append-only tuple, checked at import to be
    a subset of `ALL`. This work item sets it to `("grammar.v1", "changes",
    "inventory")`. `declared()` returns it. A later work item's whole
    contribution to this file is adding its own capability name to
    `DECLARED` — never removing or reordering an existing entry.

**The `harness` noun** (P2, P10)

37. `GET /v1/harness` returns `{"id", "version": sadana.__version__,
    "capabilities": capabilities.declared(), "tether": "disconnected",
    "org", "ledger_head", "leaves_the_box"}`. `"tether"` is a literal
    constant until H30 wires a real value.
38. `"leaves_the_box"` is `tuple(ctx.nouns)` — the plural names currently
    registered in the router's own noun mapping, not a separately maintained
    list. See § Design for why this reading was chosen over the alternative
    (a digest of `SearchDoc` field names).
39. `GET /v1/changes` and `GET /v1/inventory` call `ledger.changes_since` and
    `ledger.inventory` directly, through `ctx.conns.reader()`.
40. `harness`'s `NounSpec.actions` declares only `upgrade`, gated on the
    `"upgrade"` capability (present in `ALL`, absent from `DECLARED` —
    requirement 25's capability-gate path answers `501
    HARNESS_CAPABILITY_MISSING` naming it). `deregister` and `purge-account`
    are **not** declared at all in this work item: `capabilities.ALL`'s
    sixteen names (requirement 35) have no entry for either, and inventing
    one here would violate requirement 35's own closed-list rule. An
    undeclared action name reaches requirement 26's "verb the noun does not
    support" path instead — the same `501 HARNESS_CAPABILITY_MISSING`
    status, via "not a declared verb" rather than "declared but
    capability-gated." H30, which turns these on, is also where their
    capability names are minted, inside the console's own frozen contract —
    not this item's to guess.

**Module layout and binding rules** (intent § Constraints)

41. `src/sadana/door/` is a package; every module that performs real I/O
    (`idempotency.py`, `operations.py`, `auth.py`, `serve.py`) is its own
    file, separate from the pure modules (`request.py`, `problems.py`,
    `grammar.py`, `filter.py`, `capabilities.py`, `router.py`,
    `nouns/__init__.py`).
42. `router.handle` is pure: no socket, no clock except `ctx.clock`, no
    environment read outside `DoorContext` construction.
43. A noun module never imports another noun module. `router.py` is the only
    module that imports every `nouns/*.py`. Cross-noun reads at request time
    (requirement 33) go through `ctx.nouns`, which is data passed in, not an
    import.
44. No noun module opens a database connection itself: reads go through
    `ctx.conns.reader()` (`stores.py:86`, the `Connections` class), writes
    through `write_txn(ctx.conns.writer)` (`write_txn` itself is
    `conversation_store.py:385`; `Connections.writer` is set at
    `stores.py:117`).
45. Every write the door's own tables make (`operations` state transitions)
    is a `ledger.record_change` row in the same transaction. `door_idempotency`
    is deliberately **not** ledgered — it is bookkeeping for HTTP replay, not
    a resource the console lists or reads, and `ledger.NOUNS`
    (`ledger.py:138`, the closed tuple) has no slot reserved for it. See §
    Design.
46. Existing CLI subcommands are unchanged; none of their handlers is
    rewritten to call through `door.router`.

**Dependencies**

47. `pyproject.toml` `dependencies` becomes `["jsonschema", "PyJWT[crypto]>=2.9"]`.
    A `[tool.mypy]` override for `PyJWT` is added only if `make typecheck`
    is red without one (PyJWT ships its own inline types as of 2.x; checked
    empirically in build).

**Transports and CLI** (intent § Constraints, "loopback-only for now")

48. `serve.py` + `subcommands/door.py` add `sadana door serve [--bind
    127.0.0.1] [--port 7001]`, built on `gateway_daemon.run(make_server=...,
    lock_filename="door.lock")` (`gateway_daemon.py:55`) — the same lifecycle
    `subcommands/editor.py:47` already uses — and `sadana door token --sub
    <s> --org <o> --ws <w> --scope <a,b,c> [--ttl 300]`, which mints a
    development ES256 token against a locally generated key
    (`state_dir/door/dev_key.pem`, `0600`, generated on first use) and writes
    `state_dir/door/dev_jwks.json`. `serve` reads that file unless
    `SADANA_DOOR_JWKS_URL` is set.
49. `sadana door serve`'s handler, like `editor.py`'s, refuses to bind
    anywhere non-loopback (`subcommands/editor.py:35`'s `_is_loopback` check,
    reused rather than reimplemented) — belt-and-braces alongside token
    verification, not a replacement for it.

**Docs** (intent § Constraints, "console's own agents will be handed")

50. `docs/console/grammar.json`: JSON Schema draft 2020-12 for `ListParams`,
    `ListResponse`, `Problem` (with the `ErrorCode` enum), `Operation`,
    `ResourceChangedEvent`, `SearchDoc`, `StandardFields`; opens with the
    `$comment` the brief specifies verbatim.
51. `docs/console/wire.md`, `capabilities.md`, `nouns.md` as specified in the
    brief. `scripts/check_reference_citations.py`'s citation regex currently
    matches only `docs/reference/...` paths, so a citation into
    `docs/console/...` is invisible to it today — neither checked nor
    rejected. This work item extends the pattern to cover `docs/console/`
    too, so a citation into the new directory is actually verified, not
    merely tolerated by omission.

**Testing** (P10; intent § Proposed outcome ¶1 "provable without a real
network connection")

52. `tests/contract/test_console_grammar.py`, marked `contract`
    (`pyproject.toml:82`, declared, never yet used). Builds a seeded store,
    mints tokens against a test JWKS file, calls `handle()` directly — no
    socket. Validates every response against `docs/console/grammar.json`.
    Exercises the `harness` noun plus a `tests/contract/fixture_noun.py`
    `widgets` noun the test registers itself, so the framework is proven
    before any real noun exists. Missing schema file is a hard failure
    (`"docs/console/grammar.json is missing"`), never a skip.
53. The plugin-folder fixture reuses `tests/fixtures/plugins/plugin-a`
    (already present) rather than adding a new one.

## Design

**Prior art consulted, and what was taken.**

`src/sadana/editor_server.py` is this repository's own precedent for the
shape this work item generalises: a pure `handle(method, path, body, ...) ->
Response` (`editor_server.py:406`), a thin `make_server` that reads bytes,
calls `handle`, writes bytes back (`:447`), a `_from_this_machine` loopback +
`Origin` check (`:379`) reused verbatim by `door/serve.py`, and a
`gateway_daemon.run(make_server=..., lock_filename=...)` lifecycle
(`gateway_daemon.py:55`) that `subcommands/editor.py:61` already calls and
`subcommands/door.py` calls the same way — not a new lifecycle, the third
caller of an existing one. Nothing here is copied from `editor_server.py`'s
plugin-specific routing; what is taken is the split itself and the two
functions that make loopback-only meaningful (`_from_this_machine`,
`gateway_daemon.run`).

Nothing in `../hermes-agent`'s core is a pure, multi-noun, token-verified door
in this shape, but four of its files are close enough to be worth reading
against, per `docs/reference/hermes_core_blocks_kind.csv`'s CHANNELS and
CLIENT-SURFACE rows:

- `gateway/platforms/api_server_run_idempotency.py` — a durable, scoped
  idempotency store keyed `(scope, key)`, fingerprint compared with
  `hmac.compare_digest`, and a pruning rule that only deletes a row once its
  *stored status is terminal* **and** past retention — its own docstring
  (`:242`) says why: "age alone can never release an in-flight
  reservation." **Adopted:** the composite key and constant-time comparison
  (requirement 20). **Declined, narrowly:** the terminal-aware pruning.
  `door_idempotency` stores only an HTTP replay record (status + body), not a
  live job's state — that lives in `operations`, addressed separately by its
  own id and never deleted by the idempotency sweep. So a `door_idempotency`
  row going stale at 24 hours cannot orphan an in-flight operation; it can
  only make a *very* late retry of the original `POST` re-execute instead of
  replay, which is exactly the behaviour the console's own contract specifies
  ("Keys expire after 24 h") and which this item has no authority to change.
  Flagged in § Concerns as worth re-confirming with the console side, not
  reopened here.
- `hermes_cli/dashboard_auth/token_auth.py` — bearer-token auth that fails
  closed by default and distinguishes "no provider recognised this token"
  (`401`) from "a provider's backing store was unreachable" (`503`,
  `:180`-`:183`), so a transient outage doesn't read as a bad credential.
  **Adopted:** fail-closed as the only acceptable default. **Not fully
  resolved:** the console's contract names `401` for "missing or
  unparseable" and "expired," but is silent on "the JWKS itself could not be
  fetched on a cold cache, unknown `kid`, with no older key to fall back on."
  Recorded as an open question rather than guessed into the closed 13-code
  list.
- `gateway/relay/auth.py` — a hand-rolled HMAC bearer scheme
  (`base64url(payload:exp:sig)`, `:83`-`:106`) mirrored byte-for-byte against
  a TypeScript connector, built because hermes's gateway↔connector pairing is
  a single shared symmetric secret. **Declined, specifically:** sadana's
  setting is asymmetric and multi-tenant by construction — one console
  signs, every box only ever verifies — and a symmetric scheme would mean
  any box that could verify a token could also forge one, which is the
  opposite of what `console_fit_plan.md` §5(c) already ruled out project-wide
  ("the algorithm-confusion family of bug is precisely why nothing here is
  hand-rolled"). This is concrete corroboration of that decision, not a new
  argument for it — nothing about §5(c) is reopened.
- `gateway/platforms/api_server.py` — an ~8,000-line `aiohttp`-based REST
  surface (`POST /v1/runs` → `202` + poll, `Idempotency-Key` header,
  `:5301`) that independently validates two shapes this spec already commits
  to: the `Idempotency-Key` header name and the "start now, `202`, poll
  later" pattern for slow work. **Declined:** adopting a web framework
  dependency the way `api_server.py` does. `editor_server.py` already proves
  a hand-parsed router works at this repository's scale without one, and
  `console_fit_plan.md` §5(c) closes the dependency budget at `jsonschema` +
  `PyJWT[crypto]` for the whole console chain — a framework would be a third,
  unbudgeted addition for a capability `editor_server.py` already
  demonstrates isn't needed.

**Ledger integration, in full.** `ledger.py` already reserves `operations`
(prefix `op`) and `harness` (prefix `hrn`) with empty `sources` tuples
(`ledger.py:133`-`:134`) — H16 built this work item's landing pad on
purpose. Two points from requirements 32-33 are worth restating together
because they are the least obvious part of this design: the ledger's
`changes` table is the single source of truth for *that something changed*,
and it stores the minimum needed to prove that (`noun, id, kind, state,
version, at`) — no `search_doc`, no distinction the console doesn't need.
Rendering a `Change` into a console event is where the translation happens,
once, in `nouns/harness.py`'s `/v1/changes` handler: fold `created` into
`"changed"`, and for anything but `"deleted"`, ask the owning noun (reached
through `ctx.nouns`, never imported) to compute a fresh `search_doc` from the
row as it exists *right now*. A deleted row has no "now" to ask about, so
`search_doc` is correctly absent. This keeps `ledger.py` innocent of
`SearchDoc`'s shape entirely — a change nobody has made yet to how search
results are rendered cannot require touching the ledger.

**`"leaves_the_box"`.** The brief specifies this as "generated from every
registered noun's search_doc declaration" without pinning what "declaration"
means, because `NounSpec` (requirement's own protocol) has no per-noun flag
for it — `search_doc(row) -> SearchDoc` is a required method on every
registered noun, not an optional one. Two readings were weighed: (a) a digest
of which `SearchDoc` fields each noun's `search_doc` populates, exposed as a
schema preview; (b) the plural names of every noun currently registered in
`ctx.nouns`. (a) has no named consumer — nothing in `console_fit_plan.md` or
the brief asks the console to branch on which fields a noun's search result
carries, only on which nouns exist at all — and it would need either a
sample row to call `search_doc` against (which a noun with no rows yet
cannot supply) or a second, parallel declaration most noun authors would
have to keep in sync with their real `search_doc` body. (b) is `tuple(ctx.nouns)`:
free, always accurate, and grows automatically the moment H20, H24, H26 or
H27 register a new noun module — no noun module has anything to remember to
declare. (b) is adopted (requirement 38).

**Design guideline 2 (reduce the number of bets).** This work item sits
squarely before the plugin seam — nothing here is a plugin, and nothing about
`workflow`/`node`/`tool` nouns is built or assumed. The applicable form of the
guideline is the narrower one: do not foreclose later addition. The design
satisfies it structurally: a later noun (H20's `conversations`, H24's
`plugins`, ...) is one new file under `nouns/`, registered in the
`DoorContext.nouns` mapping the CLI/server construction passes to `router.handle`;
`router.py` itself needs no change to gain a tenth noun, because the fixed
pipeline (verify → route → scope → idempotency-replay → state/capability
gate → dispatch through `run_bounded` → If-Match check → ETag →
idempotency-store) is written once and is noun-agnostic by construction.
Growth here means more noun modules, not more router logic — the same shape
guideline 2 asks for.

**Design guideline 3 (catch the scenario at the least step-cost).** The
central choice is that fixed pipeline, shared by every noun in one
`handle()`, rather than either (a) a bespoke handler per resource — what
`api_server.py`'s ~8,000 lines amount to, cross-cutting concerns (auth,
idempotency, paging, error shape) re-implemented at every endpoint — or (b) a
declarative framework doing the same cross-cutting work via decorators and
middleware, at the cost of a new dependency. This makes one existing kind of
step — "answer a request" — heavier (a fixed, longer pipeline) rather than
adding a step per noun or per verb. `run_bounded` (requirement 22) is the
sharpest example of that trade actually paying for itself rather than just
being paid once: the "heavier" step costs nothing extra for the overwhelming
common case (a call that finishes inside the bound writes no `operations`
row at all) and only becomes genuinely heavy — a durable row, a ledger write
— for the specific, rare scenario it exists to catch. The cheaper-looking
alternative — let each noun's own code apply idempotency, paging and auth for
itself — was rejected because it multiplies the one place most likely to be
gotten wrong (idempotency, token verification) by the number of nouns instead
of dividing it.

**Design guideline 4 (minimise mutable state).** Inventory of everything this
design introduces that persists or is cached between requests:

| state | why it cannot be derived |
| --- | --- |
| `door_idempotency` rows | A replay response has to be *remembered*, not recomputed — recomputing a `POST`'s effect a second time is exactly what idempotency exists to prevent. |
| `operations` rows | An in-flight job's state is a fact about a running thread, not a pure function of anything else stored — but only once `run_bounded` actually promotes a call past two seconds (requirement 22); a call that finishes inside the bound writes none of this, which is what keeps a plain `list`/`get` from paying for it. |
| The in-process JWKS cache (`auth.py`) | Refetching on every request would make every request's latency and availability depend on the console's JWKS endpoint; the brief's own staleness policy (`refreshed at most once a minute … old keys kept until their tokens could have expired`) is a deliberately bounded cache with a named lifetime, not an unexamined one. |
| `capabilities.DECLARED` | Not request-time state — a source-level constant, changed by a commit, not a write. Included here only to say explicitly why it is out of scope for this table. |

Two things were *not* added as stored state, on purpose: `search_doc` on a
ledger row (§ above — derived, not stored) and a materialised keyset index
for paging (requirement 9 — a Python scan over already-sorted rows, with a
comment marking a SQL keyset as a later maintain item once row counts make
the scan slow). Both were the "add a step" or "make a step heavier" version
of a scenario ("this needs to be fast" / "this needs to be current") that a
stored, cached copy would catch at the cost of a second thing that can go
stale; neither has a caller yet that the plain derivation is too slow for.

**Policies applied.**

- `project-structure`: `src/sadana/door/` as a package, I/O modules
  separated per file (requirement 41), CLI wiring in
  `subcommands/door.py` matching `subcommands/editor.py`'s own shape.
- `testing-conventions`: the conformance test is a `contract`-marked test
  (an existing, unused marker this item is the first to fill), calls `handle()`
  directly rather than opening a socket (no network in the unit-adjacent
  suite), and a locally generated test key rather than a real console JWKS —
  consistent with this project's network-ban-in-tests rule; a real round trip
  against a live JWKS endpoint is explicitly out of scope for `make test`
  and would be proven, if ever, the way `deploy_evidence`-style checks in
  this project are proven: a standalone script outside `make test`, pasted
  as Evidence. Not needed here because H19 mints its own dev JWKS and never
  talks to a real console.
- `reference-lookup`: applied throughout § Design above; every adoption and
  decline names the specific hermes file and line, per guideline 1.
- No brand or UX skill applies — this work item has no browser surface of
  its own.

## Interface

**Package surface** (importable names a caller outside `door/` uses):

```
door.request.DoorRequest(method, path, query, headers, body)
door.request.DoorResponse(status, headers, body)
door.request.json_response(status, payload, *, etag=None) -> DoorResponse
door.request.problem_response(problem) -> DoorResponse
door.problems.Problem, door.problems.CODES, door.problems.make(code, detail=None, errors=None, instance=None)
door.grammar.parse_list_params(query, *, filterable, orderable) -> ListParams | Problem
door.grammar.page(rows, params) -> ListResponse
door.grammar.render_ts(t: float) -> str
door.grammar.parse_ts(s: str) -> float
door.filter.parse(expr: str) -> Ast | Problem
door.filter.evaluate(ast, row: Mapping) -> bool
door.idempotency.replay(conn, *, key, sub, method, path, body) -> Replay | None
door.idempotency.store(conn, *, key, sub, method, path, body, response) -> None
door.operations.run_bounded(conns, fn, *, timeout=2.0, resource=None) -> BoundedResult
door.operations.get(conn, id) -> Operation | None
door.operations.resume_on_start(conns) -> None
door.auth.verify(headers, *, box: BoxIdentity) -> Principal | Problem
door.auth.require_scope(principal, "noun:verb") -> None | Problem
door.auth.account_key_for(principal) -> memory.AccountKey
door.capabilities.ALL, door.capabilities.declared() -> tuple[str, ...]
door.nouns.NounSpec, door.nouns.ActionSpec, the noun protocol
door.router.DoorContext(conns, runtime, verifier, capabilities, nouns, clock)
door.router.handle(request, *, ctx: DoorContext) -> DoorResponse
door.serve.make_server(host, port, *, ctx: DoorContext) -> http.server.ThreadingHTTPServer
```

**Wire shapes** are `docs/console/grammar.json`'s job to state formally; this
section names only what crosses the Python boundary, since the wire shapes
are the conformance test's subject and would otherwise be specified twice and
drift.

**Errors as data, not exceptions.** Every noun-module function
(`list`/`get`/`create`/`update`/`remove`/`act`) returns either a row/rows or a
`Problem` — never raises for an expected outcome (echoing `CLAUDE.md`'s
`DagResult`-shaped rule for plugins, applied here to the door). `router.handle`
is the only place a `Problem` becomes a `DoorResponse`.

## Acceptance criteria

- [ ] `router.handle` answers every path in requirement 1 against the
      `harness` noun and the test's own `widgets` fixture noun, with no
      socket involved.
- [ ] `parse_list_params` rejects a fifth query key by name; tolerates
      `count=true`; rejects an unlisted `order_by` field; rejects an
      out-of-range `page_size`.
- [ ] `filter.py` parses every operator, `AND`/`OR`/`NOT` precedence,
      parentheses, quoted-string escapes, numbers, ISO-8601 dates,
      booleans, and rejects three deliberately malformed inputs.
- [ ] A `page_token` minted under one `order_by` is rejected under another.
- [ ] Keyset paging is stable when a row is inserted between page one and
      page two of a list already in flight.
- [ ] Every `GET` on a single resource carries `ETag`; a stale `If-Match` on
      `PATCH`/`DELETE`/an action is `412` naming both versions; a request
      missing `If-Match` entirely on one of those is also `412`, with a
      detail distinguishing "missing" from "stale."
- [ ] A `list`/`get` that finishes well inside the two-second bound creates
      zero rows in `operations` and writes zero extra ledger rows — asserted
      directly, not inferred, against the fixture noun.
- [ ] `capabilities.ALL` has exactly sixteen names; `POST
      /v1/harness/actions/deregister` and `.../purge-account` are `501`
      via the undeclared-verb path (requirement 26), not the
      capability-gate path.
- [ ] A replayed `POST` (same key, same body) returns the identical stored
      status and body and creates nothing; the same key with a different
      body is `422 IDEMPOTENCY_MISMATCH`.
- [ ] An action from the wrong state is `409` naming both the required and
      current state; behind an undeclared capability, `501` naming it.
- [ ] An unknown id and another account's id are both `404`, never `403`.
- [ ] An expired token is `401`; `hrn` mismatch is `403`; org mismatch is
      `403`; a missing scope is `403` naming it.
- [ ] Any `429` the router emits carries `Retry-After`.
- [ ] `tests/contract/test_console_grammar.py` validates every response
      against `docs/console/grammar.json` and fails loudly, never skips, if
      that file is missing.
- [ ] `make verify` ends `VERIFY OK`.
- [ ] A curl transcript against `sadana door serve`, authenticated with
      `sadana door token`: `GET /v1/harness`, `GET /v1/changes?since=0`,
      `GET /v1/inventory`, one `401`, one `403`, one `404`.
- [ ] Existing CLI subcommands (`editor`, `chat`, `conversations`, …) behave
      unchanged.

## Non-goals

- No real tether/socket transport (H30). `serve.py` is a loopback
  development listener only.
- No persisted organisation-at-enrollment (H30); an unset `box.org` means
  "any," per requirement 29.
- No noun besides `harness` (H20, H18, H24, H26, H27 add the rest, each its
  own work item).
- No streaming (`streaming`, `runs.live` capabilities); no SQL-backed
  keyset paging; no second storage backend or `Protocol` seam for one
  (`CLAUDE.md`'s registry-of-one rule — nothing here builds toward a second
  backend that does not exist yet).

## Open questions

- **JWKS unreachable on a cold cache.** The console's contract names `401`
  for "missing or unparseable" and "expired," but says nothing about an
  unknown `kid` with no cached key and a failed fetch — genuinely different
  from both. Recommendation, not yet decided: `401 UNAUTHENTICATED`, on the
  reasoning that from the caller's side the token simply could not be
  verified, same as the two named cases — but this is this item's own
  extrapolation of a closed, externally-owned code list, not something in
  the contract itself, and belongs in front of whoever owns that contract
  before build starts.
- **Whether `leaves_the_box = tuple(ctx.nouns)` is what the console's own
  A04 generator expects.** `grammar.json`'s own `$comment` (requirement 50)
  already commits to diffing the two repos' shapes at A04's acceptance test;
  this field is the one part of `GET /v1/harness`'s body this spec had to
  infer rather than transcribe, and it is the one worth confirming first.

## Rejected alternatives

**Storing `search_doc` on the ledger's `changes` row.** The natural first
instinct — the console needs it at `/v1/changes` time, so why not have it
already there? Rejected under guideline 4: it duplicates fields the owning
resource's own row already carries, in a shape (`title`/`subtitle`/`body`/
`facets`) that would go stale the moment a noun's rendering of its own
`search_doc` changed without a corresponding ledger backfill. Deriving it at
render time (requirement 33) means there is exactly one place `SearchDoc`'s
shape is decided — the noun's own `search_doc(row)` — and the ledger never
has an opinion about it.

**A `door` `Protocol`/registry seam for "the transport."** Tempting, since
the brief names two transports (loopback, tether). Rejected: `CLAUDE.md`'s
own rule is that a registry seam earns its cost only once a second real
member exists to register, and `serve.py` already *is* the direct call for
the one real transport this item builds; H30 is a second file, not a seam
this file has to anticipate. `router.handle`'s purity is what actually makes
two transports possible later — not a registry.

**Following the console's contract's age-only idempotency retention exactly
as hermes's own newer pattern would improve it.** Considered adopting
`api_server_run_idempotency.py`'s terminal-aware pruning (requirement 19's
neighbour, § Design above) wholesale. Declined for this item specifically
because `door_idempotency` and `operations` are already two separate tables
with two separate lifetimes (§ Design's table), so the scenario that pattern
exists to prevent — pruning a reservation for a job that is still running —
cannot happen here the way it can in hermes's single combined table. Flagged
as an open question for the console side rather than silently adopted or
silently dropped.

**Splitting this into more than one work item.** Put to the author in the
plan-stage interview and declined; recorded in `intent.md`'s trace. Restated
here because it is also a design-guideline-2 judgment: a partially-built
pipeline (auth without idempotency, or routing without error shaping) is not
a smaller bet, it is an *untestable* one — the conformance test needs the
whole pipeline in place to assert anything meaningful, so splitting would
have meant a first commit with no acceptance criterion it could actually
meet.

## Concerns

Five, in the order a reviewer should look at them. The first draft of this
section stopped at four, and a cold independent review (recorded below) found
a real fifth and, separately, an outright arithmetic error and a genuine
architectural ambiguity in what were then requirements 22/24 — both fixed
above, not merely noted here, because they were wrong rather than merely
uncertain.

**`run_bounded`'s lazy-promotion split (requirements 22-24) is new,
previously-implicit reasoning, exercised by nothing yet.** The first draft of
this spec said only "turns any noun-verb return that takes longer than two
seconds into `202`," which does not by itself say *how* the router can give
up waiting on a synchronous call without killing it — the only way to do that
is to run every dispatch through a thread from the start, and the question a
cold review correctly pushed on was whether that also meant writing an
`operations` row for *every* dispatch, which would have taken the write lock
on every plain `GET` and undercut the reader/writer split `console_fit_plan.md`
§5(f)/P8 exists to protect. The fix (write nothing until the two-second mark
actually passes) is argued for above and believed correct, but it is
reasoning about a race — dispatch, wait, maybe-promote — that has not yet been
written as code or exercised by a test with an artificially slow fixture verb.
This is the single place a build-stage reviewer should look hardest, more so
than any of the other four below.

**`"leaves_the_box"`'s semantics are this spec's own inference, not a
transcription.** Everything else in § Requirements is the console's contract,
implemented verbatim by design (`intent.md`'s own constraint). This one field
had no field-level specification to transcribe, only a sentence describing
its intent. `tuple(ctx.nouns)` is the reading argued for in § Design and
flagged again in § Open questions — it is cheap to change (one function body)
if the console's own generator expects something else, which is exactly why
it was resolved by inference rather than left blank: a placeholder here would
block the conformance test's schema validation for no gain, and the fix, if
wrong, costs one line.

**The idempotency-retention tension (§ Rejected alternatives) is a real gap
between what this box's own reference corpus knows and what the console's
frozen contract specifies, and this item follows the contract.** That is the
right call given `intent.md`'s constraint, but it means a scenario hermes's
own production experience already ran into (an in-flight reservation pruned
by age alone) is only *structurally* prevented here by `operations` being a
separate table — nobody has re-derived that safety property from first
principles for this specific schema, only argued it by analogy. Worth a
second look before build.

**This is the first work item to write to `ledger.py`'s `operations` and
`harness` reservations that H16 left empty.** `ledger.record_change`
(`ledger.py:188`) raises on an id whose prefix doesn't match its noun — a
real safety net — but nothing in H16 exercised the `operations`/`harness`
branches of that check, because nothing wrote to them yet. This spec is
confident the reservation is correctly shaped (verified by reading, not by
running anything against it), but "the first real write exercises code that
has sat unused since H16" is exactly the kind of gap `testing-conventions`
would want a direct test for, not an inference from reading two files
side by side.

**This draft's own `file.py:NNN` citations were not mechanically
re-verified before the first version of this document was handed over, and a
cold review caught six stale or wrong ones** (an off-by-19-line drift in two
`ids.py` citations that grew consistently from an intervening docstring edit,
one citation — the old `ledger.py:112` for `Change.kind` — that pointed at
genuinely unrelated code, and others off by a handful of lines). None changed
what was being claimed, only where to look for it, but `CLAUDE.md`'s own
Research Subagents rule treats an uncited claim as incomplete, and a citation
that is *wrong* is worse than none: it sends a build-stage reader to the
wrong place with full confidence. All six are corrected above. The practice
gap this exposes — cite while reading, then never re-grep the citations
against the final text — is worth naming so it doesn't repeat on the next
foundation-sized spec.

This spec.md has now had the second, deliberate pass `CLAUDE.md`'s brief for
H19 asked for before `plan.md`: an independent cold review found the
arithmetic error in requirement 35, the `run_bounded` ambiguity now resolved
in requirements 22-24, the undeclared-action gap now resolved in requirement
40, the missing-`If-Match` gap now resolved in requirement 25, and the six
citation errors above. Its two open questions (JWKS-unreachable-on-cold-cache;
`leaves_the_box`'s exact semantics) were re-examined and found to still be
genuinely unresolvable from inside this repository, not merely unresolved —
both need the console side, not another reading of this spec.

---

**A rule this spec would bind beyond this work item**, for approval before it
is written anywhere:

> - A ledger `Change` row never carries a resource's `search_doc`; a door
>   renders one at request time from the owning noun's own `search_doc(row)`,
>   and a ledger `created` row renders as console event kind `"changed"` —
>   never a fourth, wire-visible kind.

If you approve it, it belongs in `CLAUDE.md`'s `## Please do` list as that one
line.
