# Plan: MODEL-ACCESS proves one provider before it names the rest (from intent.md 2026-09-03)

## Context

C1-model-access implements what B2's spec already fixed as MODEL-ACCESS's
contract: one entry point `send(request) -> Outcome`, a closed six-outcome
set, an owned context-window fact. The proof is one real round trip through
OpenRouter to a deepseek model — not mocked — with all six outcomes
demonstrated for real, while the other 38 hermes providers get registered
(pluggable) but not wired to run.

Two research passes into `../hermes-agent` (during design) plus direct
reads just now of the actual implementation files ground every decision
below. Three findings from those direct reads changed the design since
`spec.md` was written:

1. **hermes's transport doesn't need a `convert_messages` step for us.**
   `ProviderTransport` (`agent/transports/base.py:16-89`) is a four-step ABC,
   but `convert_messages`/`convert_tools` exist to translate OpenAI-shaped
   input into a *different* wire format (Anthropic, Codex). OpenRouter's
   wire format *is* OpenAI-shaped — so for us that step is a no-op. Lighter
   design: no conversion layer, `Request.messages` goes straight into the
   HTTP body.
2. **B2 already says `send()` makes one attempt and returns — it does not
   loop.** Re-reading B2's own Interface section: "a transient transport
   error returns `retry` carrying the incremented attempt number **for the
   caller to pass back**." Retrying is CONVERSATION's job, not MODEL-ACCESS's.
   So no backoff/jitter code is needed at all in this work item — hermes's
   `retry_utils.py` (jittered backoff, Z.AI-specific long-tail schedules) is
   *not* something to port any part of, not even the shape. This is a
   bigger simplification than `spec.md` implied.
3. **No new HTTP dependency needed.** `pyproject.toml` has zero
   dependencies, and hermes's own `ProviderProfile.fetch_models` default
   implementation (`providers/base.py:273-296`) already uses bare
   `urllib.request` for a provider HTTP call — stdlib is proven sufficient
   in the reference corpus itself. `urllib.request.urlopen(req,
   timeout=...)` also gives a real deadline for free (raises
   `TimeoutError`/`socket.timeout`), so hermes's `agent/deadline.py`
   (`threading.Timer`, built to protect an event loop we don't have) is
   adopted in *principle* only (a deadline is ours, not the provider's,
   and classifies distinctly) — not ported as code.

Also confirmed directly: `pytest.ini_options` already declares
`integration`/`contract`/`eval` markers (`pyproject.toml`), but
`scripts/run_tests.sh` / `make test` apply no `-m` filter — the whole
suite runs unfiltered, hermetically (`env -i`, no credentials reachable).
So a marked integration test would still execute under `make test` and
fail for lack of network/credentials. This confirms (doesn't just assume)
`spec.md`'s call: the real round trip is a standalone script, never a
pytest test, and no new Makefile plumbing is invented to carve out an
exception — `spec.md`'s proposed CLAUDE.md amendment already covers this
and the user has approved it.

Confirmed real values to use, straight from hermes's own provider profile
(`plugins/model-providers/openrouter/__init__.py:234-249`): base URL
`https://openrouter.ai/api/v1`, credential env var `OPENROUTER_API_KEY`,
and `deepseek/deepseek-chat` is literally in hermes's own
`fallback_models` tuple for OpenRouter — reused as-is, not invented.

## Design (recap, implementation-level)

`src/sadana/model_access.py` — the single module, matching `config.py`'s
precedent (one flat module per block):

- `Request` (frozen dataclass): `messages: tuple[dict, ...]`, `tools:
  tuple[dict, ...] = ()`, `provider: str`, `model: str`, `attempt: int = 0`.
- Six frozen `Outcome` dataclasses — `Response`, `Retry`,
  `NeedsCredentialOrProviderChange`, `NeedsContextCompression`,
  `Degenerate`, `Abort` — plus `Outcome = Response | Retry | ...` as a type
  alias. Chosen over one dataclass with a `kind` tag because
  `isinstance`/`match` on a closed union is what B2's "closed set" language
  asks for, and it is what a unit test asserts against most directly.
- `ProviderManifest` (frozen dataclass): `name: str`, `env_vars: tuple[str,
  ...]`, `base_url: str`, `request_fn: Callable[[Request], _RawResult] |
  None = None` — deliberately smaller than hermes's ~15-field
  `ProviderProfile`; see `spec.md`'s `## Rejected alternatives`.
- `register_provider(manifest)`, `get_provider(name) -> ProviderManifest`
  (raises `UnknownProvider`), `list_providers() -> list[ProviderManifest]`,
  and a lazy `_discover()` that scans `src/sadana/model_providers/*/` and
  imports each `__init__.py` via `importlib.util.spec_from_file_location`
  — this is hermes's own mechanism (`providers/__init__.py:111-146,
  271-309`), narrowed to one tier (bundled only; no pip entry-points, no
  user directory — no consumer for either yet, named in `spec.md`'s
  Rejected alternatives).
- `classify(status, body, *, attempt, max_retries) -> Outcome` — the
  chat-completions-wire classifier: 401/403 or a missing credential (see
  below) → `NeedsCredentialOrProviderChange`; 429/5xx/a transport-level
  timeout → `Retry(attempt+1)` if `attempt < max_retries` else `Abort`;
  413 or a response body naming context/length exceeded →
  `NeedsContextCompression`; HTTP 200 with `usage.completion_tokens==0`
  and a non-refusal `finish_reason` (hermes's own definition,
  `agent/empty_response_guard.py`, adopted) → `Degenerate`; a clean 200 →
  `Response`; anything else answered-but-unclassifiable → `Abort`.
- `send(request) -> Outcome`: look up the manifest (`get_provider`, raises
  `UnknownProvider` for a typo'd name); if `request_fn is None`, raise
  `ProviderNotWired(name)` — never a silent no-op; **generic credential
  pre-flight**, checked once here for every provider using
  `manifest.env_vars` rather than duplicated per-provider — if any
  declared env var is unset, return `NeedsCredentialOrProviderChange`
  without calling `request_fn` (zero network calls); otherwise call
  `manifest.request_fn(request)`, which performs the one HTTP attempt and
  calls `classify()` on the real result.
- `context_window(provider, model) -> int`: one hardcoded table with
  exactly one entry (`("openrouter", "deepseek/deepseek-chat")`); raises
  `KeyError`/a named error for anything else. The actual window size gets
  looked up during implementation (OpenRouter's public, unauthenticated
  `/api/v1/models` catalog, confirmed no-auth-required by hermes's own
  `OpenRouterProfile.fetch_models` docstring) rather than guessed.

`src/sadana/model_providers/openrouter/__init__.py` — the one wired
provider. `request_fn` builds the OpenAI-chat-completions-shaped JSON body
directly from `Request.messages`/`.tools` (no conversion step — see
Context finding 1), POSTs via `urllib.request.urlopen(req,
timeout=SADANA_MODEL_ACCESS_TIMEOUT_S)`, and hands `(status, parsed_body)`
to `classify()`. The actual HTTP call is isolated behind one small
seam — a module-level `_post(url, headers, body, timeout) -> (status,
dict)` function — so unit tests monkeypatch `_post` and never touch the
network, per `testing-conventions`.

`src/sadana/model_providers/<name>/__init__.py` × 38 — one per remaining
hermes provider. Each copies exactly `name`, `env_vars`, `base_url` from
hermes's own `plugins/model-providers/<name>/__init__.py` profile
instantiation and calls `register_provider(ProviderManifest(...))` with
`request_fn` left `None`. Mechanical, done directly against each source
file during implementation (not pre-enumerated here — the CSV plus each
file's own `ProviderProfile(...)` call is the source of truth, per
build-skill's "finding facts is your job").

Two small config reads, both through `sadana.config.env_int` (already
built by B1, no changes to `config.py`): `SADANA_MODEL_ACCESS_MAX_RETRIES`
(default 3) and `SADANA_MODEL_ACCESS_TIMEOUT_S` (default 30) — matching
the exact env-var name `config.py`'s own module docstring already used as
its illustrative example.

Credential reading is `os.environ.get(var)` directly (matching hermes's
own minimal OpenRouter path and `config.py`'s existing `env()` primitive)
— no new `.env`-file-parsing code. "Use `.env` for secrets" stays an
operational convention (the user's shell loads `.env` before running the
proof script); building a `.env` loader is not this block's job and has no
second consumer yet.

## Files that change

**Amended post-implementation** (see `## Risks` below and `review.md`):
every provider manifest file below is named `provider.py`, not
`__init__.py` as originally planned — discovered during step 1's first
`make typecheck` run. Provider directory names carry hyphens (`ai-gateway`,
etc.); mypy's `files = ["src"]` package-derivation walk treats any
directory containing `__init__.py` as an importable package and validates
the directory name as a Python identifier, which hyphens fail. Renamed to
`provider.py` (these files were never meant to be regularly imported
anyway — always loaded via `spec_from_file_location`) and excluded
`model_providers/` from mypy's walk in `pyproject.toml` (also touched,
originally unlisted here) rather than fought. `src/sadana/model_access.py`'s
`_discover()` docstring carries the full reasoning.

- `src/sadana/model_access.py` (new) — `Request`, six `Outcome` dataclasses,
  `ProviderManifest`, registry functions, `classify()`, `send()`,
  `context_window()`, named exceptions (`UnknownProvider`,
  `ProviderNotWired`).
- `pyproject.toml` (touched) — one `[tool.mypy] exclude` entry for
  `model_providers/`, per the naming correction above.
- `src/sadana/model_providers/openrouter/provider.py` (new) — manifest +
  real `request_fn` + the `_post` HTTP seam.
- `src/sadana/model_providers/<name>/provider.py` (new) × 38 — one per
  remaining hermes provider (`actual`, `ai-gateway`, `alibaba-coding-plan`,
  `alibaba`, `anthropic`, `arcee`, `azure-foundry`, `bedrock`,
  `commandcode`, `copilot-acp`, `copilot`, `custom`, `deepinfra`,
  `deepseek`, `fireworks`, `gemini`, `gmi`, `huggingface`, `kilocode`,
  `kimi-coding`, `meta-ai`, `minimax`, `nebius-token-factory`, `nous`,
  `novita`, `nvidia`, `ollama-cloud`, `openai-codex`, `opencode-free`,
  `opencode-zen`, `qwen-oauth`, `router`, `stepfun`, `upstage`, `vertex`,
  `xai`, `xiaomi`, `zai` — 37 names here, confirm the 38th against
  `plugins/model-providers/*/` at implementation time; `router` is Ramp
  Router, distinct from `openrouter`, confirmed in `spec.md`'s research).
- `tests/unit/test_model_access.py` (new) — `classify()`'s six-way
  branching (each bucket, table-driven over crafted `(status, body)`
  pairs, including the attempt-vs-cap boundary for `Retry`/`Abort`);
  `send()`'s orchestration (`UnknownProvider`, `ProviderNotWired`,
  credential pre-flight short-circuit) with `request_fn` stubbed inline;
  registry discovery (`list_providers()` returns 39 names, each unwired
  provider's `request_fn is None`).
- `tests/unit/test_model_providers_openrouter.py` (new) — OpenRouter's
  `request_fn` against a monkeypatched `_post`, covering all six outcomes
  with realistic OpenRouter-shaped JSON bodies (a real 401 body, a real
  context-length-exceeded body shape, etc., drawn from what the proof
  script actually observes in Order-of-work step 6 — see Risks).
- `scripts/prove_model_access.py` (new) — standalone, no pytest, reads
  `OPENROUTER_API_KEY` from the real environment, makes one real `send()`
  call for each of the six outcomes (a normal prompt; a deliberately
  invalid key; a request with an absurdly long message history; a request
  naming a nonexistent model; a request with an artificially tiny
  `SADANA_MODEL_ACCESS_TIMEOUT_S`; a best-effort attempt at an empty
  completion — see Risks), printing each `Outcome` and the raw HTTP
  status/body it came from.

## Order of work

1. `Request`, the six `Outcome` dataclasses, `ProviderManifest`,
   `classify()` — pure data and pure functions, no I/O. Unit-test
   `classify()` fully (table-driven, every bucket, the retry/abort
   boundary at `max_retries`). Nothing here can break anything that exists
   today; nothing here depends on anything else in this list.
2. Registry: `register_provider`/`get_provider`/`list_providers`/
   `_discover()`, scanning `model_providers/`. Unit-test against a
   `tmp_path`-fixture directory of two or three fake provider dirs
   (per `testing-conventions`: real filesystem only under `tmp_path`) —
   not yet against the real 39, which don't exist until step 5.
3. OpenRouter's `request_fn` + the `_post` seam + `send()`'s orchestration
   (manifest lookup, `ProviderNotWired`, credential pre-flight, wiring
   `request_fn`'s result through `classify()`). Unit-test with `_post`
   monkeypatched — every outcome, plus the pre-flight short-circuit (env
   var unset → `NeedsCredentialOrProviderChange`, `_post` never called,
   asserted via a call-count check).
4. `context_window()` — one-entry table. Confirm the real deepseek/OpenRouter
   context-window number (OpenRouter's public `/models` catalog, or
   hermes's own cached value if visible in the reference corpus) rather
   than guessing it. Unit-test the hit and the raise-on-miss.
5. The 38 remaining provider manifests, copied mechanically from hermes.
   Unit-test `list_providers()` returns all 39 and every non-OpenRouter
   entry has `request_fn is None`.
6. `scripts/prove_model_access.py`, run once for real against live
   OpenRouter with a real `OPENROUTER_API_KEY`. This is the only step
   touching the network or a real credential, and it lands last — after
   steps 1-5 are already unit-tested and `make verify` is green — so if
   something breaks here, the failure is isolated to this one script and
   the real HTTP boundary, not to logic already proven offline. Its
   output is this work item's Deploy-stage evidence, not a pytest run.

## Risks

**What could this break?** Nothing pre-existing depends on this area yet —
`grep` for `model_access` and `model_providers` today returns nothing
outside `docs/tasks/`. The one shared surface this work item touches is
`sadana.config`'s `env_int`, already used by nothing else either; if
`env_int`'s behavior on an unset var were wrong this would surface
immediately in step 1/3's unit tests, not silently.

**Riskiest step, and why:** step 6, unambiguously — it is the only step
touching a real network boundary and a real credential, outside the
hermetic guarantee `scripts/run_tests.sh` gives every other step. Ordered
last on purpose (see Order of work). Two sub-risks inside it:

- Provoking `Degenerate` for real is the least reliable of the six.
  Hermes's own definition (`usage_present=True`, `output_tokens==0`,
  non-refusal `finish_reason`) depends on the live model's actual
  generation behavior, which nothing here controls deterministically. The
  script's best-effort approach (a stop sequence matching the first
  token, or `max_tokens` set to the smallest value the API accepts) may
  need a few manual attempts against the real API before one lands. If it
  genuinely can't be provoked within reasonable effort, that becomes a
  documented finding in Deploy's `review.md`, not a silently skipped
  acceptance criterion — `spec.md`'s acceptance criteria require this
  outcome demonstrated, and an honest "could not reproduce, here's what
  was tried" is the correct trace if it comes to that, not a fabricated
  log.
- `test_model_providers_openrouter.py` (step 3) is written with
  best-guess OpenRouter response shapes before step 6 ever runs live;
  step 6 may reveal the real shapes differ in some field. If so, step 6's
  finding gets folded back into step 3's fixtures before this work item
  is called done — not left as a known-inaccurate test.

**Drift back toward a rejected alternative?** Checked each of `spec.md`'s
six rejected alternatives against this plan: no traceback/frame-based
classification (this plan's `classify()` is pure status/body input, no
exception object touched at all); no shared-object streak state (no
mutable state anywhere in this plan outside the one-time registry
population — `classify()` and `send()` are both pure functions of their
arguments); no full 15-field `ProviderProfile` (confirmed 4-field
`ProviderManifest` above); no three-tier discovery (confirmed
bundled-only in step 2); no live models.dev subsystem (confirmed
one-entry table in step 4); no real network call inside the pytest suite
(confirmed steps 1-5 are hermetic, step 6 is a standalone script). Nothing
in this plan reinstates a rejected option.

## Proof

`make verify` green after every step from 1 through 5 — `chain`, `lint`,
`typecheck`, `test` all passing, `test` including the two new unit test
files above with no network/filesystem/wall-clock use outside `tmp_path`.

Specifically: `test_model_access.py` covers `classify()`'s six buckets
plus the attempt/cap boundary, `send()`'s `UnknownProvider`/
`ProviderNotWired`/credential-pre-flight paths, and registry discovery
against a fixture directory. `test_model_providers_openrouter.py` covers
OpenRouter's `request_fn` against a monkeypatched `_post` for all six
outcomes.

Step 6's `scripts/prove_model_access.py` run, executed once manually with
a real `OPENROUTER_API_KEY`, its full printed output (each outcome, the
real HTTP status and response body behind it) pasted verbatim — this is
the literal proof `intent.md`'s Proposed outcome asks for, and it becomes
`review.md`'s `## Evidence` at the Deploy stage, per the CLAUDE.md
amendment approved alongside `spec.md`.

## Open questions

None — the interrogation above resolved every branch point this plan
depends on. The one real unknown, whether `Degenerate` can be provoked
against the live API on the first try, is tracked as a named risk with a
fallback (an honest "could not reproduce" finding), not an open question
blocking the start of work.
