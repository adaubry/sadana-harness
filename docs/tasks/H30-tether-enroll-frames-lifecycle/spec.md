# Spec: The tether — enroll, connect, and be reachable

Intent: docs/tasks/H30-tether-enroll-frames-lifecycle/intent.md

Author: Adam Aubry (project owner), drafted by Claude. Status: approved.

## What the prompt assumed and what is true

Checked against the real tree, per this work item's own instruction to read
what actually landed before writing anything.

- **Capability names.** The build prompt assumed H18 declared
  `approvals.wait`/`approvals.call`. `src/sadana/door/capabilities.py` confirms
  both, verbatim, already in `DECLARED`. No correction needed.
- **`ledger.record_change` has no `threading.Condition`.** Confirmed —
  `src/sadana/ledger.py` exposes `record_change`, `changes_since`,
  `ledger_head`, `inventory` and nothing else; no wait/notify primitive
  exists today. This spec declines to add one — see Rejected alternatives.
- **`BoxIdentity`'s current source.** `src/sadana/subcommands/door.py:104`
  builds it from CLI args/config (`auth.BoxIdentity(harness_id=harness_id,
  org=org)`), not from a persisted identity file. This item is what replaces
  that call site with `identity.load()`.
- **`door/nouns/harness.py`'s `upgrade` action already exists as a
  placeholder** (`from_states=(), to_state="", capability="upgrade"`), added
  by H19 with a comment naming this item as the one that gives it real
  meaning, and `get_harness()` already hardcodes `"tether": "disconnected"`.
  Both are edited here, not created from nothing.
- **`plugin_install.py`'s tag validator is private** (`_is_valid_tag_syntax`).
  The build prompt says to reuse it "promoted to a public name" — confirmed
  necessary; it is currently unreachable from outside that module.
- **`door/operations.resume_on_start` today unconditionally fails every
  `running` row** with `{"detail": "the process restarted"}` — it has no
  branch for an operation that *expects* to survive its own restart. The
  upgrade design below adds exactly one.
- **`sadana.__version__` is a hand-written string literal**
  (`src/sadana/__init__.py`), not derived from installed package metadata.
  See Design → Upgrade for why this stays a literal.
- **`console_fit_plan.md` §5(f)`'s locking design and H16's
  `stores.Connections`/`conversation_lock`** are exactly as described in
  memory: one writer connection behind a lock taken inside a transaction,
  thread-local readers, one lock per conversation. The tether's inbound task
  uses `ctx.conns.reader()`/the door's own pool, never a connection of its
  own — nothing new needed here.

## Requirements

1. The box generates and persists its own P-256 key pair before it ever
   contacts a console. The private key never leaves the box, and is never
   written to a log, printed, or included in an error message, under any
   failure.
2. `sadana enroll <token> --relay <url> [--console <url>] [--replace-key]`
   posts `{public_key, version, capabilities}` to `POST <relay>/enroll`
   with the one-time bearer token; verifies the token's claims against the
   console's JWKS when the token names an issuer, otherwise requires
   `--console` and says plainly that the token is being trusted unverified.
   On success it persists the enrolled identity; refuses to overwrite an
   existing identity without `--replace-key`; a `401` is reported as "the
   enrollment token was refused; mint a new one in the console," not a raw
   HTTP error.
3. The box connects outbound only, to `<relay>/connect`, and opens no
   inbound listener under any configuration — this holds regardless of what
   else the process is already listening on (the webhook channel).
4. The connection proves the box's identity by signing a server-issued
   nonce with its own private key, never by presenting a static secret. Any
   reply to the signed challenge other than `welcome` closes the connection
   and backs off rather than proceeding.
5. Once connected, the box's `hello` frame states its live capability set,
   computed at connect time from the door's own declared list — never a
   value fixed at process start — so a reconnect after a capability-adding
   deploy advertises the new set with no code change and no restart beyond
   the one that already happened.
6. The box sends a `heartbeat` frame every 15 seconds while connected. It
   never marks itself degraded or offline; that judgment belongs to the
   console alone.
7. A `429` reply to `/connect` makes the box sleep exactly the given
   `Retry-After` before retrying. Every other disconnection or connect
   failure reconnects with exponential backoff, full jitter, and a 60-second
   cap; the attempt counter resets after a connection that stayed up at
   least 60 seconds.
8. A `request` frame is answered by exactly one `response` frame carrying
   the same id. Dispatch runs off the tether's own event loop, so one slow
   turn cannot delay a concurrent fast list. Every token inside a request is
   verified by the door exactly as if it had arrived over the loopback
   listener — the relay is never trusted for identity, regardless of what
   channel a request arrived on.
9. A durable change lands as an `event` frame, in ledger order, with no
   gap, for as long as the connection lasts. A dropped connection is
   expected to leave a gap; closing that gap is the console's own job
   (`GET /changes?since=<cursor>`, seeded by the `hello` frame's own
   `ledger_head`), not something the box has to remember across a reconnect.
10. An ephemeral (streaming) frame reaches the console only while the
    connection carrying it is live. One still queued from before a
    disconnect is discarded on reconnect, never delivered late — a stale
    delta is worse than a missing one.
11. Every frame type the box's decoder does not recognise answers with an
    `error` frame. It never crashes the process and never silently drops
    the connection over an unrecognised frame.
12. `GET /v1/harness` reports the tether's real, current connection state,
    not a hardcoded placeholder, alongside the box's version, capabilities
    and organisation.
13. An operator can trigger a remote upgrade. The box records the target
    version as a running operation before touching anything, runs the
    upgrade as a process that outlives the Python process being upgraded,
    and on the next boot resolves that operation to succeeded or failed by
    comparing the box's own reported version against the recorded target —
    with nobody present for any of it.
14. An operator can trigger a remote deregistration. The tether stops, the
    box's local identity and private key are deleted, and a tombstone is
    recorded. Every other row the box holds is left exactly as it is.
15. An operator can trigger an account purge. Every table carrying an
    account's data is enumerated and cleared for that one account, and a
    test fails, by name, the moment a table gains an account column this
    enumeration does not already know about.
16. A fresh, unconfigured Ubuntu machine reaches a connected box by
    following only the written install steps, with no manual step the
    document omits.
17. Because no real console relay exists yet, a fixture relay speaking the
    same protocol ships in this repository, so every requirement above is
    provable without the other repository existing.
18. `make verify` ends `VERIFY OK` with this item's own tests included, and
    a separate, hand-run script proves the whole path — enroll, connect,
    dispatch, push, disconnect, reconnect, backoff — against a real (if
    local) socket, since this is the first work item whose own contract
    requires a real external round trip.
19. Exactly one new runtime dependency is added, for the one outbound
    socket this needs.
20. The door's declared capability list gains exactly one new name for this
    item's own feature (`upgrade`), and the console-facing documentation is
    updated to match — coordinated with a concurrently-running, unrelated
    piece of work that touches the same three files.

## Design

**Reference corpus, applied**

A background research pass read hermes-agent's closest analogue in full:
`gateway/relay/{__init__,auth,transport,ws_transport,command_manifest,media}.py`,
`hermes_cli/gateway_enroll.py`, `hermes_cli/heartbeat.py`, and
`docs/relay-connector-contract.md`. Two files turned out to be false leads
despite matching names: `hermes_cli/heartbeat.py` is a *session* heartbeat
(re-injecting a prompt into an idle chat), unrelated to connection liveness;
`docs/observability/relay-shared-metrics.md` is an NVIDIA telemetry SDK
document, not a protocol document. Neither is cited below as precedent.

**Adopted:**
- **Answer-by-request-id via a per-connection lookup**
  (`ws_transport.py:837-909,1095-1098`) — exactly `frames.py`'s
  `request`/`response` shape, one id, one reply.
- **Self-scheduling reconnect** — the reader task's own exit path starts the
  next connection attempt, rather than an external watchdog polling
  connection state (`ws_transport.py:960-973`).
- **Bounded drain, then unconditional fail-all, on teardown**
  (`ws_transport.py:617-680`) — applied to this box's own in-flight
  dispatches: `client.py`'s shutdown waits briefly for outstanding
  `door.handle()` calls the executor is running, then lets the connection
  close under them rather than blocking forever.
- **"Authenticate the channel once, trust it after" over per-message
  signing** — `docs/relay-connector-contract.md` §6 documents that hermes's
  own connector performs no per-message platform verification once inside
  an authenticated socket; this box does the same at the channel level
  (challenge/response once, at connect) while *also* keeping H19's
  per-request token check, because those two checks answer different
  questions (which box is this socket vs. which principal, with which
  scope, is this specific request). Not redundant; stated once here so a
  reviewer does not read it as the same check done twice.
- **The one paragraph most worth citing for the constraint this box
  already lived by:** `docs/relay-connector-contract.md` §3 (lines 75-146)
  documents that hermes's own inbound-over-signed-HTTP design was replaced
  because it required every gateway to expose a reachable inbound URL —
  "impossible for hosted gateways." Independent, first-party confirmation
  that "no inbound port, ever" is a lesson learned under load, not a
  preference.

**Declined**, each with what specifically breaks if transplanted:
- **Shared-secret HMAC channel auth** (`auth.py`) requires the secret to
  exist on both ends — exactly the liability an asymmetric key pair exists
  to remove, and wire.md's own challenge/response already supersedes it.
- **Capped-doubling backoff with no jitter** (`ws_transport.py:1018-1048`)
  — the production precedent has none; sadana's own frozen decision is full
  jitter, an improvement the reference never had to test at its own scale.
- **Handshake-history-aware terminal-vs-retryable judgment** on a revoked
  identity (`ws_transport.py:939-973`, the `_auth_revoked` flag) — the
  wire protocol here treats every non-`welcome` reply uniformly (close,
  back off, retry). Inventing a terminal state the protocol does not define
  would mean not "implementing it verbatim." See Concerns for the cost.
- **A `Transport` `Protocol` separating wire logic from adapter logic**
  (`transport.py`) — no second real transport exists or is planned; the
  fixture relay proves this protocol from the *server* side, which is not a
  reason to add a client-side seam. One concrete transport, direct
  `websockets` calls, per CLAUDE.md's registry-of-one rule.
- **A pending-request/future correlation table for outbound requests the
  box itself originates** (`ws_transport.py`'s `self._pending`) — this
  protocol never has the box originate a request needing a reply; it only
  answers and pushes. There is nothing to correlate, so this state is
  never built at all.
- **A side HTTP channel for binary payloads, referenced by id/URL**
  (`media.py`) — wire.md already fixes binary response bodies as inline
  base64 inside the `response` frame; redesigning that is out of scope.
  The lesson taken instead: a client-side size cap that fails fast, rather
  than trying to inline an unbounded blob into one WebSocket frame
  (Requirement 8's Interface, below).

**`src/sadana/tether/` (new package)**

- **`keys.py`** (I/O). `generate() -> KeyPair` (P-256, via `cryptography`,
  already present transitively through `PyJWT[crypto]` — no new dependency).
  `save(key, path, *, replace=False)`: opens with
  `os.O_CREAT | os.O_EXCL | os.O_WRONLY`, mode `0o600`; a `FileExistsError`
  is refused unless `replace=True`, in which case the old file is removed
  first. `load(path) -> KeyPair`. `KeyPair.sign(nonce: bytes) -> bytes`
  produces the **raw r‖s** signature (32 bytes each, big-endian,
  concatenated — not DER), via
  `cryptography.hazmat.primitives.asymmetric.utils.decode_dss_signature`
  unpacking `ec.ECDSA(hashes.SHA256())`'s DER output. This is the same raw
  format JOSE's ES256 uses, so it stays consistent with the console's own
  token format even though this signature is never itself a JWT.
  `KeyPair.public_pem() -> bytes` for the `/enroll` body.
- **`identity.py`** (I/O). `Identity` dataclass: `harness_id`, `org`,
  `relay_url`, `console_url`, `enrolled_at`. `load()`/`save()`/`remove()`
  against `state_dir/tether/harness.toml` (`tomllib` to read, a small
  hand-written writer — no new dependency for one flat table). `load()`
  returns `None` when the file does not exist, never raises for "not
  enrolled yet."
- **`frames.py`** (pure). One frozen dataclass per frame in wire.md §5
  (`Challenge`, `ChallengeResponse`, `Welcome`, `Hello`, `Heartbeat`,
  `Request`, `Response`, `Event`, `Ephemeral`, `Error`). `encode(frame) ->
  dict`, `decode(raw: dict) -> Frame | UnknownFrame`. An unrecognised
  `"type"` decodes to a sentinel that every caller turns into an `Error`
  frame back — never an exception. `Request` round-trips to/from
  `door.request.DoorRequest`; `Response` round-trips to/from
  `door.request.DoorResponse` (encoding a binary body as base64 with
  `"encoding": "base64"` per wire.md, and refusing — as a clean `Error`
  frame, never a crash — a decoded body over
  `SADANA_TETHER_MAX_RESPONSE_BYTES` (config, default 8 MiB): the direct,
  narrow lesson taken from hermes's own `MEDIA_MAX_BYTES` client-side cap,
  without adopting its side-channel).
- **`backoff.py`** (pure). `next_delay(attempt, *, base=1.0, cap=60.0, rng)
  = rng.uniform(0, min(cap, base * 2 ** attempt))`. `honour_retry_after
  (seconds) -> float` (identity function with a name, so a call site reads
  as a decision rather than a bare literal).
- **`client.py`** (I/O). The connection loop, on its own `daemon=True`
  thread with its own `asyncio` event loop — the same shape
  `scheduling.run_tick_loop` already established for a long-lived
  background loop that needs no coordination with `gateway_daemon.run()`'s
  own shutdown sequence. One `TetherState` dataclass (`disconnected |
  connecting | connected`, `connected_at`, `last_heartbeat_at`, `attempt`),
  behind one `threading.Lock`, read by `door/nouns/harness.py`'s
  `get_harness()`.

  Per connection: connect → receive `challenge` → sign and send
  `challenge_response` → require `welcome` (anything else: close, back
  off) → send `hello` (capabilities from `capabilities.declared()`, called
  fresh at this exact moment; `ledger_head` from
  `ledger.ledger_head(ctx.conns.reader())`) → three tasks until the socket
  closes:
  - **heartbeat**: send every 15s.
  - **inbound**: a `request` frame → `loop.run_in_executor(door_pool,
    door.handle, ...)` → a `response` frame by id. `door_pool` is a small
    `ThreadPoolExecutor` this module owns (sized by
    `SADANA_TETHER_DOOR_WORKERS`, default matching `operations.py`'s own
    `SADANA_DOOR_WORKERS`), so a slow turn's dispatch and a fast list's
    dispatch run concurrently instead of serializing on the event loop's
    implicit default executor.
  - **outbound**: one loop, `asyncio.sleep(0.25)` between iterations (the
    documented poll fallback — see Rejected alternatives for why this is
    the *only* mechanism, not a fallback to something heavier). Each
    iteration: (a) `ledger.changes_since(reader, cursor, limit)` from a
    cursor that starts at this connection's own `ledger_head` (never a
    value remembered from a previous connection — Requirement 9) and
    advances only on a successful send; (b) drain `door.events
    .ephemeral_queue` non-blockingly into `ephemeral` frames. **On the
    transition into `connected`**, before this loop's first iteration, the
    queue is drained and discarded once (loop `get_nowait()` to
    `queue.Empty`) — this is the one line that makes the asymmetry real:
    the ledger tail is ordered and gap-free within a connection; the
    ephemeral queue is actively flushed of anything stale from before this
    connection existed, not merely rate-limited by H21's own bounded
    drop-oldest behaviour.

  A recording write's own failure (a send that raises mid-loop) is caught
  and logged, never allowed to kill the loop — matching this project's
  existing rule for observability writes.

  On close or error: state → `disconnected`; sleep
  `backoff.honour_retry_after(seconds)` after a `429`, else
  `backoff.next_delay(attempt, rng=...)`; reconnect. `attempt` resets to 0
  after a connection whose `connected_at` was at least 60s ago at the
  moment it dropped.

  Docstring states explicitly: survives the relay restarting, the network
  dropping, and a handler that raises (the frame gets `INTERNAL`, the loop
  lives); does not survive two tethers in one process (the daemon's own
  lock already forbids two processes) and does not itself decide the box is
  "degraded" or "offline" — that word belongs to the console, watching the
  heartbeats it receives. Also states, explicitly, that `hello`'s
  capability list is computed fresh at connect time — the property the
  console's own capability gating depends on.

**`src/sadana/enroll.py` + `subcommands/enroll.py`**

`enroll.py` (I/O) does the work and returns one of a small closed set of
outcome dataclasses (`Enrolled`, `TokenRefused`, `AlreadyEnrolled`,
`NetworkFailed`) — the "outcomes are dataclasses, the subcommand renders"
pattern already used elsewhere in this project (`plugin_install.py`'s
`FetchedTag`/`TagMismatch`/`FetchFailed`). It decodes the token's claims
without trusting them (to read `hrn`/`org`/`iss` before any verification),
verifies against `iss`'s JWKS when present via `door.auth.Verifier`
(already built, H19), else requires `--console` and prints that the token
is being trusted unverified; generates a key pair (`tether.keys.generate`);
POSTs to `<relay>/enroll`; on `2xx` calls `tether.identity.save(...)` and
returns `Enrolled`. `subcommands/enroll.py` owns the parser and renders each
outcome to an exit code and message, matching `door.py`'s own convention of
owning parser and handlers together.

**`subcommands/gateway.py`**

`cmd_gateway_run` starts the tether thread — `tether.client.start(runtime)`
— beside the existing scheduling-tick thread, exactly the same
`threading.Thread(..., daemon=True)` shape, and only when
`tether.identity.load()` returns non-`None` (no identity, no tether — a
box that has never enrolled runs exactly as it does today).
`gateway_daemon.py` is not edited, per this item's own rule.

**`door/auth.py`**

`BoxIdentity` is now built from `identity.load()` at `door serve`/`gateway
run` startup, not from CLI args/config — `org` is enforced from this point
on (`subcommands/door.py:104`'s call site changes; the module's own
`BoxIdentity` dataclass shape is unchanged, so every existing test
construction keeps working).

**`door/nouns/harness.py`**

- `get_harness()`: `"tether"` reads `tether.client.state()` (one of
  `disconnected|connecting|connected`) instead of the hardcoded string;
  adds `connected_at`.
- `upgrade` action (already declared by H19 as a placeholder): scope
  `harness:upgrade`, capability `upgrade` (newly declared — chain (c)).
  `act()` validates the target tag with `plugin_install
  .is_valid_tag_syntax` (promoted from `_is_valid_tag_syntax` — see below),
  writes an `operations` row `running` with `detail_json =
  {"upgrade_target": tag}` via the same `operations._promote` machinery
  H19 built, then launches `scripts/upgrade.sh <tag>` **detached**
  (`subprocess.Popen` with `start_new_session=True`, stdout/stderr to a log
  file — it must outlive this process's own restart) and returns the
  operation's `202` immediately.
- `deregister` action: scope `harness:deregister`. Stops the tether thread,
  calls `identity.remove()`, deletes `key.pem`, writes a ledger tombstone
  (`ledger.record_change(..., noun="harness", kind="deleted", ...)`).
  Nothing else is touched — stated in the docstring, because "the data is
  the customer's" is a promise, not an implementation detail.
- `purge-account` action: scope `harness:purge`, `consequential=True`
  (default, matching H18's convention for an action that cannot be undone).
  `stores.PURGE_STEPS: tuple[tuple[str, str], ...]` — every `(table,
  account_column)` pair for a table that carries an `account_key` column
  today (`memory_entries`, `memory_rubric_overrides`, `schedules`,
  `persona_selections` — confirmed by grep, not assumed). `purge_account
  (conns, account_key)` deletes matching rows from each, inside one
  `write_txn`. A new test scans `sqlite_master` for every table with an
  `account_key` column and fails, by name, on the first one absent from
  `PURGE_STEPS` — this is the mechanism Requirement 15 asks for, and it is
  a test that would have caught a table added later and forgotten here.

**`door/operations.py` — one additive branch, named explicitly**

`resume_on_start` currently marks every `state='running'` row `failed`
unconditionally. This item adds one branch, before that fallback: a row
whose `detail_json` contains `"upgrade_target"` is instead resolved by
comparing `sadana.__version__` to that value — `succeeded` if they match,
`failed` with `"restarted on <actual>"` otherwise. Every other row (every
existing caller's) is entirely unaffected; this is additive, not a
behaviour change to anything that exists today. Named here, and it belongs
in plan.md's own file list, because `operations.py` is H19's file and this
item is not the one that owns it.

**`plugin_install.py` — one rename, named explicitly**

`_is_valid_tag_syntax` becomes `is_valid_tag_syntax` (drop the leading
underscore; behaviour unchanged). This is the only edit this item makes to
that file, and it belongs in plan.md's own file list for the same reason as
`operations.py` above — `plugin_install.py` is PLUGIN-INSTALL-01's file.

**`sadana.__version__` — stays a literal (Design decision, resolves intent's open question)**

`src/sadana/__init__.py`'s `__version__ = "0.0.1"` is left as a plain
source string, not switched to `importlib.metadata.version("sadana")`. The
literal already answers `--version` correctly and is read directly out of
whatever commit `scripts/upgrade.sh`'s `git checkout <tag> --` lands on,
with no dependency on the install step's own metadata having refreshed
correctly first. The real requirement this surfaces is a process rule, not
a code change: **a release tag must be byte-identical to `__version__` at
that commit**, or `resume_on_start`'s upgrade check can never succeed. See
"CLAUDE.md amendment" below.

**`scripts/upgrade.sh`**

`git -C <repo> fetch --tags`, `git -C <repo> checkout -- <tag>` (`--`
before the tag: CLAUDE.md's own rule against a caller-supplied value being
read as a git option — `<tag>` here is console-supplied, so this is not
optional), `.venv/bin/pip install -e . --quiet`,
`systemctl restart sadana-gateway` (via `gateway_service.restart()`'s own
existing `_run_systemctl` — reused, not re-implemented).

**`scripts/test_relay.py` (~300 lines, fixture)**

A minimal relay speaking exactly the protocol above: `POST /enroll`
(verifies a token it minted itself with its own test ES256 key, stores the
box's public key), `GET /connect` (challenge, verify the raw r‖s signature
against the stored public key, `welcome`, log `hello`, track heartbeats),
`POST /forward/<harness_id>` (a `request` frame → the matching `response`
frame's status/body; `503 HARNESS_OFFLINE` when nothing is connected,
`504` on timeout), `POST /token` (mints console-shaped tokens for a given
`sub`/`org`/`ws`/`hrn`/`scope`), `GET /.well-known/jwks.json`, `GET
/events` (every `event`/`ephemeral` frame logged), `--retry-after N`,
`--die-after N`. Its own docstring says plainly that the console's real
relay replaces it and this box does not change when that happens.

**`scripts/install.sh` / `docs/install.md`**

One section per step: prerequisites, clone at a tag, `.venv`, `pip install
-e .`, `sadana setup`, `sadana enroll`, `sudo sadana gateway install &&
start`, `sadana gateway status`. One sentence stating the security-group
implication ("no inbound rule for the console — the box dials out; if a
rule exists for it, that is a finding"), citing
`docs/relay-connector-contract.md`'s own retrofit as the reason this is a
hard requirement, not a preference. One paragraph on reconnect behaviour.
The lifecycle commands. The test relay named as the local-profile
substitute for a real console.

## Interface

- `tether.keys`: `generate() -> KeyPair`; `save(key, path, *, replace:
  bool=False) -> None`; `load(path) -> KeyPair`; `KeyPair.sign(nonce:
  bytes) -> bytes`; `KeyPair.public_pem() -> bytes`.
- `tether.identity`: `Identity(harness_id, org, relay_url, console_url,
  enrolled_at)`; `load() -> Identity | None`; `save(Identity) -> None`;
  `remove() -> None`.
- `tether.frames`: one dataclass per frame; `encode(frame) -> dict`;
  `decode(raw: dict) -> Frame | UnknownFrame`.
- `tether.backoff`: `next_delay(attempt: int, *, base: float=1.0, cap:
  float=60.0, rng: random.Random) -> float`; `honour_retry_after(seconds:
  float) -> float`.
- `tether.client`: `start(runtime) -> threading.Thread`; `state() ->
  TetherState` (read by `harness.get_harness`).
- `enroll.enroll(token, relay, *, console=None, replace_key=False) ->
  Enrolled | TokenRefused | AlreadyEnrolled | NetworkFailed`.
- `door.nouns.harness`: `act()` gains three real branches
  (`upgrade`/`deregister`/`purge-account`); `get_harness()`'s `tether` field
  is now live.
- `stores.PURGE_STEPS: tuple[tuple[str, str], ...]`;
  `stores.purge_account(conns, account_key: str) -> None`.
- Errors: every wire-level failure is an `error` frame
  (`{"type": "error", "code", "detail"}`); every door-level failure inside
  a dispatched `request` is whatever Problem Details shape `router.handle`
  already produces, carried inside the `response` frame's body exactly as
  the loopback listener would have returned it.

## Acceptance criteria

- [ ] `make verify` ends `VERIFY OK`.
- [ ] Frame codec round-trips every frame type; an unknown `"type"` decodes
      to a sentinel and every caller turns it into an `error` frame, never
      an exception.
- [ ] `backoff.next_delay` stays within `[0, min(cap, base*2**attempt)]`
      across attempts 0-10; `honour_retry_after` returns exactly what it is
      given.
- [ ] A signature produced by `KeyPair.sign` verifies against
      `KeyPair.public_pem()` using the same raw-r‖s convention, and fails
      to verify against a different key pair's public key.
- [ ] `hello`'s `capabilities` field equals `capabilities.declared()` at
      the moment of connect, not a value fixed earlier in the process.
- [ ] `hello`'s `ledger_head` equals `ledger.ledger_head()` at connect time.
- [ ] The outbound ledger tail, driven against a fake sender, sends every
      row exactly once, in cursor order, starting from the connection's own
      `ledger_head` — never skipping and never repeating one already sent
      in this connection.
- [ ] Disconnect mid-turn, reconnect: the changes catch-up
      (`GET /changes?since=<cursor>`) is complete, and no `message.delta`
      queued before the disconnect is replayed after reconnect.
- [ ] `upgrade`'s tag is validated with `plugin_install.is_valid_tag_syntax`
      before anything runs; `resume_on_start` marks the operation succeeded
      when `__version__` matches the target and failed, naming the actual
      version, otherwise — and every other `running` row's existing
      behaviour is unchanged by this branch.
- [ ] `PURGE_STEPS` names every table `sqlite_master` reports with an
      `account_key` column; the completeness test fails by name if one is
      missing.
- [ ] `enroll()`'s four outcomes are each exercised against a monkeypatched
      `urllib`, with no real network call in the unit suite.
- [ ] `identity.save`/`load`/`remove` round-trip through a real (tmp-dir)
      file.
- [ ] `scripts/prove_tether_e2e.py`'s full run: enrolls, connects, forwards
      a `GET`/`list`/`POST`, sees the resulting event reach `/events` with
      a cursor, survives a relay kill/restart with a reconnection inside
      60s and a logged jittered delay, forwards `GET /v1/changes?since=`
      after that reconnect and finds nothing missing, and observes exactly
      one `--retry-after 7` sleep of 7 seconds.
- [ ] `docs/install.md`'s steps, followed on a fresh host, reach
      `sadana gateway status` reporting connected.
- [ ] `GET /v1/harness` reports live tether state, not a placeholder.
- [ ] `declared()` returns all sixteen names in `capabilities.ALL` except
      `settings.write`/`secrets.write` (H14's, landing after this item).

## Non-goals

- The staging-EC2 checkpoint against a real box (HCP) — a separate, later
  work item; this one's finish line is the fixture relay and CI.
- A generic multi-transport seam (`Protocol`-based `Transport`) — no second
  transport exists to justify one.
- A side HTTP channel for binary artifact downloads — wire.md fixes inline
  base64 for now; a side channel is future work if artifact sizes outgrow
  the size cap this item adds.
- Two tethers in one process, or coordinating two processes' tethers — the
  daemon's own single-instance lock already forbids two processes.
- A browser-facing pty bridge — already declined, `console_fit_plan.md`
  §2.3.
- Distinguishing "revoked" from "transiently unreachable" on reconnect —
  the wire protocol as given does not carry that distinction; see Concerns.

## Rejected alternatives

Every rejection below is argued in Design's "Reference corpus, applied"
subsection or inline where the decision is made; collected here for the
four design guidelines specifically.

1. **Learn from the reference.** Six hermes files read in full; adopted the
   answer-by-id pattern, self-scheduling reconnect, bounded-drain teardown,
   and "authenticate the channel once" — each cited above with file:line.
   Declined shared-secret HMAC auth, no-jitter backoff, terminal-close-code
   judgment, a Transport `Protocol`, a pending-request future table, and a
   binary side-channel — each with the specific coupling or assumption that
   would break if transplanted, not a bare preference.
2. **Reduce the number of bets.** This item sits below the plugin seam — it
   is core substrate (P5/P10/P11), like the door itself, not a plugin, so
   "growth means more plugins" does not directly apply. What does apply:
   avoid a decision that would make a future seam expensive. Declining the
   `Transport` `Protocol` is exactly that — the fixture relay already
   exercises the protocol from the outside; a client-side seam with one
   real implementation is a bet with no second payer.
3. **Catch the scenario at the least step-cost.** The load-bearing choice
   is authenticating the *channel* once (a step, taken once per
   connection) rather than every message (a step made heavier, paid by
   every request forever). A cheaper move still exists and was rejected:
   relying on the loopback door's own per-request token check alone,
   *without* the channel-level challenge, would still catch "is this
   request authorized" but never catches "did this socket ever prove it
   belongs to this harness_id at all" — a step the protocol needs and a
   token alone cannot supply, since a token names a principal, not a
   transport.
4. **Minimise mutable state.** Full inventory: `TetherState` (necessary —
   "is the socket connected" cannot be derived, and `GET /v1/harness`
   needs it); the private key and `harness.toml` (necessary — the box's
   own identity); the per-connection outbound cursor (necessary but
   *deliberately not persisted* — resets to `ledger_head()` every
   connection, on the argument that the console's own catch-up path
   already exists and remembering it across a reconnect would duplicate
   that path for no benefit); `door_pool`, one `ThreadPoolExecutor`
   (necessary — a thread pool cannot be derived, only sized and owned).
   **Declined**: a `threading.Condition` on `ledger.record_change` to wake
   the outbound task instantly, instead of the documented 250ms poll. The
   intent's own outcome ("push updates the moment they happen") is already
   satisfied by a 250ms bound without touching a file
   `[[project-h16-store]]` already closed; add the wake-up only once a
   real workload demonstrates it needs sub-250ms latency, which nothing in
   this item's own acceptance criteria does. **Declined**: deriving
   `__version__` from installed package metadata instead of the existing
   literal — no benefit over reading the file `git checkout` just placed,
   and a new dependency on the install step's own metadata having
   refreshed first.

## Concerns

- **A genuinely revoked box has no protocol-level way to learn to stop
  retrying.** The wire protocol treats every non-`welcome` challenge reply
  uniformly (close, back off, retry forever, capped at 60s). Only a local
  `sadana deregister` or an operator killing the process stops it. Accepted
  because retries are cheap and capped, and because inventing a terminal
  state the frozen protocol does not define would mean not implementing it
  verbatim — but the console team should know this box-side behaviour when
  they design their own revoke path, since "revoke" will not, by itself,
  make a box stop dialing in.
- **Two files this item edits belong to closed work items**: `door/
  operations.py` (H19) gains one additive branch in `resume_on_start`;
  `plugin_install.py` (PLUGIN-INSTALL-01) gains one rename
  (`_is_valid_tag_syntax` → `is_valid_tag_syntax`). Both are named here,
  both are minimal and backward-compatible, and both belong in plan.md's
  own file list rather than arriving as an unannounced diff.
- **Base64-inlining a large artifact download into one WebSocket frame
  does not scale indefinitely.** This item adds a conservative,
  config-backed size cap (`SADANA_TETHER_MAX_RESPONSE_BYTES`, default 8
  MiB) as a stopgap consistent with wire.md's own fixed shape, not a
  redesign. A real side-channel, if artifact sizes outgrow this, is future
  work — flagged, not solved, here.
- **Dependency-budget tension.** `console_fit_plan.md` §5(c) closes the
  budget at two dependencies, once each; `websockets` is the one this item
  spends. If it needs its own mypy override (unconfirmed until it's
  actually installed and typechecked), that is a second, small exception to
  "the standard library is the first answer," accepted because a hand-rolled
  WebSocket client was explicitly ruled out ("websockets, nothing else").
- **Lane coordination is a build-stage fact, not a design one**, but it
  binds this item's own file list: `door/capabilities.py`, `docs/console/
  nouns.md`, `docs/console/capabilities.md` are touched by this item and,
  concurrently, by H24 in the other lane. plan.md sequences this item's
  edits to those three files after H24's commit merges; if H24 has not
  merged when that point is reached, intent.md's own constraint says
  proceed and record it rather than block — restated here so plan.md
  inherits it rather than re-deciding it.

## CLAUDE.md amendment — for approval, not applied

The upgrade design above establishes a rule that binds every future
release, not just this item: **`resume_on_start`'s upgrade check can only
ever succeed if a release tag is byte-identical to `sadana.__version__` at
that commit.** That is a project-wide process rule this spec depends on but
cannot itself enforce in code. Proposed line, for a `## Please do` entry:

> A release tag name is byte-identical to `sadana.__version__` at that
> commit — the upgrade action's own success check, and `sadana --version`,
> both depend on the two never diverging.

Not added to CLAUDE.md by this spec — surfaced for your approval first.
