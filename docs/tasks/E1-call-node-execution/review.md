# Review: EXECUTION — what a call step may do (from plan.md 2026-09-07)

Reviewed: HEAD..working tree (all changes uncommitted; there is no
`<base>..HEAD` range yet) — 7 files, +530/-1 (`CLAUDE.md`,
`docs/tasks/E1-call-node-execution/{intent,spec,plan}.md`,
`src/sadana/execution.py`, `tests/unit/test_execution.py`,
`scripts/prove_execution.py`).
Reviewer context: same session as build for the initial diff — but the
bugs/security/compliance review itself was **delegated to a fresh subagent
with no context beyond the diff and the three artifacts**, per this stage's
own rule about reviewing cold. Its findings were independently reproduced
against the actual running code before being trusted (see `## Findings`).
Four Important findings were then fixed in this same pass; the fix's own
re-verification evidence is below.
Second opinion: none beyond the cold-review delegation above — the
build-stage self-check (`/ponytail-review` + `/simplify`) already ran
during build, per this project's `SDLC-second-opinion-timing` decision, and
is not repeated here by design.

## Evidence

Final state, after the four Important findings below were fixed:

```
$ make verify
docs/tasks/E1-call-node-execution: all present artifacts valid
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
shellcheck.................................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 12 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 22%]
........................................................................ [ 44%]
........................................................................ [ 66%]
........................................................................ [ 88%]
....................................                                     [100%]
324 passed in 3.03s
TESTS OK
VERIFY OK
```

Note: an earlier run of this same command reported `VERIFY OK` while the
new files were still `git`-untracked, which silently skips them from
`pre-commit --all-files`'s lint pass — a known project gap. The run above
is against the actually-tracked tree (`git add -N`, plus a real `git add`
for `scripts/prove_execution.py` so its executable bit is recorded); it is
the one that counts as evidence.

**Live-network evidence** (`scripts/prove_execution.py`), re-run after the
fixes, using the real network — unaffected by the fixes, since neither
touches the `http/https` path:

```
=== success (real GET against example.com) ===
Success(status=200, body=b'<!doctype html>...Example Domain...')

=== failure (real DNS resolution failure) ===
Failure(detail='<urlopen error [Errno -2] Name or service not known>')

both scenarios matched their expected Outcome type.
```

**Direct repro of the four fixed defects**, before and after
(`run_http` called directly, no test framework):

```
before:
1. no-scheme:  RAISED ValueError: unknown url type: 'example.com'
2. file://  :  Success(status=None, body=b'root:x:0:0:...')   # read a real local file
3. CRLF hdr :  RAISED ValueError: Invalid header value b'bar\r\nX-Injected: evil'

after:
1. no-scheme: Failure(detail="unsupported URL scheme: 'example.com' (only http/https are allowed)")
2. file://  : Failure(detail="unsupported URL scheme: 'file:///etc/hostname' (only http/https are allowed)")
3. CRLF hdr : Failure(detail="Invalid header value b'bar\\r\\nX-Injected: evil'")
```

## Findings

Cold review (delegated to a fresh subagent, no context beyond the diff and
`intent.md`/`spec.md`/`plan.md`) ran the bugs/security/compliance passes
and independently verified `make test`/`make lint`/`make typecheck` and
re-ran `scripts/prove_execution.py` itself before trusting the pasted
evidence. Its findings were reproduced a second time, independently, before
being accepted below.

### Important

- **[Bugs, fixed]** `run_http()` raised an uncaught `ValueError` — not
  `Failure` — for a URL with no scheme (`src/sadana/execution.py`, in the
  `urlopen()` call). Confirmed live: `run_http(HttpRequest(method="GET",
  url="example.com"))` raised `ValueError: unknown url type: 'example.com'`
  straight out of the function, a direct violation of spec.md Requirement 2
  ("the function never raises... nothing propagates past `run_http()`").
  The unit suite never caught this because it monkeypatches
  `urllib.request.urlopen` wholesale, bypassing the real scheme-dispatch
  logic where this actually originates. **Fixed**: an explicit
  `http://`/`https://` scheme check runs before anything else, returning
  `Failure` immediately — re-verified above (`before`/`after`, case 1) and
  covered by a new test,
  `test_malformed_url_from_urlopen_is_failure`.

- **[Security/Bugs, fixed]** `run_http()` accepted any URL scheme urllib
  understands, including `file://`, with no allow-list
  (`src/sadana/execution.py`, the `urllib.request.Request(...)` call built
  from the caller's URL verbatim). Confirmed live:
  `run_http(HttpRequest(method="GET", url="file:///etc/passwd"))` returned
  `Success(status=None, body=b'root:x:0:0:...')` — it read and returned
  local filesystem contents through the sanctioned, intended call path,
  framed as an ordinary success. This directly undercuts intent.md's own
  Proposed outcome ("what it is allowed to touch is bounded and inspectable
  rather than arbitrary") for the sanctioned path itself — not merely the
  already-acknowledged bypass-via-calling-`urllib`-directly that spec.md's
  Concerns section discusses. Scoped honestly: today's only callers are
  first-party per intent.md, so this was not exploitable by an outside
  actor yet — but this is the exact function PLUGINS' next work item wires
  directly to a plugin's declared `call` step, so leaving it unbounded
  would have surfaced later as a harder-to-trace bug rather than a design
  decision made now. **Fixed**: the same scheme check above rejects
  anything but `http://`/`https://` before a request is ever constructed —
  re-verified above (`before`/`after`, case 2) and covered by a new test,
  `test_unsupported_scheme_is_failure_without_ever_calling_urlopen`, which
  also asserts `urlopen` is never reached (a tripwire, matching
  `test_model_providers_openrouter.py`'s own pattern for the equivalent
  check).

- **[Bugs, fixed]** A control character (e.g. a stray `\r\n`) in a header
  name or value raised an uncaught `ValueError` from inside `urlopen()`
  rather than returning `Failure` (`src/sadana/execution.py`). Confirmed
  live: `headers={"X-Foo": "bar\r\nX-Injected: evil"}` raised `ValueError:
  Invalid header value ...` straight out of `run_http`. Not an exploitable
  injection — CPython's own `http.client` blocks the actual header split
  before anything reaches the wire — but still a "never raises" contract
  violation on realistic input (any caller building a header from a value
  that happens to contain a stray newline, not necessarily malicious).
  **Fixed**: `except (OSError, ValueError)` now catches this alongside the
  existing connection-failure case — re-verified above (`before`/`after`,
  case 3) and covered by a new test,
  `test_malformed_url_from_urlopen_is_failure` (renamed from a
  URL-specific name since it now documents the general "urlopen raised
  ValueError" contract, which both the header case and any other urllib
  internal validation failure share).

- **[Compliance, fixed]** The `Failure` built from an `HTTPError` never
  read the response body, contradicting spec.md's own Design prose
  (`src/sadana/execution.py`, was `Failure(f"HTTP {exc.code}:
  {exc.reason}")`). spec.md § Design says the failure "becomes `Failure`
  carrying the response status and *whatever one-line message the body
  yields*," but `exc.reason` is only the HTTP status-line phrase (e.g.
  "Not Found"), never body content — so a caller hitting, say, a rate-limit
  endpoint returning `{"error": "quota exceeded, retry after 30s"}` lost
  that diagnostic entirely, getting back only `"HTTP 429: Too Many
  Requests"`. This also diverged from `model_providers/openrouter/
  provider.py:_post()`, which the module's own docstring claims to mirror
  "almost exactly" and which does extract body content on failure. The
  existing test didn't catch this because its fixture body was empty
  either way. **Fixed**: `run_http` now reads and decodes `exc.read()`,
  falling back to `exc.reason` only when the body is empty, truncated to
  200 chars to keep the "one safe line" property spec.md's own Interface
  section asks for. Covered by a new test,
  `test_http_error_detail_reads_the_response_body`. The generic,
  non-JSON-specific read (rather than porting `_post()`'s JSON-message
  extraction) is a deliberate, noted divergence: this module is
  HTTP-generic, not chat-completions-specific, so JSON parsing wouldn't
  generalize the way it does for `_post()`'s one fixed endpoint shape.

- **[Compliance, fixed]** `plan.md` § Files that change said "No other
  file changes," but the diff also carries one new line in `CLAUDE.md` —
  the "registry seam for a family of one" rule spec.md's own Rejected
  alternatives argued for. This is exactly the class of drift this stage's
  own compliance pass exists to catch (a file touched that the plan didn't
  name), reported plainly rather than waved through. Not a code defect —
  the `CLAUDE.md` line itself was added during the design stage, before
  this plan existed, and approved by the user separately from this
  implementation. **Fixed**: `plan.md` now says so explicitly instead of
  claiming no other file changed.

### Nits

- `run_http()` passes `request.method` to `urllib.request.Request`
  unchecked; an empty string silently produces a `GET`-shaped request
  rather than failing or defaulting explicitly. Low-stakes today (only
  first-party callers exist), not fixed — no test asks for this and
  fixing it isn't implied by any spec.md requirement.

## Compliance pass detail

- **`plan.md` § Proof**: `tests/unit/test_execution.py` — discharged,
  `make test` shows 324 passed (up from 321 pre-fix; net +3 after removing
  one redundant test during build and adding three covering the fixes
  above). `scripts/prove_execution.py` output — discharged, reproduced
  live above, post-fix. `make verify` ending `VERIFY OK` — discharged,
  pasted above against the fully-tracked tree.
- **`spec.md` § Acceptance criteria**: all five checked directly — the
  module exists and `run_http()` is callable with no plugin/DAG/catalog
  machinery in its imports; the unit test covers 2xx/HTTPError/OSError all
  mapping correctly, plus (after this review) the scheme-rejection and
  malformed-input cases, and none raise; the live-proof script exists and
  its output was reproduced above; `pyproject.toml` has no new dependency
  (confirmed via `git diff HEAD` — no hunk for it); `make verify` ends
  `VERIFY OK`.
- **`spec.md` § Rejected alternatives drift check**: confirmed clean — no
  dict-keyed registry or `execute(backend, request)` dispatcher, no
  `httpx` or other HTTP client import, no shared base class. The scheme
  allow-list added by this review's fix is a bound on the one function
  that exists, not a second backend or a registry — it doesn't reopen any
  declined alternative.
- **Files touched vs `plan.md` § Files that change**: `CLAUDE.md` was
  touched and not originally named — see the fixed Important finding
  above; `plan.md` now accounts for it.
- **Five design principles**: reference-corpus-first (spec.md's citations
  of `tools/environments/` and `_create_environment()` stand, unaffected by
  the fixes); reduce the number of bets (still true — the scheme
  allow-list is a one-line bound, not a new decision surface); more
  plugins not more core (unaffected — this module still has zero plugin
  callers); least step-cost (the fixes made an existing step, `run_http`'s
  own exception handling, catch two more real cases rather than adding a
  new step or a heavier one); minimise mutable state (unaffected — no new
  state introduced by any fix).

## Decision

Approved by Adam, 2026-09-07, with the five Important findings (the four
`run_http()` defects — no scheme validation, no `Failure` on a
scheme/header `ValueError`, and an unread `HTTPError` body — plus
`plan.md`'s file-list omission) fixed in this branch before merge, and
`plan.md` corrected in place to match reality.
