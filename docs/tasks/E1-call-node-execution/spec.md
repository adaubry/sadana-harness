# Spec: EXECUTION — what a call step may do

Intent: docs/tasks/E1-call-node-execution/intent.md

## Requirements

1. A function exists that makes exactly one outward HTTP request on behalf
   of a first-party plugin's own code, given a request expressed as data
   (method, URL, headers, body) — not by handing that code unrestricted
   network access through some other channel this project provides. Traces
   to intent's Proposed outcome ("run code that actually touches the
   outside world, and what it is allowed to touch is bounded and
   inspectable... rather than arbitrary").
2. The function never raises for a network-level failure (a bad host, a
   timeout, a non-2xx response). Every outcome — success or failure —
   comes back as one of a small, closed set of return values, each
   carrying enough to know what happened, with nothing like a stack trace
   in it. Traces to intent's Proposed outcome ("the run still hands back a
   real, structured answer describing what happened") and CLAUDE.md's rule
   that a classified outcome with meaningfully different branches is a
   named, closed set, never a raised exception.
3. The request travels over the project's already-established HTTP
   mechanism (the standard library, matching `model_providers/openrouter`'s
   own transport) rather than a new third-party dependency. Traces to
   intent's Constraints declining the reference project's own backend
   catalogue — the same "fewer bets" reasoning, extended to the transport
   itself.
4. The mechanism runs in the same process as the rest of sadana — no
   subprocess, no container, no sandbox — matching intent's Constraint that
   every installed plugin is first-party today and that sandboxing
   untrusted code is explicitly out of scope.
5. Nothing here is wired to a plugin's own declared `call` step, and
   nothing here decides whether a step may run without approval. Traces to
   intent's Constraints ("does not build the outward-reaching plugin step
   itself, nor the later safety approval gate").

## Design

New module: `src/sadana/execution.py`. Real I/O (the network), so its own
file per CLAUDE.md's rule that a module touching disk/network/clock is
separate from a block's pure-function modules regardless of size.

```python
@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None

@dataclass(frozen=True)
class Success:
    status: int
    body: bytes

@dataclass(frozen=True)
class Failure:
    detail: str  # one safe line — never a stack trace

Outcome = Success | Failure

def run_http(request: HttpRequest) -> Outcome: ...
```

`run_http()` uses `urllib.request` — see Rejected alternatives — with the
timeout read from a new config key, `SADANA_EXECUTION_HTTP_TIMEOUT_S`
(`config.env_int`, default 30, same default `model_access.py` already uses
for the same reason), per CLAUDE.md's "timeouts go in config." It follows
`model_providers/openrouter/provider.py`'s own `_post()` shape exactly: a
`urllib.error.HTTPError` becomes `Failure` carrying the response status and
whatever one-line message the body yields; an `OSError` (DNS failure,
connection refused, timeout) becomes `Failure` with `str(exc)`; nothing
propagates past `run_http()`.

No registry, no dispatch table, no base class. `run_http` is called
directly. See Rejected alternatives for why a registry is not built here
even though the intent's own framing named one.

## Interface

**In:** `HttpRequest(method, url, headers, body)`.
**Out:** `Success(status, body)` on any completed HTTP exchange (2xx through
5xx alike — a 404 is a successful round trip that came back with a 404, not
a `Failure`); `Failure(detail)` only when the round trip itself did not
complete (DNS/connection/timeout) or the request could not be sent.
**Errors:** none raised. `run_http` has the same "never raises" contract
`model_providers/openrouter/provider.py:_post` already carries.

## Acceptance criteria

- [ ] `src/sadana/execution.py` exists; `run_http()` is directly callable
      with no plugin, DAG, or catalog machinery in the loop.
- [ ] A unit test (`tests/unit/test_execution.py`) covers: a mocked 2xx
      response becomes `Success`; a mocked HTTP error status becomes
      `Failure`; a mocked `OSError` (connection failure) becomes `Failure`;
      none of these raise out of `run_http`. No network touched by the unit
      suite, per `testing-conventions`.
- [ ] A standalone script proves one real outward HTTP round trip against a
      live endpoint, output pasted as Deploy-stage evidence, per CLAUDE.md's
      rule to prove a block's first real external round trip outside
      `make test` rather than relaxing the unit suite's network ban.
      Mirrors `scripts/prove_model_access.py`'s own live proof for the
      identical reason (an HTTP transport that has only ever talked to a
      mock has not actually been proven).
- [ ] No new entry added to `pyproject.toml`'s `dependencies`.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Sandboxing untrusted or marketplace-installed plugin code (intent's own
  Constraints).
- A multi-backend dispatch table or registry (see Rejected alternatives).
- Wiring `run_http` to an actual plugin `call` step — that is PLUGINS'
  execution half, item 6, a separate work item building on this one.
- SAFETY's approval gate on effectful steps — a separate, later work item.

## Rejected alternatives

**Hermes's `tools/environments/` backend family** (`base.py`, 1,602 lines;
eight concrete backends — `local.py`, `docker.py`, `ssh.py`, `modal.py`,
`managed_modal.py`, `singularity.py`, `daytona.py`, `vercel_sandbox.py` —
2,356 lines for `local.py` alone) — declined outright, not narrowed. This is
hermes's own answer to a question sadana is not asking yet: sandboxed,
resumable, multi-tenant terminal sessions with heredoc-embedded stdin,
CWD-marker protocols, and per-backend resource limits. None of that has a
consumer here — there is no sandbox requirement (intent declines it
explicitly) and no second execution surface to unify. Porting the shape
would mean importing machinery built for a problem (untrusted, long-lived,
resumable sessions) this item's own intent says does not exist yet.

**Hermes's `_create_environment()` factory**
(`../hermes-agent/tools/terminal_tool.py:1948`) — the literal "registry
seam" the intent's opening framing evoked. Read directly: it is a single
~200-line function, one large `if`/`elif` keyed on an `env_type` string,
each branch unpacking its own slice of a `container_config` dict (CPU,
memory, disk, volumes, network flags...). Declined specifically because
growing it is "make a step heavier" in the cheapest, worst sense — every
new backend adds another branch and more shared-parameter surface to an
already-large function, the opposite of guideline 3's "add a step" move.
`model_access.py`'s own registry (below) already represents this project's
considered alternative to that exact shape, built and proven once already.

**A dict-keyed multi-backend registry, mirroring `model_access.py`'s
`ProviderManifest`/`register_provider`** (`_BACKENDS: dict[str, Callable]`,
looked up by a `backend: str` argument) — this is the shape "take the
registry seam" most literally suggests, and it is declined for now, not
forever. `model_access.py`'s registry earns its cost by declaring 39 real
provider identities today, 38 of them unwired (`register_provider` called,
`request_fn=None`) — there is a real reason to list them even before
they're wired: `list_providers()`/`get_provider()` have real callers today.
EXECUTION has no second backend to declare an identity for: intent.md
already declined porting even identity-only stubs for hermes's other seven
backends, for the same reason it declined their implementations — there is
no marketplace, no untrusted source, nothing to sandbox yet. A one-entry
lookup table returns nothing a direct call to `run_http()` doesn't already
give, and the literal cost of adding a second backend later — a new
function plus updating the one call site that currently calls `run_http`
directly — is identical whether or not a dict exists today. Building the
registry now is a bet with no return until a second backend is actually
designed; this is exactly guideline 2's "do not invent a plugin system
early" caveat, applied one level down from PLUGINS to EXECUTION's own
internals. Revisit the moment a second real backend is designed — the
project's own "two occurrences" bar for promoting a pattern.

**`httpx`**, named in `docs/reference/plugin_blueprint.md §2.3`'s own
wording ("a call node that hits an HTTP API is `httpx` in-process") —
declined. The project's one existing real outward HTTP path
(`model_providers/openrouter/provider.py`) already made this exact call for
the exact same shape of problem: `urllib.request` from the standard
library, no client library, and it already carries the "never raises,
maps every failure to a return value" contract this item needs verbatim.
Nothing about a plugin's outward call is different enough to justify a
dependency the rest of the project already declined for the identical
reason (ponytail's own ladder: stdlib before a dependency).

## Concerns

**"Bounded and inspectable rather than arbitrary" is a paved path here, not
an enforced boundary — worth reading intent.md's own wording carefully
against what this item actually ships.** A first-party plugin's own
`init.py` code could still call `urllib`, `socket`, or `subprocess` directly
instead of going through `run_http()`; nothing in this design stops it.
Real enforcement — making the unsanctioned path actually impossible, not
merely unnecessary — is a sandboxing problem, and intent.md explicitly
declines sandboxing for this item (first-party only, no marketplace). What
ships is the easy, structured, inspectable way to reach outside; it is not
a wall around any other way. This should be named plainly to whoever
reviews this against the intent, since "bounded" can read as a stronger
guarantee than what's actually built.

**Policy conformance named explicitly, per the brief's own instruction:**
`testing-conventions` applies and is followed above (unit tests mock the
transport, network-touching proof lives in a standalone script, not the
unit suite). `project-structure`, `reference-lookup`, and any security/UX
skill do not exist as separate skills in this repository — CLAUDE.md's own
"Our methodology for learning from the reference" section is the project's
actual reference-lookup policy, and it is what guideline 1's research above
follows (hermes's `EXECUTION` block in
`docs/reference/hermes_core_blocks_kind.csv`, read and audited, not copied).

**No genuine policy conflict found.** The interface is small (one request
type, one closed outcome pair, one function), the decision to defer a
registry is cheap to reverse (adding one later changes nothing about
`run_http`'s own signature), and there was no point in this design where
two of this project's own rules pulled in different directions.
