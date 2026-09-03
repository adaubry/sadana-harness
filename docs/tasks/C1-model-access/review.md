# Review: MODEL-ACCESS proves one provider before it names the rest (from plan.md 2026-09-03)

Reviewed: a0c3109b47d1478e40d7ca185a88c72d9e964735..HEAD (staged, uncommitted) — 48 files, +2391/-0
Reviewer context: same session as build for triage and fixes, but the primary
compliance/bugs/security pass was delegated to a fresh general-purpose
subagent with no context beyond the diff and the three artifacts — the
closest approximation to "review cold" available without a second human.
That subagent's findings are reproduced below in my own words, per this
skill's instruction not to paste command output verbatim.
Second opinion: `ponytail-review` ran (this session, against the diff after
the cold pass); `/simplify` is not installed in this project (only the
`ponytail` plugin is enabled — `ponytail-review`, `ponytail-audit`,
`ponytail-debt`, `ponytail-gain` — no generic `/simplify` command exists to
run). Noted as a stated limitation of this review rather than skipped
silently.

## Evidence

```
$ make verify
docs/tasks/C1-model-access: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
LINT OK
Success: no issues found in 3 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................                 [100%]
56 passed in 0.20s
TESTS OK
VERIFY OK
```

`scripts/prove_model_access.py`, run manually against live OpenRouter with a
real `OPENROUTER_API_KEY` (supplied via a local `.env`, sourced into the
shell — never read or echoed by me, per the project's own deny-rule on
reading `.env` files):

```
=== response (normal prompt) ===
Response(content='pong', tool_calls=(), finish_reason='stop', usage=Usage(prompt_tokens=90, completion_tokens=13))

=== needs-credential-or-provider-change (env var unset, zero network) ===
NeedsCredentialOrProviderChange(detail='missing env var(s): OPENROUTER_API_KEY')

=== needs-credential-or-provider-change (real 401 round trip) ===
NeedsCredentialOrProviderChange(detail='User not found.')

=== retry (deliberately tiny deadline against the real network) ===
Retry(next_attempt=1)

=== needs-context-compression (oversized request) ===
NeedsContextCompression(detail="This endpoint's maximum context length is 1310720 tokens. However, you requested about 3000000 tokens (3000000 of text input). Please reduce the length of either one, or use the context-compression plugin to compress your prompt automatically.")

=== abort (unknown model) ===
Abort(detail='bogus/does-not-exist is not a valid model ID')

=== degenerate (best-effort — models often refuse to comply; see plan.md Risks) ===
Response(content=None, tool_calls=(), finish_reason='stop', usage=Usage(prompt_tokens=126, completion_tokens=34))
```

## Findings

Three passes run (bugs, security, compliance) by a fresh cold-review
subagent, plus a `ponytail-review` complexity pass run afterward in this
session. Bugs pass: none found — `classify()` traced through every branch
against the unit tests, `send()`'s credential pre-flight correctly
short-circuits before any network call. Security pass: clean — the API key
only ever appears in the `Authorization` header, never echoed into a
returned body, `Outcome`, or log; no secrets in source. Two Important
compliance findings, both already fixed in this branch; details below.

### Important

- [Compliance] `plan.md`'s original `## Files that change` named every
  provider manifest as `<name>/__init__.py`; the actual diff uses
  `<name>/provider.py` throughout (discovered mid-build: hyphenated
  provider-directory names collide under mypy's package-name derivation
  when the file is `__init__.py`). The cold-review subagent flagged this as
  an undocumented artifact/code mismatch. **Fixed**: `plan.md` amended with
  an explicit "Amended post-implementation" note explaining the discovery
  and correction, and `pyproject.toml` (the mypy-exclude fix) added to the
  file list it was missing from.
- [Compliance] `plan.md`'s Order-of-work step 2 and `## Proof` promised a
  `tmp_path`-fixture test exercising `_discover()`'s own scan/import logic
  (missing-manifest skip, underscore-prefixed skip) directly; no such test
  existed — discovery was only exercised indirectly through the real
  39-provider tree. **Fixed**: added
  `test_discover_scans_a_fixture_directory` to `tests/unit/test_model_access.py`,
  using `monkeypatch.setattr` on `_PROVIDERS_DIR`/`_REGISTRY`/`_discovered`
  so it never touches the real registry (`monkeypatch` restores all three
  automatically at test end — no bespoke reset fixture needed, consistent
  with `test_config.py`'s existing pattern).

### Nits

- [Compliance] `model_access.py`'s module docstring said providers register
  via `__init__.py` (stale after the `provider.py` rename); the accurate
  explanation was 190 lines below, in `_discover()`'s own docstring.
  **Fixed**: corrected the module docstring's one reference.
- [Compliance] The build plan promised unit tests would monkeypatch the
  provider's own `_post` function; the actual tests monkeypatch
  `urllib.request.urlopen` globally instead, with a documented reason in
  the test file's own module docstring — the provider file is imported
  twice under different module names (a normal `import` in the test vs.
  `_discover()`'s dynamic `spec_from_file_location` import), so patching
  module-local `_post` risks silently missing whichever instance actually
  got registered. Functionally sound; left as-is rather than forcing the
  plan's exact original wording.
- [Bugs, found by `ponytail-review`] `classify()`'s transient-check carried
  a dead `status is not None and` guard — unreachable false state, since
  the two preceding `or` clauses already exclude `status is None` by the
  time that clause runs. **Fixed** (`model_access.py`).
- [Bugs, found by `ponytail-review`] `openrouter/provider.py`'s exception
  handling listed `TimeoutError` and `urllib.error.URLError` alongside
  `OSError` (both are already `OSError` subclasses) and
  `json.JSONDecodeError` alongside `ValueError` (already a subclass) —
  redundant tuples. **Fixed**, along with a stray function-local `import os`
  moved to the top-level import block and a `context_window()` try/except
  that only reworded the `KeyError` it was wrapping, collapsed to one line.

### Raised, not findings

- The cold-review subagent's file list is 39 near-identical ~17-line
  manifest files (646 lines total) where a single data table (~40 lines)
  could represent the 38 unwired providers' identity. I checked this
  against `spec.md` § Design before treating it as a finding: the
  directory-per-provider shape is a deliberate choice, not an oversight —
  it mirrors hermes's own registration mechanism (`providers/__init__.py`),
  keeps every provider's addition independent of every other (guideline 2:
  addition, not modification), and is the same shape the one wired
  provider (`openrouter`) already has to use for its real `request_fn`. A
  data-table alternative for the 38 unwired ones only would split the
  mechanism in two rather than keep one. Not changed.
- `Degenerate` could not be provoked against the live model despite three
  attempts with different prompts (see Evidence above and `plan.md` §
  Risks, which named this as the most likely gap going in). The model
  reliably produces real completion tokens even when explicitly instructed
  to emit nothing — `usage.completion_tokens` was 13–47 across every
  attempt, never 0. This is not a code defect: `classify()`'s definition
  (adopted from hermes's own `empty_response_guard.py`) is exercised and
  correct in the unit tests (`test_empty_completion_is_degenerate`,
  `test_classify_empty_completion_is_degenerate`) against a crafted
  response; it simply never fired against the real model in this session.
  Recorded here as the honest limitation `plan.md` anticipated, not
  papered over.
- This work item's Test stage surfaced that sadana-harness has no `.env`
  loader — `.env` is gitignored but nothing parses it; `config.py` only
  wraps `os.environ`. The user supplied `OPENROUTER_API_KEY` via `.env` and
  I sourced it into the shell manually (`set -a; source .env; set +a`)
  to run the proof script. Out of scope for C1 by `spec.md`'s own
  Constraints; the user asked to debrief this after this deploy stage
  rather than open a work item now — flagging it here so the debrief has
  something concrete to point at.

## Decision

Approved by Adam, 2026-09-03.
