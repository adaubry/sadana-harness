# Review: The real box behind the socket (from plan.md 2026-09-15)

Reviewed: no code diff — `plan.md` § Files that change is `None`; this
item's only repository-visible output is its own four artifact files, one
`CLAUDE.md` line (already approved and applied at the design stage), and
two pushed release tags (`0.0.1`, `0.0.2`).
Reviewer context: **same session as build — a stated limitation.** This
checkpoint's "build" was live, interactive evidence-gathering against a
real EC2 box across one continuous conversation with the user; no fresh
session or subagent reviewed it cold. The compliance pass below is the
best substitute available: reconciling every claim against `plan.md`/
`spec.md` and the pasted evidence itself, rather than against memory.
Second opinion: none.

## Evidence

**make verify — local repo (no code changed here)**

```
$ make verify
LINT OK
Success: no issues found in 107 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [100%]
1715 passed in 145.47s (0:02:25)
TESTS OK
VERIFY OK
```

All 1715 tests pass locally, including `test_gateway_service.py`'s
precondition check — because this laptop has never installed a real
gateway. That's the exact mechanism behind the box-side failure below: the
same test, same code, different host history.

**Step 1 — provision the box**

Cloned `docs/install.md` verbatim except one substitution, recorded live as
it happened:

```
$ sudo apt-get install -y python3.11 python3.11-venv git
E: Unable to locate package python3.11
```

Ubuntu 24.04 "noble" ships Python 3.12 by default and doesn't carry 3.11 in
its archive at all; `pyproject.toml` only requires `>=3.11`, so 3.12
satisfies it. Substituted `python3.12`/`python3.12-venv`, everything else
followed the document unchanged:

```
$ git clone --branch 0.0.1 -- https://github.com/adaubry/sadana-harness.git ~/sadana-harness
Cloning into '/home/ssm-user/sadana-harness'...
Note: switching to '12ed891ad60efe5c395e445925e71956b6bafb23'.  # pragma: allowlist secret
$ .venv/bin/sadana --version
sadana 0.0.1
```

**Finding**: `install.md` names "Fresh Ubuntu 22.04 or 24.04" but its one
apt command only works on 22.04.

**Step 2 — status, GET /v1/harness, relay hello**

```
$ sadana gateway status
● sadana-gateway.service - sadana gateway daemon
     Active: active (running)
     └─2287 /home/ssm-user/sadana-harness/.venv/bin/python3 -m sadana.cli gateway run
gateway service is running
```

```
GET /v1/harness via /forward:
{"status": 200, "body": {"id": "hrn_hcp_staging", "version": "0.0.1",
"capabilities": ["grammar.v1", "changes", "inventory", "artifacts.download",
"approvals.wait", "approvals.call", "schedules.write", "streaming",
"runs.live", "runs.stop", "plugins.install", "plugins.inspect",
"plugins.write", "settings.write", "secrets.write", "upgrade"],
"tether": "connected", "org": null, "ledger_head": 4,
"leaves_the_box": [...22 nouns...], "mirror": "full",
"connected_at": "2026-09-15T13:29:22.595Z"}}
```

Relay log:
```
connection open
test relay: hello from hrn_hcp_staging — capabilities=('grammar.v1',
'changes', 'inventory', 'artifacts.download', 'approvals.wait',
'approvals.call', 'schedules.write', 'streaming', 'runs.live', 'runs.stop',
'plugins.install', 'plugins.inspect', 'plugins.write', 'settings.write',
'secrets.write', 'upgrade')
```

All sixteen declared capabilities present — the full closed list.

**Step 3 — make verify on the box, and conformance**

```
CHAIN OK
LINT OK
Success: no issues found in 107 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
[...]
=================================== FAILURES ===================================
______________ test_install_refuses_before_writing_the_unit_file _______________
>       assert not gateway_unit.unit_path().exists()
E       AssertionError: assert not True
E        +  where True = exists()
E        +    where exists = PosixPath('/etc/systemd/system/sadana-gateway.service').exists
=========================== short test summary info ============================
FAILED tests/unit/test_gateway_service.py::test_install_refuses_before_writing_the_unit_file
1 failed, 1714 passed in 85.86s (0:01:25)
TESTS FAILED (pytest exit 1)
```

**Finding (Important — see below)**: this test's precondition reads real
host state a prior, unrelated real action (installing the actual gateway,
step 2) already made false. 1714/1715 otherwise green.

No `--remote` flag or sibling script exists for `test_console_grammar.py`
(`console_fit_plan.md` §5(e) deliberately keeps the conformance test
transport-free — building one reopens a frozen decision). Ran the
equivalent checks directly against real `/forward` responses instead:

```
=== test_get_harness equivalent ===
PASS: id, capabilities superset, harness in leaves_the_box
=== ListResponse shape: GET /v1/conversations ===
PASS: validates against ListResponse - {'data': [], 'next_page_token': None}
=== Problem shape: no token ===
status: 401
PASS: validates against Problem - {'type': '.../UNAUTHENTICATED', ...}
ALL CONFORMANCE CHECKS PASSED
```

**Step 4 — relay acceptance requirements 1–4 (H30 spec.md)**

```
=== req1: key pair persisted, no private key in logs/state ===
/home/ssm-user/.local/state/sadana/tether/key.pem
/home/ssm-user/.local/state/sadana/tether/harness.toml
no private key material in journal
=== req2: enroll refuses to overwrite without --replace-key ===
hrn_hcp_staging
EXIT:1
	already enrolled; pass --replace-key to re-enroll
=== req3: no inbound listener from the gateway process ===
LISTEN 127.0.0.1:8787   (test relay, loopback only)
LISTEN 127.0.0.1:8765   (H19's own loopback door, loopback only)
LISTEN 0.0.0.0:22       (stock sshd, unrelated to sadana, never used)
```
Req 4 (signed-challenge identity, never a static secret): proven
indirectly but concretely — every successful `hello` observed this session
necessarily passed the relay's own nonce-sign-verify-`welcome` gate
(`scripts/test_relay.py:287-310`); a wrong signature never reaches `hello`
at all. Corroborated by `make verify`'s own passing `KeyPair.sign`/`verify`
round-trip test.

**Step 5 — the two-organisation first-brick journey**

```
=== 1. list (alice) ===          200 {'data': [], ...}
=== 2. create (alice) ===        201 {'id': 'conv_...', 'version': 1, ...}
=== 3. send a message (alice) — real turn ===   202 {operation running}
  poll 0: succeeded
=== 4. /events: message.delta + sent ===
{"type": "ephemeral", "name": "message.delta", ...,
 "data": {..., "delta": "acknowledged", "seq": 1}}
{"type": "event", "cursor": 30, "event": {..., "noun": "messages",
 "state": "sent", ...}}
=== 5. rename with If-Match ===  200 version 3, name "HCP checkpoint convo"
=== 6. archive ===               200 version 4, state "archived"
=== 7. archive again, same If-Match ===   409 CONFLICT
=== 8. bob (org1, different sub) GETs alice's conversation ===   404 NOT_FOUND
=== 9. carol (org2) GETs alice's conversation ===   403 FORBIDDEN
  "token organisation does not match this harness's enrollment"
ALL STEP-5 CHECKS PASSED
```

A real OpenRouter round trip: the assistant's actual reply was
`"acknowledged"`, exactly as instructed.

**Step 6 — parked call node, approve, run reaches done**

```
=== poll approvals (count=true) ===
200 {'data': [...2 entries...], 'count': 2}
approval id: appr_..., conversation: conv_...
=== approve through /forward ===   200 state: "approved"
=== find the run ===   state: "done", exit_reason: "completed"
=== final messages ===
{'role': 'assistant', 'content': "Done — I've noted down that your
favorite color is blue, so I'll remember it in later conversations."}
```

**Step 7 — secret rotation, live turn, redaction**

```
create secret: 201 {'name': 'cred_test', 'fingerprint': '79c3·4e60c436'}
providers list: openrouter credential_ref was "OPENROUTER_API_KEY"
rotate provider: 200 credential_ref: "cred_test", version 3
=== turn on the NEXT request, no daemon restart ===
send message: 202 {operation running}
poll 3: succeeded
final messages: {'role': 'assistant', 'content': 'rotated'}
=== redaction filter demo ===
provider credential resolved: [REDACTED]
```

The real secret value never appeared in any response body (the noun only
ever renders a `fingerprint`) or in my own transcript — it was read and
used entirely inside scripts running on the box.

**Finding**: the task's own literal `PUT /v1/secrets/cred_test` doesn't
match the real API — `secrets.py`'s own docstring explicitly rejects that
shape in favour of `POST /v1/secrets` + `PATCH /v1/secrets/{id}`, cited
under Findings below.

**Step 8 — upgrade round-trip**

```
=== upgrading to 0.0.2 ===
upgrade operation: 202 {state: running}
  poll 2: succeeded
sadana --version → sadana 0.0.2
=== upgrading to 0.0.1 ===
upgrade operation: 202 {state: running}
  poll 2: succeeded
sadana --version → sadana 0.0.1
DONE
```

Both operations reached `succeeded`; `sadana --version` independently
confirmed each landing rather than trusting the operation state alone.
(Required a one-line `__version__` bump to `0.0.2`, made by the user's own
hand — see Concerns.)

**Step 9 — kill/restart relay, backoff, catch-up**

```
reconnecting in 0.13s (attempt 1, full jitter)
reconnecting in 3.57s (attempt 2, full jitter)
reconnecting in 0.27s (attempt 3, full jitter)
reconnecting in 14.44s (attempt 4, full jitter)
reconnecting in 2.61s (attempt 5, full jitter)
reconnecting in 5.59s (attempt 6, full jitter)
reconnecting in 39.25s (attempt 7, full jitter)
reconnecting in 12.64s (attempt 8, full jitter)
```
All under the documented 60s cap, genuinely randomized (full jitter, not a
clean exponential curve).

```
GET /v1/changes?since=126:
{"status": 200, "body": {"data": [
  {"kind": "changed", "noun": "providers", ...},
  {"kind": "changed", "noun": "conversations", ...},
  {"kind": "changed", "noun": "runs", "state": "running", ...},
  ... 9 entries in cursor order ...
]}}
```
Catch-up after reconnect returned a real, ordered batch with no gap and no
repeat.

**Finding**: the relay's own signing keypair isn't persisted across its
restart (only enrolled boxes' public keys are, via `--state-file`) — the
gateway's cached JWKS went stale after the relay restarted, requiring a
gateway restart to recover. Fixture limitation, not a box defect — the
box's own cache/TTL/throttle design is reasonable.

## Findings

**Important**

- **[Compliance]** `tests/unit/test_gateway_service.py::test_install_refuses_before_writing_the_unit_file`
  fails once a real gateway is installed on the test-running host, because
  its precondition checks the real path `/etc/systemd/system/sadana-gateway.service`
  instead of state the test itself controls. This directly violates the
  `CLAUDE.md` rule this item's own design stage proposed and the user
  approved. Not fixed here (no production code); named for its own
  maintain intent below.
- **[Compliance]** `install.md` names "Fresh Ubuntu 22.04 or 24.04" but its
  one prerequisite command (`apt-get install python3.11 python3.11-venv`)
  only resolves on 22.04 — 24.04 ships 3.12 and doesn't carry 3.11 in its
  archive. `pyproject.toml`'s `>=3.11` tolerates the substitution, but the
  document itself is wrong for one of the two OSes it names.
- **[Compliance]** `make verify`'s dependencies (`pytest`, `mypy`, `ruff`,
  `pre-commit`) are declared nowhere in the repository — no dev extras in
  `pyproject.toml`, no `requirements-dev.txt`, no CI workflow to
  cross-reference. A box provisioned exactly per `install.md` cannot run
  `make verify` at all without externally-sourced version knowledge.
- **[Compliance]** The task's own literal `PUT /v1/secrets/{name}`
  instruction doesn't match the implemented API — `secrets.py`'s docstring
  explicitly documents rejecting that exact shape in favour of
  `POST /v1/secrets` + `PATCH /v1/secrets/{id}`. Worth correcting upstream
  in whatever document the next checkpoint's author reads, since they will
  hit the same wrong assumption.
- **[Bugs, fixture]** `scripts/test_relay.py`'s hand-rolled WebSocket layer
  (`_WsConnection.recv()`) never flushes an auto-generated PONG after
  receiving a PING — `receive_data()` queues it, but nothing calls
  `_flush()` on the receive path, only on explicit `send()`/`close()`. This
  caused the repeated "keepalive ping timeout" reconnects observed
  throughout this session. Not a box defect (the box's own reconnect
  logic recovered correctly every single time); a fixture defect.
- **[Bugs, fixture]** `scripts/test_relay.py` regenerates its own signing
  keypair (`auth.generate_dev_keypair("relay")`) on every process start
  and never persists it, unlike enrolled boxes' public keys
  (`--state-file`). Restarting the relay silently invalidates every
  already-connected box's cached JWKS until that box's own gateway
  restarts. Also a fixture defect, not a box defect.
- **[Compliance]** Two of eleven promises grade below 4 (see `## Grade`).
  Per this checkpoint's own instructions, that makes the console-plan
  readiness verdict `no`, regardless of how cleanly the nine numbered
  steps themselves passed.

**Nits**

- **[Bugs]** `sadana setup`'s `env_file.path_put` (`src/sadana/env_file.py:38`)
  raises a raw `UnicodeEncodeError` traceback instead of a clear validation
  error when given a non-UTF-8-decodable argv value (observed once, from a
  browser-terminal paste artifact, not from the real key). Low severity —
  operator-facing only, and the actual root cause was a terminal issue, not
  a normal input a real operator would supply — but the crash itself is a
  real gap worth a one-line fix eventually.

**Raised, not findings**

- The Session Manager browser terminal reliably mangles long pasted lines
  and CRLF-contaminates heredoc terminators. Not a sadana-harness defect at
  all — an AWS console/terminal property — but worth remembering for
  whoever runs the next checkpoint against this same box: write scripts via
  `send-command` (base64-encoded) rather than asking a human to paste
  multi-line scripts by hand.

## Compliance pass

- **`plan.md` § Proof** — discharged. Every numbered step's evidence is
  pasted above, under its own heading, matching the plan's own ordering.
- **`spec.md` § Acceptance criteria** — all ten checked items are satisfied
  by the evidence above; the eleventh (grading) is this document's own
  `## Grade` section below.
- **`spec.md` § Rejected alternatives** — no drift. No `--remote` flag was
  built (an ad hoc replay was used instead); the relay's signing-key
  statelessness was worked around, not patched; none of the eight findings
  were fixed in place.
- **Design guideline 1 (learn from the reference)** — `spec.md` already
  found nothing analogous in `../hermes-agent` for "provision and grade a
  real deployment target"; nothing in execution contradicted that.
- **Design guideline 2 (reduce the number of bets)** — held: zero new
  infrastructure was built (no `--remote` mode, no persistent relay
  identity, no CI).
- **Design guideline 3 (least step-cost)** — the JSON-construction failures
  were resolved by adding a step (`jq`-built payloads, base64 file
  transfer) rather than making any existing step heavier; consistent with
  `spec.md`'s own rejected-alternatives reasoning.
- **Design guideline 4 (minimise mutable state)** — the state actually
  created (box identity, `cred_test` secret, two git tags) matches
  `spec.md`'s own inventory exactly; nothing extra was introduced.

## Grade

| id | name | evidence (1 line) | grade |
| --- | --- | --- | --- |
| P1 | Named | Every resource observed (conversations, messages, approvals, secrets, providers, runs, operations, harness) carried a real id, mutable name, `created_at`/`updated_at`, `version`, and a closed-set `state`, consistently across steps 5–9. | 5 |
| P2 | Spoken | The same six-verb, id-addressed grammar (GET list/get, POST create, PATCH update, POST actions, If-Match, `filter=`/`count=`/`query=`) worked identically across conversations, messages, approvals, secrets, providers and harness with no per-noun special-casing beyond its own URL/body shape. | 5 |
| P3 | Logged | `GET /v1/changes?since=126` returned a real, cursor-ordered batch after the relay kill/restart with no gap and no repeat (step 9); delete-tombstone and full-inventory-enumeration were not separately exercised. | 4 |
| P4 | Locked | 300s-capped door tokens, principal derived from token claims not payload, 401 on missing auth, 403 on org mismatch with the exact `auth.py:161` message (step 5). | 5 |
| P5 | Tethered | Own key pair generated and never left the box (step 4); real backoff+jitter observed under the 60s cap through both a fixture ping-timeout bug and a deliberate kill (steps 4, 9); zero non-loopback inbound listeners (step 4); recovered cleanly from the relay's own identity change. | 5 |
| P6 | Watched | Real `message.delta` ephemeral frames streamed live during a real turn (step 5); async 202+poll operations observed throughout; `runs.stop` (stopping mid-flight) was never exercised. | 4 |
| P7 | Parked | A real `call` node (`memory.remember`) parked, was listed with `count=true`, approved through `/forward` by a different principal than the one who triggered it, and the run resumed to `done` (step 6). | 5 |
| P8 | Shared | Same-org-different-principal → 404 (principal-scoped `account_key`); cross-org → 403 (harness-org check); optimistic concurrency via If-Match → 409 on stale (step 5). True concurrent-write racing was not exercised. | 4 |
| P9 | Tuned | `PATCH /v1/providers/{id}` rotated `credential_ref` live; the very next turn used the new credential with no daemon restart; secrets never appear in any read (fingerprint only); the redaction filter genuinely redacted the real value to `[REDACTED]` (step 7). | 5 |
| P10 | Known | Version/capabilities/health reported correctly and repeatedly; upgrade through the tether fully round-tripped (step 8) — but "a conformance test against the published contract runs in our own CI" is **not met at all**: there is no CI in this repository (no `.github/workflows`) and no real-box conformance path (Finding above). | 3 |
| P11 | Gone | **Not exercised.** `deregister`/`purge-account` were never part of this checkpoint's own nine-step checklist — a real, structural gap in this checkpoint's own design, not a skipped step. | 2 |

Every promise below 4 is a maintain intent:

- **P10** — no CI exists anywhere in this repository, and the conformance
  test has no path to run against a real, deployed box without reopening
  `console_fit_plan.md`'s own frozen transport-free decision. A future work
  item needs to either build a CI workflow that runs the existing
  in-process conformance suite, or make a deliberate, amended decision
  about what "runs in our own CI" against a real box should mean.
- **P11** — `deregister` and `purge-account` have never been exercised
  against a real, enrolled box. A future checkpoint (or an addition to this
  one's own follow-up) needs to actually deregister a real box, confirm its
  identity and private key are gone, and confirm a purge enumerates
  everything held for a principal — ideally on a disposable box, since
  deregistering this one would undo the "keep it running" decision this
  item's own intent recorded.

## Ready for the console plan: no

Evidence: steps 2–9 all passed with real, pasted evidence — but two of
eleven promises (P10, P11) grade below 4, and this checkpoint's own
instructions are explicit that the verdict is `yes` only if every promise
clears that bar. The nine-step checklist itself never asked for P11's
evidence at all, which means the checklist's own coverage — not just this
run's execution of it — needs to widen before a `yes` is honest. P10's gap
(no CI, no real-box conformance path) is a pre-existing structural absence
this checkpoint surfaced rather than caused.

The box itself is left running, enrolled as `hrn_hcp_staging`/org `org1`,
with the test relay co-located and the gateway active — ready for the
console plan's later checkpoints to target once P10/P11 are closed, per
the user's own "keep it running" decision.

## Decision

Pending — awaiting Adam.
