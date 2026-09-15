# Plan: The real box behind the socket (from intent.md 2026-09-15)

Author: Claude, drafted for Adam Aubry (product owner) to review. Status: approved.

## Files that change

None. Requirement 5 (`spec.md`) forbids any change under `src/`, `scripts/`,
or `tests/` as part of this item — every finding this checkpoint surfaces is
recorded for its own follow-up work item, never patched here. The only
repository-visible artifacts this item produces are its own four files under
`docs/tasks/HCP-real-box-behind-socket/` plus `CLAUDE.md` (one line, already
approved and applied at the design stage) and two release tags (`0.0.1`,
`0.0.2`) — tags, not tracked files, and already pushed.

## Order of work

The nine steps the checkpoint's own instructions named, each landing
something checkable before the next began:

1. Provision the EC2 box per `install.md` (prereqs, clone at `0.0.1`, venv) —
   landed first because every later step depends on a working checkout.
2. `sadana setup` (user-run, secret-bearing) → `sadana enroll` → gateway
   install/start → `sadana gateway status` / `GET /v1/harness` / relay hello —
   the tether has to exist before anything door-facing can be tested.
3. `make verify` and an ad hoc conformance replay — run once the tree is
   known-good, so a real failure (the `test_gateway_service.py` precondition
   bug) is attributable to the box's real state, not a half-finished install.
4. The relay acceptance requirements 1–4 (key persistence, enroll shape,
   outbound-only, signed-challenge identity) — box-to-relay contract, proven
   before anything door-side is layered on top.
5. The two-organisation first-brick journey — the first real exercise of the
   door's own grammar and multi-tenancy boundary, landed once the box is
   confirmed reachable and authenticated.
6. A parked `call` node, approved through `/forward` — depends on (5)'s
   conversation/message plumbing already working.
7. Secret rotation + a turn on the next request, no restart — chosen to run
   *after* the approval flow so a working turn path was already proven
   before rotating what it authenticates with.
8. The upgrade round-trip — deliberately last among the write-risk steps: it
   restarts the daemon twice and briefly leaves the box on different code,
   so it ran only once everything upstream (enroll, door, approvals,
   secrets) was already confirmed working on the *original* checkout.
9. Kill/restart the relay, observe backoff and `changes?since=` catch-up —
   last, because it's the only step that deliberately breaks connectivity;
   running it earlier would have made every other step's failures ambiguous
   between "the box is broken" and "the relay is down."

Findings were recorded at the point each was discovered, not batched to the
end — CLAUDE.md's own maintain-stage posture ("a maintenance finding worth
recording is worth a work item") applies to the *outcome*, not to when
during the checkpoint it gets written down.

## Risks

**What could this change break?** Nothing in the codebase — no files
change. What it could have broken: the box's own standing as a *persistent*
staging environment for the console plan's later checkpoints (the user's
explicit "keep it running" decision). Three concrete things were at risk and
each was checked rather than assumed: the relay's in-memory enrollment
registry (mitigated — `--state-file` persists enrolled boxes' public keys
across the relay's own restart, confirmed working in step 9); the gateway
systemd service (mitigated — every restart used the exact
`sudoers`-scoped `systemctl restart sadana-gateway` install.md itself
documents, never a broader command); the box's own git checkout (mitigated
— the upgrade round-trip's second leg returned it to `0.0.1`, confirmed via
`sadana --version` before moving on).

**Which step is the most risky, and why that one?** Step 8 (upgrade). It's
the only step that required an actual production-code change
(`__version__` bump) in direct tension with this item's own "no production
code" constraint, and the only step where an interruption mid-sequence
(`git checkout` succeeding, `pip install` or `systemctl restart` failing)
would leave the box in a half-upgraded state harder to diagnose than any
other failure mode here. Mitigated three ways: the version bump was made by
the user's own hand, never through my `Edit`/`Write` tools (the
artifact-chain hook enforced this rather than merely suggesting it); the
target tag was validated end-to-end (`git show origin/main:...`) before any
box-side action touched it; and `sadana --version` was checked immediately
after each leg rather than trusting the operation's own `succeeded` state
alone.

**Which options did `spec.md` already reject, and is this plan drifting
back toward one?** No drift found. `spec.md`'s three rejections — no
`--remote` conformance mode, no persisted relay identity, no in-place fixes
to any of the eight findings — all held throughout execution: the
conformance step used an ad hoc replay instead of a new flag, the relay's
signing-key statelessness was worked around (a gateway restart) rather than
patched, and every finding was recorded, not fixed.

## Proof

Every numbered step's actual evidence — command output, HTTP responses,
journal excerpts — is pasted verbatim in `review.md`'s `## Evidence`
section, one subsection per checklist item, exactly as the checkpoint's own
instructions require ("paste it," "paste each result"). This plan names
*what* was proven and *in what order*; `review.md` carries the proof
itself, plus the eleven-promise grade this plan's acceptance criteria (in
`spec.md`) deliberately left to the deploy stage.
