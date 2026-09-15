# Plan: The tether — enroll, connect, and be reachable (from intent.md 2026-09-15)

Author: Adam Aubry (project owner), drafted by Claude. Status: approved.

## Context

`intent.md` and `spec.md` are both approved. This is the sadana-harness
console-fit chain's step H30: the box currently has a door (H19) that only a
request already inside its own loopback can call — nothing external can
reach it. This work item gives the box one outbound WebSocket to a hosted
console: it enrolls once with a self-generated key pair, authenticates by
signing a server challenge, heartbeats, answers framed requests, pushes
ledger changes and streaming deltas, reconnects with full-jitter backoff,
and exposes three remote lifecycle operations (upgrade / deregister /
purge-account). Because the console's own relay doesn't exist yet, this
item also ships a fixture relay and a hand-run end-to-end proof script.

Two closed files get small, named, additive edits (`door/operations.py`,
`plugin_install.py`) — called out explicitly per this project's convention
of never silently modifying a prior work item's file. Three files
(`door/capabilities.py`, `docs/console/nouns.md`, `docs/console/
capabilities.md`) are concurrently owned today by lane B's H24 work; this
item's edits to those three are sequenced into their own chain, after
merging lane B's commit — a build-time ordering decision already authorized
by intent.md's own Constraints section.

Research already done at the design stage (cited in `spec.md`): six
hermes-agent files read in full (`gateway/relay/{__init__,auth,transport,
ws_transport,command_manifest,media}.py`), plus `hermes_cli/gateway_enroll.py`
and `docs/relay-connector-contract.md`. Adopted: answer-by-request-id
dispatch, self-scheduling reconnect, bounded-drain teardown,
authenticate-the-channel-once. Declined: shared-secret HMAC auth,
jitter-less backoff, a `Transport` `Protocol` seam, a pending-request
futures table, a binary side-channel. `spec.md` § Rejected alternatives has
the full argument for each; this plan does not repeat it, only guards
against drifting back to one.

Two design decisions from `spec.md` resolve intent's open questions and
shape the file list below: `sadana.__version__` stays a plain source
literal (no `importlib.metadata`), and `ledger.py` gets **no** new
`threading.Condition` — `tether/client.py`'s outbound task polls both the
ledger tail and the ephemeral queue on one 250ms loop instead. Neither
touches `ledger.py` at all.

**Pre-flight findings**, from reading the current tree in plan mode, before
writing this plan:

- `door/operations.resume_on_start` is currently called from exactly one
  place: `subcommands/door.py:111` (`sadana door serve`) — not from
  `cmd_gateway_run`. Since the tether (and the upgrade lifecycle) lives
  under `sadana gateway run`, this plan adds a `resume_on_start(conns)`
  call to `cmd_gateway_run` too — a new call site in a file chain (a)/(b)
  may already touch, not an edit to `operations.py` itself.
- `tests/unit/test_door_nouns_harness.py::test_get_harness_shape` asserts
  `result["tether"] == "disconnected"` literally, against a fake `ctx` that
  never touches any tether module. Resolved by design: `get_harness()`
  reads tether state via a plain module-level function,
  `tether.client.state()`, never threaded through `DoorContext`.
  `tether/client.py` keeps its own module-level `TetherState` singleton
  behind a lock, mirroring `operations.py`'s own existing
  `_executor`/`_executor_lock` pattern — defaults to `"disconnected"` until
  `start()` is ever called, which no unit test does. This existing test
  needs no edit.
- `test_door_capabilities.py` and `test_door_nouns_harness.py`'s capability
  assertions are already subset/superset checks, not exact counts — adding
  `"upgrade"` to `DECLARED` in chain (c) breaks nothing here.
- No test references `plugin_install._is_valid_tag_syntax` by name
  (grepped) — only `_resolve_tag_commit`'s sibling call inside the module
  itself uses it. The rename to a public name is a pure rename with one
  internal call site to update.
- `door/nouns/harness.py`'s `upgrade` `ActionSpec` already exists as a
  placeholder (`capability="upgrade"`) from H19 — confirmed still there,
  unchanged since `spec.md` was written.

## Files that change

**Chain (a)/(b) — enrollment, keys, identity, frames, the connection loop,
events, reconnect. Touches only `pyproject.toml`, `src/sadana/tether/`,
`src/sadana/door/auth.py`, `src/sadana/subcommands/`, `scripts/test_relay.py`,
`scripts/prove_tether_e2e.py`, and their tests.**

- `pyproject.toml` — `dependencies` gains `"websockets>=13"`; a `[[tool.
  mypy.overrides]]` for `websockets.*` only if it turns out not to ship
  inline types (checked once installed).
- `src/sadana/tether/__init__.py` (new) — package marker. Whether it also
  exports a `CAPABILITIES` tuple is decided in chain (c), from what H24's
  `review.md` actually says — left empty here.
- `src/sadana/tether/frames.py` (new), `tests/unit/test_tether_frames.py`
  (new) — one frozen dataclass per wire.md §5 frame; `encode`/`decode`; an
  unrecognised `"type"` decodes to a sentinel, never raises;
  `Request`↔`DoorRequest`, `Response`↔`DoorResponse` (base64 binary bodies,
  a `SADANA_TETHER_MAX_RESPONSE_BYTES`-bounded refusal as a clean `Error`
  frame).
- `src/sadana/tether/backoff.py` (new), `tests/unit/test_tether_backoff.py`
  (new) — `next_delay`, `honour_retry_after`.
- `src/sadana/tether/keys.py` (new), `tests/unit/test_tether_keys.py` (new)
  — P-256 keypair generation, `O_CREAT|O_EXCL` save at `0600` refusing
  overwrite without `replace=True`, raw r‖s ES256 sign/verify round trip.
- `src/sadana/tether/identity.py` (new), `tests/unit/test_tether_identity.py`
  (new) — `Identity` dataclass, `load`/`save`/`remove` against `state_dir/
  tether/harness.toml`, round-tripped through a real tmp-dir file.
- `src/sadana/door/auth.py` — docstring update (drop "from config until H30
  persists enrollment"); `mint_token` gains an optional `iss: str | None
  = None` parameter (added to the claims dict when given). Both additive;
  `BoxIdentity`'s shape and every existing `mint_token` call site are
  unchanged. Needed by `scripts/test_relay.py`'s own `/token` endpoint,
  which plays the console's role and must mint a token naming itself as
  issuer, for `enroll.py`'s "verify against iss's JWKS" path to have
  something real to chase in the fixture.
- `src/sadana/enroll.py` (new), `src/sadana/subcommands/enroll.py` (new),
  `tests/unit/test_enroll.py` (new) — `enroll()` returns one of `Enrolled |
  TokenRefused | AlreadyEnrolled | NetworkFailed`, exercised against a
  monkeypatched `urllib`; the subcommand owns the parser and renders each
  outcome.
- `src/sadana/cli.py` (missed in the original file list, added here) —
  registers `build_enroll_parser` alongside every other subcommand; without
  this, `sadana enroll` exists as code but is unreachable from the CLI.
- `src/sadana/door/context.py` (new, missed in the original file list,
  added here) — `build(turn_runtime, *, box_identity, verifier, clock)
  -> DoorContext`: the noun registry, capability list and
  `resume_on_start` call that `subcommands/door.py`'s `cmd_door_serve`
  already assembles inline. Extracted so the tether (a second transport
  calling the same `handle()`, per `console_fit_plan.md` §5(e)) and the
  loopback listener share one construction rather than two noun
  dictionaries that could silently drift apart on what they serve.
  `subcommands/door.py` is edited a second time (beyond the identity-source
  swap already listed) to call this instead of building its own dict
  inline; behaviour is unchanged, only where the construction lives.
- `src/sadana/subcommands/door.py` — the one existing `BoxIdentity(harness_id
  =harness_id, org=org)` call site (line 104) now prefers `identity.load()`
  when an identity is enrolled, falling back to today's args/config
  construction when it is not (so `sadana door serve` keeps working
  unenrolled, for local dev and the conformance test).
- `src/sadana/tether/client.py` (new), `tests/unit/test_tether_client.py`
  (new) — the connection loop on its own daemon thread with its own asyncio
  loop, decomposed into independently testable pieces so the unit tier
  never touches a real socket: handshake (sign the challenge, require
  `welcome`); heartbeat send (every 15s); inbound dispatch (`request` →
  `loop.run_in_executor(door_pool, door.handle, ...)` → `response` by id);
  outbound drain (ledger tail from this connection's own `ledger_head`,
  never persisted across reconnects; ephemeral queue drained and
  *discarded* once on every transition into `connected`, then drained
  live); the reconnect wrapper (`429` → sleep exactly `Retry-After`, logged;
  else `backoff.next_delay`, logged; attempt resets after 60s connected);
  module-level `TetherState` singleton + lock (mirrors `operations.py`'s
  `_executor`/`_executor_lock`), read by `harness.get_harness()` via
  `tether.client.state()`.
- `src/sadana/subcommands/gateway.py` — `cmd_gateway_run` calls
  `resume_on_start(conns)` (new call site, parity with `door.py`'s existing
  one) and starts `tether.client.start(runtime)` as a `daemon=True` thread
  beside the scheduler, only when `tether.identity.load()` is not `None`.
  `gateway_daemon.py` is not edited. Also calls `logging.basicConfig
  (level=logging.INFO)` once, at the top — nothing in this project
  configures logging anywhere today, so every existing `logger`/`_logger`
  call (this project's own `router.py` included) is currently silently
  swallowed; a long-running foreground daemon process is exactly where
  that should stop being true, and `tether/client.py`'s own reconnect/
  backoff logging (below) needs it to be observable at all.
- `scripts/test_relay.py` (new, ~300 lines) — the fixture relay: `/enroll`,
  `/connect` (challenge/verify/welcome/hello/heartbeat tracking),
  `/forward/<harness_id>`, `/token`, `/.well-known/jwks.json`, `/events`,
  `--retry-after`, `--die-after`.
- `scripts/prove_tether_e2e.py` (new) — hand-run, not part of `make test`:
  starts the fixture relay, mints a token, enrolls, starts `gateway run`,
  asserts connected + hello capabilities, forwards `GET /v1/harness`/a
  list/a `POST`, asserts the event reaches `/events` with a cursor, kills
  and restarts the relay, asserts reconnection inside 60s with a logged
  jittered delay, forwards `GET /v1/changes?since=` after that and finds
  nothing missing, runs once with `--retry-after 7` and asserts exactly a
  7s sleep.

**Chain (c) — lifecycle, install script, documentation. Starts after
merging lane B's H24 commit into this worktree (or, if H24 has not merged
by then, proceeds anyway per intent.md's own constraint and records that in
`review.md`).**

- `src/sadana/door/operations.py` — one additive branch in
  `resume_on_start`: a `running` row whose `detail_json` has an
  `"upgrade_target"` key resolves to `succeeded`/`failed` by comparing
  `sadana.__version__`; every other row's existing behaviour (including the
  existing test) is unchanged.
- `tests/unit/test_door_operations.py` — new case: an upgrade-shaped row
  resolves `succeeded` when `__version__` matches, `failed` naming the
  actual version otherwise.
- `src/sadana/plugin_install.py` — `_is_valid_tag_syntax` → `is_valid_tag_
  syntax` (rename only; its one internal caller updated in the same edit).
- `src/sadana/stores.py` — `PURGE_STEPS: tuple[tuple[str, str], ...]`
  (`memory_entries`, `memory_rubric_overrides`, `schedules`,
  `persona_selections` — confirmed by grep against the current schema, not
  assumed) and `purge_account(conns, account_key)`.
- `tests/unit/test_stores_purge.py` (new) — scans `sqlite_master` for every
  table with an `account_key` column and fails, naming the first one absent
  from `PURGE_STEPS`.
- `src/sadana/door/nouns/harness.py` — `get_harness()`'s `"tether"` field
  reads `tether.client.state()` live, adds `connected_at`; `act()` gains
  `upgrade` (validates the tag via `plugin_install.is_valid_tag_syntax`,
  promotes an operation with `detail_json={"upgrade_target": tag}`,
  launches `scripts/upgrade.sh <tag>` detached), `deregister` (stops the
  tether, `identity.remove()`, deletes `key.pem`, ledger tombstone),
  `purge-account` (calls `stores.purge_account`).
- `tests/unit/test_door_nouns_harness.py` — new coverage for the three
  `act()` branches and the live tether field (existing
  `test_get_harness_shape` needs no edit — see Pre-flight findings).
- `scripts/upgrade.sh` (new) — `git fetch --tags`, `git checkout -- <tag>`
  (`--` before the console-supplied tag, per CLAUDE.md's own git-argument
  rule), `pip install -e . --quiet`, `systemctl restart sadana-gateway` via
  `gateway_service`'s existing `_run_systemctl`.
- `scripts/install.sh` (new), `docs/install.md` (new) — fresh-Ubuntu-to-
  connected, one section per step, the no-inbound-rule security sentence,
  reconnect behaviour, the lifecycle commands, the test relay as the local
  profile.
- `src/sadana/door/capabilities.py` **or** `src/sadana/tether/__init__.py`
  — read H24's `review.md` first: if it exported a per-module `CAPABILITIES`
  tuple pattern instead of editing the `DECLARED` literal directly, follow
  that pattern (`tether/__init__.py` exports `CAPABILITIES = ("upgrade",)`)
  and leave `capabilities.py` untouched; otherwise add `"upgrade"` to
  `DECLARED` directly. Either way, the first line of chain (c)'s own
  evidence is `capabilities.declared()` returning all sixteen `ALL` names
  except `settings.write`/`secrets.write`, pasted. If H24 has not landed by
  this point, add all four missing names (its three plus `upgrade`) here
  directly and say so in `review.md`, per intent.md's own instruction.
- `docs/console/nouns.md` — fill only the harness section; H24's five
  sections untouched.
- `docs/console/capabilities.md` — add the `upgrade` row; fill in "harness
  version it landed in" for every row still blank.
- `docs/console/wire.md` §5 — update the closing paragraph: no longer "H30
  is still the work item that puts a real socket behind this door," since
  it now has.

## Order of work

1. `pyproject.toml`: add `websockets>=13`; `pip install -e .`; confirm
   whether it needs a mypy override, add if so.
2. `tether/frames.py` + tests.
3. `tether/backoff.py` + tests.
4. `tether/keys.py` + tests.
5. `tether/identity.py` + tests.
6. `door/auth.py` docstring update (no behaviour change).
7. `enroll.py` + `subcommands/enroll.py` + tests.
8. `subcommands/door.py`: swap the `BoxIdentity` call site to prefer
   `identity.load()`, falling back to today's args/config.
9. `tether/client.py` + tests — the riskiest step, landing after every
   simpler piece it depends on is already built and independently proven,
   and internally split into fake-driven, socket-free testable units.
10. `subcommands/gateway.py`: add the `resume_on_start(conns)` call and
    start the tether thread when an identity exists.
11. `scripts/test_relay.py`.
12. `scripts/prove_tether_e2e.py`, run by hand — chain (a)/(b)'s own
    capstone proof, against a real (local) socket.
13. Merge lane B's H24 commit into this worktree. If it has not landed,
    proceed to the next step anyway and record that in `review.md`.
14. `door/capabilities.py` or `tether/__init__.py` (whichever pattern H24
    actually used, or both new names directly if H24 hasn't landed) — paste
    `declared()`'s output as chain (c)'s first evidence.
15. `door/operations.py`'s additive branch + its new test.
16. `plugin_install.py`'s rename.
17. `stores.py`'s `PURGE_STEPS`/`purge_account` + the completeness test.
18. `door/nouns/harness.py`'s live tether field and three `act()` branches
    + tests.
19. `scripts/upgrade.sh`.
20. `scripts/install.sh` + `docs/install.md`.
21. `docs/console/nouns.md` (harness section only).
22. `docs/console/capabilities.md` (upgrade row + version-landed column).
23. `docs/console/wire.md` §5 status update.
24. Self-check: `/ponytail-review` + `/simplify` against the whole diff;
    triage findings (take now / later work item / already settled by
    spec.md) per build-skill's own rule.
25. `make verify`; paste the output.

## Risks

**What could this break?**
`subcommands/door.py`'s `BoxIdentity` call site is mitigated by falling
back to the existing args/config path when unenrolled — every existing
test that constructs `BoxIdentity` directly (ten of them, across contract
and unit tests) never goes through this call site at all. `door/
operations.resume_on_start`'s new branch is additive and keyed on a
`detail_json` field ("upgrade_target") nothing else ever sets, so
`test_resume_on_start_fails_every_row_still_running` (which sets no such
key) keeps passing unchanged; the new `subcommands/gateway.py` call site is
net-new, not a change to an existing one. `door/nouns/harness.py`'s
`get_harness()`: `test_get_harness_shape` literally asserts
`"disconnected"`, resolved by design — `tether.client.state()` is a
module-level default nothing in the unit suite ever mutates, so this test
needs no edit. `capabilities.DECLARED` gaining `"upgrade"`: existing tests
are subset/superset checks, confirmed by reading both capability test
files. `plugin_install.py`'s rename: no test references the private name,
confirmed by grep.

**Riskiest step, and why it's ordered last in chain (a)/(b).**
`tether/client.py` (step 9) — real threading, asyncio, a WebSocket, and a
reconnect state machine, the one piece this item's own contract admits
can't be fully proven without a live socket. It lands after every simpler
dependency (frames, backoff, keys, identity, enroll, the `BoxIdentity`
swap) already exists and is independently tested, is itself decomposed
into small functions each drivable by a fake sender/fake socket in the
unit tier, and its remaining, genuinely-needs-a-real-socket behaviour is
caught by the fixture relay and the hand-run prove script (steps 11-12) —
landing last, not first, so the risky part runs against something already
proven rather than against nothing.

**Rejected-alternative drift check** (against `spec.md` § Rejected
alternatives): no `Transport` `Protocol` is introduced anywhere in this
plan — `client.py` calls `websockets.connect` directly. No pending-request
futures table is added — the box only ever answers a `request`, never
originates one needing a reply. No `threading.Condition` touches
`ledger.py` — the outbound task polls only. `sadana.__version__` stays a
literal — nothing in this plan reads `importlib.metadata`. None of this
plan drifts back toward a declined alternative.

**Chain-timing risk.** Chain (c)'s start depends on lane B's H24 merging.
This is not re-decided here: intent.md's own Constraints section and
spec.md's own Concerns section both already say proceed and record it if
H24 hasn't landed by then — steps 13/14 above just execute that, they
don't re-argue it.

## Proof

`tests/unit/test_tether_frames.py`, `test_tether_backoff.py`,
`test_tether_keys.py`, `test_tether_identity.py`, `test_tether_client.py`,
`test_enroll.py` each passing, covering exactly `spec.md`'s Acceptance
criteria (codec round trip + unknown-type error, backoff bounds, raw r‖s
sign/verify, hello's capabilities/ledger_head freshness, ledger-tail
order/no-skip under a fake sender, ephemeral discard-on-reconnect,
enroll's four outcomes against a monkeypatched `urllib`, identity file
round trip). `tests/unit/test_door_operations.py`'s new upgrade-branch case
(both succeeded and failed-naming-the-actual-version outcomes).
`tests/unit/test_stores_purge.py`'s `sqlite_master`-scanning completeness
test. `tests/unit/test_door_nouns_harness.py`'s new coverage for the three
`act()` branches and the live tether field, with existing tests in this
file unmodified and still green. Chain (c)'s own first line of evidence,
pasted: `capabilities.declared()` returns all sixteen `ALL` names except
`settings.write`/`secrets.write`. `make verify` ending `VERIFY OK`.
`scripts/prove_tether_e2e.py`'s full hand-run output, pasted — the real
external round trip this item's own contract requires outside `make test`,
per `CLAUDE.md`'s existing rule.
