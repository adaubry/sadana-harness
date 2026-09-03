# Spec: MODEL-ACCESS proves one provider before it names the rest

Intent: docs/tasks/C1-model-access/intent.md

## Requirements

1. `send(request) -> Outcome`, the single entry point B2 already specified,
   is implemented and produces a real HTTP round trip against OpenRouter for
   at least the `response` outcome — not a stub, not a mock, not a recorded
   fixture standing in for a live call.
2. Every one of B2's other five outcomes — `retry`,
   `needs-credential-or-provider-change`, `needs-context-compression`,
   `degenerate`, `abort` — is demonstrated as real, provoked behavior on the
   OpenRouter path, not merely representable as a Python value nothing ever
   returns.
3. A provider registration mechanism exists such that any of hermes's 39
   providers can be added by registering one identity manifest, without
   changing the registry, `send()`, or any other provider's registration.
4. All 39 providers' manifests — name, credential environment variable,
   base URL — are copied from hermes and registered. Only OpenRouter's
   request-handling is implemented; the other 38 register but are not
   wired to run.
5. The interface matches B2's spec exactly: one entry point, the closed
   outcome set above, the active model's context-window size exposed
   alongside `send()`, and the retry bound owned by MODEL-ACCESS and carried
   as data in `request` rather than held as state between calls.
6. Streaming, credential rotation across multiple accounts on one provider,
   and the tool-call plug point B2 named are not implemented here — B2
   scoped the plug point to CONVERSATION's side of the boundary in any case.

## What the reference corpus showed

Two research passes read hermes-agent's transport layer, provider-manifest
mechanism, error classification, retry/deadline handling, and credential
resolution — one pass on each half, both scoped to what a single-provider,
single-key proof round trip actually needs, not the full ~170-file block.

**Transports are keyed by wire protocol, not by provider — and OpenRouter
needs none of its own.** `ProviderTransport` (`agent/transports/base.py:16-89`)
is a four-step ABC — `convert_messages` → `convert_tools` → `build_kwargs` →
`normalize_response` — that explicitly excludes client construction,
credential refresh, streaming, and retry from its own scope, by the
transport's own docstring. Transports are registered by an `api_mode`
string, not a provider name; `ChatCompletionsTransport`
(`agent/transports/chat_completions.py:329`, self-registers at
`chat_completions.py:1138`) already handles roughly sixteen OpenAI-compatible
providers by its own docstring. `OpenRouterProfile` never overrides
`api_mode` (`plugins/model-providers/openrouter/__init__.py:234-252`), which
defaults to `"chat_completions"` (`providers/base.py:44`) — OpenRouter rides
the shared transport unmodified. This directly shapes the design below: the
"one working transport" the intent asks for does not mean "one bespoke
OpenRouter transport," it means implementing the one shared
protocol-shaped path OpenRouter happens to use, which is also the path most
other chat-completions-style providers would reuse later — a happy
coincidence of picking OpenRouter, not something engineered for.

**The provider manifest hermes actually runs on is not the YAML file.**
`plugin.yaml` (`plugins/model-providers/openrouter/plugin.yaml:1-5`) carries
only name, kind, version, description, author — decorative, not read by any
runtime discovery path found. The real manifest is a `ProviderProfile`
dataclass instance (`providers/base.py:39-100`, ~15 fields covering
identity, auth, capability flags, model catalog, and request-shaping hook
overrides), built and registered by a call to `register_provider(...)` at
the bottom of each provider's own `__init__.py`
(`plugins/model-providers/openrouter/__init__.py:252`).

**The discovery mechanism is already the shape this work item wants, and it
lives inside the MODEL-ACCESS block.** `providers/__init__.py` (confirmed
present in `docs/reference/hermes_core_blocks_kind.csv` under MODEL-ACCESS)
discovers providers lazily — nothing scans at import time, only on first
`get_provider_profile()`/`list_providers()` call
(`providers/__init__.py:75-76,84-85`) — by scanning directories and
dynamically importing each provider's `__init__.py`
(`providers/__init__.py:111-146`); importing the module **is** registration,
since `register_provider(profile)` runs at each module's own import time as
a side effect, populating one module-level `_REGISTRY` dict
(`providers/__init__.py:45,56-67`). Hermes runs this at three precedence
tiers (pip entry-points, bundled directory, user directory,
`providers/__init__.py:149-343`); this work item adopts the shape (lazy
directory-scan-and-import into one registry) and declines two of the three
tiers (see `## Rejected alternatives`).

**Failure classification is status/body-driven, not stack-driven — mostly.**
`classify_api_error()` (`agent/error_classifier.py:810`) runs a
priority-ordered pipeline over HTTP status code and response body text,
returning a `ClassifiedError` (`error_classifier.py:85-107`) with a
`~25`-value `FailoverReason` enum (`error_classifier.py:30-77`) covering
auth, rate limits, server errors, context overflow, content-policy blocks,
and more. This file is clean of the traceback-frame-inspection pattern B2's
spec already named as the cause of two hermes incidents. That pattern does
exist, narrower, in two other places: a deliberately scoped stale-connection
check in `agent/bedrock_adapter.py:321-380`, and a closer match in
`agent/conversation_loop.py:8700-8708`, which walks a traceback's frame
filenames to decide local-processing-vs-API-call — that file is CONVERSATION's,
not MODEL-ACCESS's, so it is not this work item's to fix, but it confirms
the pattern is still live somewhere in the system and worth declining
deliberately here rather than by accident.

**Retry is already stateless in hermes, matching B2's own design.**
`agent/retry_utils.py` (208 lines, read in full) takes `attempt: int` as an
explicit argument everywhere; the only thing held between calls is a
process-global jitter-seed counter for RNG decorrelation, not attempt
counting. The retry ceiling itself (`api_max_retries`, default 3,
`retry_utils.py:187`) lives in the calling loop, not in this module —
confirming B2's own design choice (attempt number carried in `request`,
cap owned and compared by MODEL-ACCESS) rather than inventing something new.

**"Degenerate" has a precise definition, and a state pattern worth
declining.** `agent/empty_response_guard.py` (272 lines, read in full)
defines degenerate as `usage_present=True` with `output_tokens==0` and a
non-refusal `finish_reason` (signaled refusals are excluded, handled
elsewhere as already-terminal). But the guard's own streak/cost-budget
state is stashed as attributes directly on the shared agent object
(`empty_response_guard.py:62-68`) — exactly the side-channel mutation shape
B2's spec already rejected in favor of return values. Adopted: the
definition of degenerate. Declined: tracking a streak via object mutation.

**Deadline is a stateless per-call bound, and names itself distinctly from
a provider's own timeout.** `agent/deadline.py` (structure read in full)
resolves a timeout from config, clamps it, and enforces it with a daemon
`threading.Timer` independent of the event loop, so a stalled loop can't
silently disable it. Its own docstring states the invariant this design
adopts directly: a timeout from this layer is *our* deadline, not the
provider's, and should classify distinctly from a transport-level timeout
the provider itself reports.

**Credentials for a single-API-key provider are genuinely minimal — no
detour through hermes's multi-account machinery required.**
`agent/credential_pool.py:3330-3345` resolves OpenRouter's credential by
reading exactly one environment variable, `OPENROUTER_API_KEY`, with no
OAuth or pooling involved. No pre-flight `MissingCredentialError`-style
raise was found in the credential-resolution files scoped; a missing or
invalid key appears to surface only after an attempted call, as a 401
classified into the `auth`/`auth_permanent` `FailoverReason`. This work
item's design (below) adds the pre-flight check hermes's scoped files
didn't show, rather than assuming its absence is deliberate.

**One expected file turned out not to be what it sounded like.**
`agent/bounded_response.py` does not bound context-window/request size —
it bounds *reading a failed streaming call's error body* (byte cap plus
wall-clock deadline on a worker thread). Noted so nobody reaches for it
expecting context-window enforcement.

**`agent/model_metadata.py` / `agent/models_dev.py` are named explicitly as
a non-goal, not a port target.** Context-window resolution in hermes is a
multi-thousand-line subsystem: a live fetch from a public model database,
three-tier caching, a background refresh daemon, and per-provider
fallback/override logic. It does cover OpenRouter-routed models
generically, so it is the right shape for hermes's ~40-provider,
thousands-of-models surface — and the wrong shape for a work item wiring
one model on one provider. See `## Non-goals`.

## Design

**Where this lives**, extending B2's own placement decision and A1/B1's
existing convention (one flat module per block, no shared growing object):
`src/sadana/model_access.py` is the single public surface — `send()`,
`Request`, the `Outcome` shapes, and `context_window()` — exactly as B2
named it. It owns a provider registry, populated lazily on first use by
scanning one new directory, `src/sadana/model_providers/<name>/`, and
importing each provider's `__init__.py`, mirroring hermes's own
`plugins/model-providers/*/` layout and its lazy-scan-and-import mechanism
(`## What the reference corpus showed` above) — because that shape is
already exactly "one place any of the 39 can register," which is what the
intent asks for, and re-deriving a different shape from scratch would be a
second bet for no reason found in the reference reading.

**The manifest is intentionally smaller than hermes's `ProviderProfile`.**
Each provider directory's `__init__.py` calls `register_provider(...)` with
a small frozen dataclass: `name`, `env_var` (the credential's environment
variable name), `base_url`, and `request_fn` — a callable of
`(Request) -> Outcome`, `None` by default. Registering a provider never
requires implementing `request_fn`; calling `send()` against a provider
whose `request_fn` is `None` raises a clear, named error identifying the
provider as registered-but-unwired, rather than silently doing nothing or
routing somewhere unintended. Hermes's `ProviderProfile` carries roughly
fifteen fields — vision/cache capability flags, a model catalog, and
several request-shaping hook overrides — none of which any of the 38
unwired providers need yet, and OpenRouter's own two request-shaping
quirks (a sticky-routing key, a reasoning-config translation) are kept as
a small function private to OpenRouter's own `__init__.py`, not fields on
the shared dataclass, mirroring hermes's own override-hook pattern for
exactly the same reason hermes has it: one provider's quirk should not grow
every other provider's manifest shape.

**Classification is one function, adopted from hermes's shape and narrowed
to what six outcomes need.** A single `classify(status, body) -> Outcome`
style function maps an HTTP status code and response body to one of B2's
six outcomes — modeled on `classify_api_error()`'s status/body-first
approach, not on frame-inspection. It does not attempt hermes's ~25-value
`FailoverReason` granularity; it only needs to separate the six buckets
`send()` can return:

- 401/403, or the credential's environment variable unset before any
  request is attempted → `needs-credential-or-provider-change`
- 429, 5xx, a network-level transient error, or a deadline expiry from this
  work item's own timeout (not the provider's) → `retry`, bounded (below)
- 413, or a response body naming the context length exceeded →
  `needs-context-compression`
- 200 with usage present, zero output tokens, and a non-refusal
  finish-reason (hermes's own definition, adopted) → `degenerate`
- anything else that answered but cannot be classified into the above
  (malformed request, unknown model) → `abort`
- a normal, usable response → `response`

The credential pre-flight check — failing before any network call if the
environment variable is simply unset — is this work item's own addition;
nothing in the hermes files read pre-empted a call this way. It costs one
`os.environ` read and makes "credential missing entirely" a real, cheap,
zero-network demonstration of `needs-credential-or-provider-change`,
distinct from "credential present but rejected," which is demonstrated by
an actual round trip with a deliberately invalid key.

**Retry and deadline are both stateless, mirroring hermes's own already-
proven shape.** The retry cap is a config value (`SADANA_MODEL_ACCESS_MAX_RETRIES`,
default 3, read via `sadana.config.env_int`), compared against the attempt
number arriving in `request` — never stored between calls. The per-call
deadline is likewise a config value
(`SADANA_MODEL_ACCESS_TIMEOUT_S`, the exact name B1's own module docstring
already used as its example) enforced once per attempt; a deadline expiry
is classified as `retry`, the same bucket B2's own spec already uses for a
transient transport error, not a new outcome.

**Context window is one static fact for one model, not a subsystem.**
`context_window()` returns the known window size for the one wired model
(a deepseek model available through OpenRouter; the exact model string is
a config value, not fixed in code) from a small table with exactly one
entry. Hermes's live-fetch, three-tier-cache, background-daemon machinery
is not ported — see `## Non-goals`.

## Interface

Unchanged from B2's spec — this work item implements it, not revises it.

**In** (`Request`): message history as data, merged tool schemas (empty in
this work item — nothing populates them yet, since EXECUTION and CONTEXT
are unbuilt), provider/model selection, and the attempt number (zero on a
first attempt).

**Out** (`Outcome`), one of: `response` (assistant message, tool calls,
usage — tool calls will always be empty here, since nothing offers tool
schemas yet); `retry` (carries the incremented attempt number, per B2, for
the caller to pass back unchanged); `needs-credential-or-provider-change`;
`needs-context-compression`; `degenerate`; `abort`. `send()` never calls
another block itself — it returns the classified outcome and stops, exactly
as B2 specified.

`context_window(model) -> int` sits alongside `send()`, callable
independently, per B2's requirement that both CONVERSATION and CONTEXT read
the same owned fact rather than each guessing or reaching into CONFIG.

## Acceptance criteria

- [ ] `send()` against OpenRouter, real network, returns `response` with an
      actual assistant message — pasted as Deploy-stage evidence, not
      claimed.
- [ ] Each of `retry`, `needs-credential-or-provider-change`,
      `needs-context-compression`, `degenerate`, and `abort` is provoked for
      real on the OpenRouter path at least once, and each provocation's
      output is pasted as evidence.
- [ ] All 39 provider directories exist, each registers a manifest
      (`name`, `env_var`, `base_url`) copied from hermes's own
      `ProviderProfile` declaration, and `list_providers()`-equivalent
      returns all 39.
- [ ] Calling `send()` against any of the 38 unwired providers raises a
      named, specific error identifying the provider as registered but not
      wired — never a silent no-op, never routed to another provider.
- [ ] Unit tests cover `classify()` and `send()`'s six-way branching by
      stubbing the transport/HTTP call entirely — no unit test touches the
      network or a real credential, per `testing-conventions`.
- [ ] The retry cap and deadline are read from config
      (`SADANA_MODEL_ACCESS_MAX_RETRIES`, `SADANA_MODEL_ACCESS_TIMEOUT_S`),
      not hardcoded, and neither is stored as state between calls.
- [ ] `context_window()` returns the correct value for the one wired model
      and raises (not silently defaults) for any other.

## Non-goals

Porting `agent/model_metadata.py` / `agent/models_dev.py`'s live model
catalog — one static fact for one model is this work item's whole
context-window surface. Porting the full `ProviderProfile` shape (vision
flags, cache flags, model catalogs, per-provider request-quirk hooks) for
any of the 38 unwired providers. Porting hermes's pip-entry-point or
user-directory provider discovery tiers — no consumer for a
third-party-installed or user-local provider exists yet. Multi-account
credential rotation, `credential_pool.py`'s full machinery, streaming, and
the tool-call plug point — all already named out of scope in `intent.md`.
Fixing `conversation_loop.py`'s traceback-frame classification pattern —
real, but CONVERSATION's, not this work item's.

## Open questions

Intent's own open question stands: whether a manifest shape fitted before a
second provider is ever ported will actually fit that second provider isn't
resolvable until one is ported. Kept minimal (identity only, quirks as a
private per-provider hook function) specifically to make a wrong guess here
cheap to correct — see `## Concerns`.

## Rejected alternatives

Classifying retryability by inspecting exception traceback/frame module
names — the pattern B2's spec already traced to two hermes incidents
(`## What the reference corpus showed` above finds it still live in
`conversation_loop.py`, confirming the risk is real, not historical).
Rejected in favor of hermes's own cleaner alternative, already proven in
`error_classifier.py`: classify from HTTP status and response body.

Storing empty-response/retry-streak state as attributes on a shared object,
the way `agent/empty_response_guard.py:62-68` does. Rejected for the same
reason B2's own spec rejected side-channel mutation generally: harder to
unit-test than a return value, and this work item's `classify()` needs no
cross-attempt memory to do its job — each attempt is classified on its own
evidence.

Porting hermes's full ~15-field `ProviderProfile` dataclass for all 39
providers now. Rejected: every field beyond identity (name, credential
variable, base URL) exists in hermes to support request-shaping behavior
this work item deliberately isn't porting for 38 of the 39 providers;
carrying those fields now is a bet on a shape nothing yet uses.

Porting hermes's three-tier provider discovery (pip entry-points, bundled,
user-local). Rejected for two of the three tiers: no consumer exists for
installing a third-party provider via pip or dropping one in a user
directory. The bundled-directory tier is adopted; the other two are a bet
with no caller, exactly what guideline 2 warns against.

Wiring hermes's live models.dev-backed context-window subsystem now.
Rejected on cost alone: a multi-thousand-line, cache-and-daemon-backed
system to serve a single fact for a single model this work item needs is
the heavier of two moves (guideline 3) when the lighter one — a one-entry
table — catches the same scenario (`context_window()` answers correctly for
the one model in use) at far lower cost.

Running the real OpenRouter round trip inside the pytest suite so `make
test` exercises it directly. Rejected: `testing-conventions` bars network
and model-API calls from unit tests outright, and no integration or
contract test tier exists yet to house a real-network test safely apart
from the unit suite. The round trip runs as a standalone script instead,
and its output is pasted into `review.md`'s `## Evidence` alongside `make
verify`'s output — see `## Concerns` for why this is a project-wide
tension, not just this work item's.

Pre-empting the missing-credential case with a pre-flight check was not
found in hermes's own scoped files (`## What the reference corpus showed`),
raising the question of whether to omit it here too, matching hermes
exactly. Rejected: the intent requires demonstrating
`needs-credential-or-provider-change` as real, and a zero-network,
one-`os.environ`-read pre-flight check is cheap enough that skipping it to
match hermes exactly would cost more in awkwardness (forcing a fake or
temporarily-unset real key to prove the same outcome the pre-flight check
would prove for free) than it saves.

## Concerns

The sharpest tension: the intent demands a genuinely real round trip as
proof, and `testing-conventions` flatly bars the model API from unit tests.
B2's own `## Concerns` already saw this coming — "MODEL-ACCESS's whole
purpose is to talk to a model... the `send()` seam has to be narrow and
stub-friendly by construction." This spec resolves it by splitting the
deliverable in two: unit tests stub the transport completely and cover
`classify()`'s six-way branching and `send()`'s orchestration around it;
the actual live round trip is a standalone script outside `make test`,
run manually, with its output pasted as Deploy-stage evidence. This is
clean for one work item, but it is a pattern, not a one-off — any future
block whose whole purpose is real external I/O (a database, a webhook, a
different model provider) hits the identical tension. Worth a durable rule
rather than re-deciding it per work item; see the amendment proposed
alongside this spec.

Second: the minimal provider manifest (`name`, `env_var`, `base_url`,
optional `request_fn`) is itself a bet made before a second provider is
ever ported, which is exactly the situation guideline 2's caveat warns
about — no plugin seam should be invented speculatively. The mitigation is
that this isn't a new seam invented for this work item: it is hermes's own
already-proven registration shape, narrowed by dropping fields nothing
here uses yet, not a novel design being bet on cold. If it's wrong once
provider #2 is actually ported, the fields hermes already has ready to
reference (in each provider's own `__init__.py`, one file away) make
correcting it cheap.

Third: this design adds a credential pre-flight check hermes's own scoped
files didn't show. It's a small, justified addition (see `## Rejected
alternatives`), but it means this work item is not a pure port for that one
piece — worth a reviewer's attention specifically because everything else
in this spec is adoption, and this one piece is new.

Policy conformance: `testing-conventions` is applied throughout (unit tests
stub the transport; no network, wall clock, or real filesystem in the unit
suite; no source-reading tests; the one runner). No `project-structure` or
`reference-lookup` skill exists in `.claude/skills/` yet — B2's spec noted
the same gap and used CLAUDE.md's own "Layout and ownership" and "The
reference corpus" sections in their place; this spec does the same, and
found no tension between them. No security, brand, or UX skill applies:
this work item's only external surface is one outbound HTTPS call to a
provider the user chose, carrying no user-facing UI and no credential
storage beyond reading one environment variable hermes itself reads the
same way.
