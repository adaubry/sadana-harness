# Spec: The real box behind the socket

Intent: docs/tasks/HCP-real-box-behind-socket/intent.md

Author: Claude, drafted for Adam Aubry (product owner) to review. Status: approved.

## Requirements

1. The box is provisioned from a fresh Ubuntu 22.04/24.04 EC2 image using
   only `docs/install.md`'s own steps; any manual step the document does not
   name is recorded as a finding, never silently patched around (intent
   Constraints).
2. Every one of the eleven promises (`docs/reference/console_fit_plan.md`
   §4) is exercised against the real box with pasted, real evidence — not
   asserted, not simulated (intent Proposed outcome).
3. Each promise is graded 1–5 with one line of evidence; any promise below 4
   becomes a named follow-up rather than a silent gap (intent Proposed
   outcome).
4. `review.md` states plainly whether the console plan's next phase can
   start, and that verdict is `yes` only if every checklist step passed and
   no promise graded below 4 (intent Proposed outcome).
5. Nothing in `src/`, `scripts/`, or `tests/` changes as part of this item;
   a promise found short is recorded and deferred, never patched in place
   (intent Constraints).
6. The box's private key never leaves the box — not in a log, an error, a
   command I run, or this conversation (intent Constraints).
7. No inbound rule reaches the box for the tether itself, under any
   administration method chosen (intent Constraints).

## Design

**Topology.** One EC2 instance runs both the installed gateway
(`docs/install.md` §1–7) and `scripts/test_relay.py`, loopback-bound at
`127.0.0.1:8787` — settled during planning, matching the install document's
own local-dev section rather than standing up a second reachable host.

**Access.** AWS Systems Manager Session Manager only — no security-group
inbound rule, no SSH key pair. Two execution paths, split by what they
touch:

- **Non-secret actions** (prereqs, clone, venv, enroll, `/forward` traffic,
  systemd install/start/restart, relay kill/restart, tag creation) run
  through `aws ssm send-command`, driven directly by me. This is the
  overwhelming majority of the checkpoint's work.
- **Secret-bearing actions** (`sadana setup --openrouter-key`, creating the
  `cred_test` secret from the box's own `.env`) run in the user's own
  interactive Session Manager browser shell. Claude Code's own auto-mode
  classifier independently enforces this split — it refused two of my own
  `send-command` attempts (installing the systemd service/sudoers rule,
  labeled "Unauthorized Persistence"; reading and forwarding the real
  OpenRouter key end-to-end in one script, labeled "Credential
  Materialization") — so this design tracks a real, external constraint,
  not a self-imposed one.

**Request construction.** Every `/forward` call is a JSON envelope
(`method`/`path`/`query`/`headers`/`body`) POSTed to the relay's
`/forward/<harness_id>`. Early attempts hand-built these with shell string
interpolation and regex-based JSON field extraction; both proved fragile
(a double-JSON-encoding bug from treating `body` as a pre-serialized string
instead of a parsed value, and a regex that couldn't scope a field to one
object among several). The design settled on `jq -n --arg ... '{...}'` to
build request bodies and `jq -r` to parse responses, once `jq` was
confirmed already present on the AMI.

**File transfer to the box.** Scripts longer than a few lines are written
to the box as complete files via `aws ssm send-command` with the content
base64-encoded inline (`echo <b64> | base64 -d > file`), never composed by
having a human paste a long multi-line script into the browser-based
Session Manager terminal — that terminal was observed corrupting long
single lines and CRLF-contaminating heredoc terminators, both empirically
confirmed during the secret-rotation step.

**Detached processes under `send-command`.** `setsid nohup <cmd> >log 2>&1 </dev/null & disown` started the relay correctly (the process survives and detaches), but the *`send-command` invocation itself* hung until cancelled — an SSM Run Command quirk, not a process bug. Confirmed by checking the process was alive and correct after cancelling the hung command.

## Interface

- **Inbound to the box**: none. Every action is either the box dialing out
  (tether, `git`, `pip`, `apt`) or an SSM Session (agent-initiated
  long-poll, not a listener).
- **Between me and the box**: the AWS SSM `SendCommand`/`GetCommandInvocation`
  API, via the `sadana-hc` CLI profile (scoped to SSM actions only — no
  EC2/IAM permissions).
- **Between the box and "the console"**: `scripts/test_relay.py`'s
  `/enroll`, `/connect` (WS), `/token`, `/forward`, `/events`,
  `/.well-known/jwks.json`, `/status/<id>` — the same surface H30 already
  specified and `prove_tether_e2e.py` already exercises, just reached over
  a real loopback socket on real hardware instead of an in-process fixture.
- **Errors**: every door-facing failure is the project's own `Problem`
  shape (`type`/`title`/`status`/`code`/`detail`); every SSM-facing failure
  is either a non-zero command exit (`StandardErrorContent` carries the
  traceback) or, twice, a Claude Code auto-mode classifier refusal (not an
  SSM error at all — a harness-level policy block, surfaced before the
  command ever reached AWS).

## Acceptance criteria

- [x] Box provisioned per `install.md`; every deviation recorded as a
      finding (python3.12 substituted for 3.11 on Ubuntu 24.04; dev tooling
      for `make verify` undeclared anywhere in the repo).
- [x] `sadana gateway status`, `GET /v1/harness` via `/forward`, and the
      relay's hello log all pasted, capabilities the full closed sixteen.
- [x] `make verify` run on the box; tail pasted (chain/lint/types green,
      one test failure found and explained, not hidden).
- [x] A conformance check run against the box through `/forward` — not the
      literal `--remote` flag the task named (none exists; building one
      would reopen `console_fit_plan.md`'s own frozen decision (e)), but an
      equivalent ad hoc schema/assertion replay of `test_console_grammar.py`'s
      own checks against real `/forward` responses.
- [x] The console's relay acceptance requirements 1–4 (H30 spec.md
      Requirements 1–4) verified against this machine individually.
- [x] The two-organisation first-brick journey: list → create → message →
      `/events` (`message.delta` + `sent`) → rename → archive → 409 →
      cross-principal 404 → cross-org 403.
- [x] A real `call` node parked, listed with `count=true`, approved through
      `/forward`, run watched to `done`.
- [x] A secret created and a provider's `credential_ref` rotated through
      the door; the next turn used it with no daemon restart; the
      redaction filter demonstrated against the real value.
- [x] Upgrade round-trip (`0.0.1 → 0.0.2 → 0.0.1`) through `/forward`, both
      operations reaching `succeeded`, `sadana --version` confirming each
      landing.
- [x] Relay killed and restarted; backoff delays logged (0.13s–48.28s, full
      jitter, capped under 60s); `changes?since=` catch-up confirmed
      against a real cursor after reconnect.
- [ ] All eleven promises graded with the console plan's own yes/no
      criterion honestly applied — this is `review.md`'s own job, not
      `spec.md`'s; the checklist above only proves the *inputs* to that
      grade exist.

## Non-goals

- Fixing any of the eight findings this checkpoint surfaced — each is
  recorded for its own maintain-intent, never patched here (intent
  Constraints, requirement 5).
- Building `test_console_grammar.py --remote` or a real console relay —
  both declined in `console_fit_plan.md` already; reopening either is a
  different work item that must amend that document first.
- Testing `deregister`/`purge-account` against this box — genuinely absent
  from the task's own nine-step checklist, not merely skipped; named
  explicitly in `review.md`'s grade for P11 rather than left implicit.

## Rejected alternatives

**1. Learn from the reference.** Nothing in `../hermes-agent`'s corpus is
analogous — provisioning and grading a real deployment target is an
operational procedure, not a reusable code block, and the hermes index
(`docs/reference/hermes_core_blocks_kind.csv`) has no block for "deploy and
grade a real agent host." This guideline does not apply in its usual sense;
recorded rather than silently skipped.

**2. Reduce the number of bets.** The design deliberately built nothing
new: no `--remote` conformance flag, no persistent relay identity, no CI.
Every one of those would be a bet this checkpoint has no standing to take —
each belongs to whichever work item actually owns that decision. The
zero-new-infrastructure posture is the bet-reduction move here, not a
missed opportunity.

**3. Catch the scenario at the least step-cost.** The JSON-construction
problem (browser terminal mangling long single lines) had three real
options: add a step (heredoc/file-based bodies, or `jq`-built payloads,
chosen), make a step heavier (one monolithic script handling everything
end-to-end — tried first, and independently rejected by the platform's own
credential-materialization guard when I ran it, not just by my own
judgment), or make a step harder (ask the user to paste more carefully —
rejected, since the corruption was empirically a terminal/paste property,
not an attention property, and retrying without diagnosing it caused the
`create secret` / `rotate provider` blank-response failures to recur
identically on a second attempt).

**4. Minimise mutable state.** State this item actually created, and why
each is real rather than derivable: the box's own tether identity/keypair
(intrinsic to what's being tested); the relay's in-memory box registry and
event log (ephemeral fixture state, `--state-file` persists only enough of
it — enrolled boxes' public keys — to survive the relay's own restart,
which is exactly what step 9 needed and what Finding (the relay's *own*
signing key is not persisted) exposed); the `cred_test` secret and the
`openrouter` provider's `credential_ref` (real state changes on the box,
because that is literally what P9 asks to be proven); two git tags,
`0.0.1` and `0.0.2` (needed once, durable by design — a tag is not meant to
be derived or deleted). Nothing here is state this item invented for its
own convenience.

## Open questions

- Whether the eight findings become one maintain-intent or several —
  `review.md`'s own job to name, not this spec's to pre-decide.

## Concerns

- **The task's own literal wire shape didn't match reality.**
  `PUT /v1/secrets/{name}` (what the checklist said) was explicitly
  rejected by this project's own implementation in favour of
  `POST /v1/secrets` + `PATCH /v1/secrets/{id}` (`secrets.py`'s own
  docstring names the rejection and why). Resolved by using the real API
  and recording the mismatch as a finding rather than treating it as a
  blocker — but a reviewer should weigh whether the *task description
  itself* needs correcting upstream, since the next checkpoint's author
  will hit the same wrong assumption.
- **Step 8 needed a production-code change** (`__version__` bump) that this
  item's own Constraints forbid. Genuinely irreconcilable as written — no
  design choice avoids it, since the upgrade action's success check is
  defined as tag-name-equals-`__version__`. Resolved with the user's
  explicit, narrow approval and the user's own hand making the edit (never
  through my `Edit`/`Write` tools, which the artifact-chain hook correctly
  refused). This is the single largest tension in the whole item — flagging
  it prominently rather than letting the resolution read as routine.
- **Two of eleven promises will grade below 4.** This is a finding about
  this *checkpoint's own design* (P11's deregister/purge path was never in
  the nine-step checklist; P10's CI/conformance-against-a-real-box path
  doesn't exist anywhere in the repo), not a flaw in execution against that
  checklist. A reviewer should not read "8/9 checklist steps passed
  cleanly" as implying the promise grade will follow — it doesn't, and
  `review.md` says so plainly.

## Proposed CLAUDE.md amendment

One finding generalizes past this work item and is worth naming to the
user directly: `tests/unit/test_gateway_service.py` asserts a real,
global filesystem path (`/etc/systemd/system/sadana-gateway.service`) does
not exist as a *precondition* — true on a laptop or CI, false the moment
any real gateway has ever been installed on the host running the suite,
which is exactly what this checkpoint did. That's a test correctness bug
with a shape broader than this one file: a precondition check that reads
real, shared host state rather than state the test itself controls.

Proposed line for `CLAUDE.md`'s "Please do" section:

> A test's precondition check reads state the test itself created, never
> real global host state (a systemd unit path, a real port, a real file
> outside the test's own tmp dir) — a prior, unrelated real action can
> make that precondition false on a host nothing else about the test
> controls.

This is a genuine candidate the user should approve or reject explicitly,
not something I add unilaterally.
