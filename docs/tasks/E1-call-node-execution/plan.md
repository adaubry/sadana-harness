# Plan: EXECUTION — what a call step may do (from intent.md 2026-09-07)

## Files that change

- `src/sadana/execution.py` (new) — `HttpRequest`, `Success`, `Failure`,
  `Outcome = Success | Failure`, `run_http(request) -> Outcome`.
- `tests/unit/test_execution.py` (new) — mocks the transport, per
  `testing-conventions` (unit tests never touch the network).
- `scripts/prove_execution.py` (new) — the live-network proof, mirroring
  `scripts/prove_model_access.py`'s own role for C1.

`CLAUDE.md` also carries one new line, added during the design stage
(before this plan existed) and approved by the user separately from this
implementation: the "registry seam for a family of one" rule spec.md's own
Rejected alternatives argued for. No code in this plan changes it further.
`pyproject.toml` is explicitly untouched (spec.md's own acceptance
criterion: no new dependency). `config.py` is untouched too —
`config.env_int` already exists and is called with a new key name, not a
new function.

## Order of work

1. **`src/sadana/execution.py`.** `HttpRequest(method, url, headers, body)`
   and the closed `Success(status, body) | Failure(detail)` outcome, both
   frozen dataclasses. `run_http()` built directly off
   `model_providers/openrouter/provider.py`'s `_post()`: `urllib.request`,
   catch `urllib.error.HTTPError` → `Failure` (status + one-line body
   detail), catch `OSError` → `Failure(str(exc))`, nothing else raises.
   Timeout read via `config.env_int("SADANA_EXECUTION_HTTP_TIMEOUT_S", 30)`.
2. **`tests/unit/test_execution.py`.** Same mocking shape
   `tests/unit/test_model_access.py` already uses for `_post`: monkeypatch
   the one network call point, assert a 2xx → `Success` with the real
   status/body carried through, a 4xx/5xx `HTTPError` → `Failure`, a
   connection-level `OSError` → `Failure`, and that none of these raise out
   of `run_http`. Run `make test` narrowly against this file once written.
3. **`scripts/prove_execution.py`.** Two real, unmocked calls: a `GET` to
   `https://example.com` (IANA-reserved, stable, no third-party service
   dependency) asserted as `Success` with `status == 200`; a `GET` to a
   deliberately unresolvable hostname asserted as `Failure` — both exercise
   `run_http`'s real code path, not a mock, matching CLAUDE.md's "prove a
   block's first real external round trip with a standalone script." Run it
   and capture the output.
4. **Self-check**: `/ponytail-review` and `/simplify` against the diff, per
   build-skill's own phase-two close. Apply anything in the "worth taking
   now" bucket; note anything else in one line rather than expanding scope.
5. **`make verify`**, full run, pasted as this stage's evidence.

The order puts the deterministic, mocked part (steps 1-2) before the one
step that spends a real network call (step 3) — nothing in step 3 can be
wrong in a way steps 1-2 wouldn't already have caught, since it's the same
`run_http` function, just unmocked.

## Risks

**What could this break:** nothing existing. `execution.py` has zero
callers today — this work item explicitly stops short of wiring it to a
plugin `call` step (that's a separate, later work item per intent.md's own
Constraints). The only shared surface touched is `config.env_int`, called
with a brand-new key; its existing callers (`model_access.py`'s own timeout
lookup) are untouched. No existing test, fixture, or module is modified.

**Riskiest step:** step 3, the live-proof script — it is the only step that
depends on something outside this repo (DNS resolution, a real remote
host's availability) and could be flaky on a bad network. Mitigated by
ordering it last, after the deterministic unit tests already prove the
logic is correct in isolation, and by picking `example.com` specifically
for its unusually high stability (an IANA-reserved domain kept up for
exactly this kind of use) rather than a general third-party API.

**Rejected-alternative drift check**, against `spec.md`'s own `## Rejected
alternatives`: no `httpx` or other HTTP client dependency (stdlib only); no
`_BACKENDS` dict or `execute(backend, request)` dispatcher (direct call to
`run_http` only — also the new CLAUDE.md rule this work item's design
stage just added); no shared base class mirroring hermes's
`BaseEnvironment`. None of these appear in the plan above; confirmed
nothing here drifts back toward what the spec declined.

## Proof

- `tests/unit/test_execution.py` — pytest output (via `make test`) showing
  all cases (success, HTTP error, connection failure, never-raises) green.
- `scripts/prove_execution.py` output pasted verbatim: the real status/body
  from `example.com` and the real failure detail from the unresolvable
  host — this is the Deploy-stage Evidence CLAUDE.md asks for.
- `make verify` output ending `VERIFY OK`.
