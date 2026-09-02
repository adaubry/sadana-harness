# Review: CONFIG as a contract, not a module (from plan.md 2026-09-03)

Reviewed: untracked new files, no base commit — 2 code files, +226/-0
(`src/sadana/config.py` +96, `tests/unit/test_config.py` +130); the three
artifact docs (`intent.md`, `spec.md`, `plan.md`) are already validated and
out of scope for this review's own edits.
Reviewer context: fresh session, no prior context beyond intent.md/spec.md/
plan.md and the diff.

## Evidence

```
docs/tasks/B1-config-contract: all present artifacts valid
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
Success: no issues found in 2 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................                                                 [100%]
24 passed in 0.09s
TESTS OK
VERIFY OK
```

## Scope

`git diff --stat HEAD` (files staged with `git add -N` to render untracked
content as a diff, then unstaged again) shows exactly `src/sadana/config.py`
(new, 96 lines) and `tests/unit/test_config.py` (new, 130 lines) as code
changes — matches `plan.md` § Files that change verbatim: both named files
present, no file the plan named is missing, no file outside the plan is
touched. No existing file was edited; `src/sadana/__init__.py` is untouched
and nothing else in `src/` or `tests/` imports `sadana.config` yet, matching
`plan.md` § Risks ("Nothing existing... breaks").

## Findings

Three passes run (bugs, security, compliance), plus the scope check above.
One Important finding, from the compliance pass. Detail below.

### Bugs pass
None found. `get_paths()`'s two-tier precedence chain
(`src/sadana/config.py:91-96`) matches `plan.md`'s pseudocode
(`plan.md:46-47`) exactly: `SADANA_STATE_DIR` wins outright, else
`$XDG_STATE_HOME/sadana`, else `~/.local/state/sadana`; `config_dir` follows
the same two-tier shape against `XDG_CONFIG_HOME`. `state_default` is built
before it's passed as `env_path`'s `default=`, so it's evaluated whether or
not `SADANA_STATE_DIR` is set — eager rather than lazy, but has no
observable effect (no side effects, cheap `Path` arithmetic) and is not a
defect. `env_bool`'s token set and `env_int`'s `int()` parsing are
straightforward and match their docstrings. No off-by-one, no swapped
branch, no mutable default argument.

### Security pass
No secrets, credentials, or PII in the diff (`Detect secrets` and
`detect private key` both pass in Evidence). `env`/`env_bool`/`env_int`/
`env_path` read only from `os.environ` and raise or return — no subprocess,
no file write, no network call, nothing that widens what the process can
reach. `ValueError` messages interpolate the raw env-var value
(`config.py:50`, `:64`) — acceptable here since these are operational
config values (timeouts, toggles, paths), and secret storage is explicitly
out of scope per `intent.md` § Constraints and `spec.md` requirement 6.
Nothing here stores, transmits, or rotates a secret. Clean.

### Compliance pass

**`spec.md` § Acceptance criteria** — checked item by item:

- Exactly six exported names — verified by grepping top-level `def`/`class`
  in `config.py`: `env`, `env_bool`, `env_int`, `env_path`, `Paths`,
  `get_paths`, nothing else. **Satisfied.**
- `Paths` frozen — `test_paths_is_frozen`, `test_config.py:81-85`.
  **Satisfied.**
- `get_paths()` no caching — `test_get_paths_does_not_cache`,
  `test_config.py:123-130`. **Satisfied.**
- `SADANA_STATE_DIR`/`XDG_STATE_HOME`/home-fallback tiers, and the same
  shape for `config_dir` — `test_config.py:88-120`, all four tiers covered.
  **Satisfied.**
- `env_bool`/`env_int`/`env_path`: set-valid → parsed, unset → default,
  set-invalid → raises `ValueError` (`spec.md:194-196`) — `env_bool` and
  `env_int` each have all three cases (`test_config.py:25-46`,
  `:49-65`). `env_path` has only two of the three: unset → default
  (`test_config.py:68-72`) and set-valid → parsed (`:75-78`). There is no
  set-invalid → raises test for `env_path`, and there cannot be one against
  the current implementation (`config.py:67-72`) — `pathlib.Path(raw)`
  accepts essentially any string, so `env_path` has no failure mode to
  raise on. See Important finding below. **Partially satisfied.**
- No import from any other `sadana` module — confirmed by reading
  `config.py:24-28`: only `__future__`, `os`, `dataclasses`, `pathlib`.
  **Satisfied.**
- `tests/unit/test_config.py` exists, named per testing-conventions
  (`foo.py` → `test_foo.py`), every test tagged `@pytest.mark.unit`, all
  assertions are invariants (env-var-in/value-out relationships) not
  literal-path snapshots — e.g. `test_get_paths_falls_back_to_home_when_
  nothing_set` asserts against `Path.home()` at test time, not a hardcoded
  string. **Satisfied.**
- `make verify` ends `VERIFY OK` — see Evidence. **Satisfied.**

**`plan.md` § Proof** — three of four items discharged as claimed; the
fourth is only partly discharged:

- Per-primitive unset/set-valid/set-invalid assertions
  (`plan.md:88-91`) — discharged for `env`, `env_bool`, `env_int`; **not**
  discharged for `env_path`'s set-invalid case, for the reason above. The
  plan's own Proof section claims this case is covered ("per primitive
  (`env_bool`, `env_int`, `env_path`): ... set-invalid → raises
  `ValueError`") but the shipped test file has no such test, and none is
  possible against the shipped implementation.
- `get_paths()` precedence-tier assertions — discharged,
  `test_config.py:88-120`.
- `Paths` frozen assertion — discharged, `test_config.py:81-85`.
- `make verify` output pasted in full ending `VERIFY OK` — discharged, see
  Evidence.

**`spec.md` § Rejected alternatives — checked for drift:** no growing
shared `Config` object (only the two named `Paths` fields exist); no
central registry; no bare module-level singleton (access is through the
`get_paths()` function, not a `CONFIG` attribute); no dict-based config (a
frozen dataclass); no mtime cache (`get_paths()` rebuilds from `os.environ`
every call, confirmed by `test_get_paths_does_not_cache`); no
fail-safe-on-bad-value coercion copied from `gateway/config.py`'s
`_coerce_bool` (`env_bool`/`env_int` raise `ValueError` on an unrecognized
value, `config.py:50`, `:64` — the opposite of hermes's silent fallback).
No drift found.

**Design principles (spec.md § Design):**

1. *Learn from reference first* — `plan.md` § Context records reading
   `gateway/config.py:31-42`/`:287-320` and `hermes_constants.py:113-140`
   directly (beyond the fork research already in spec.md), and states what
   was declined and why (silent fallback on bad value). Applied.
2. *Reduce number of bets* — no registry, no registration order, nothing
   composed at import time; matches spec's stated design. `env_bool`/
   `env_int` ship with no call site inside this work item — spec's own
   Concerns section names this explicitly as a deliberate, on-the-record
   exception, not an oversight; I checked the reasoning (repo-wide future
   scaling was named in scope by the user in intent.md's amendment) and
   find it consistent with what intent.md actually says. Not re-raising it
   as a new finding.
3. *More plugins, not more core* — not applicable; no plugin seam exists
   yet and none is invented here. Consistent with spec's stated position.
4. *Least step-cost* — the four primitives are added once, here, and every
   future block reuses them rather than each writing its own
   `os.environ.get` parsing (spec.md's own example of what this avoids:
   `gateway/config.py`'s duplicate `_coerce_bool`/`_coerce_int`). Applied.
5. *Minimise mutable state* — zero stored state confirmed by reading the
   whole file: no module-level cache, no class with mutable fields,
   `get_paths()` recomputes from `os.environ` on every call. Applied.

### Important

- **`env_path`'s "set-and-invalid raises `ValueError`" case, required by
  `spec.md:194-196` and claimed discharged by `plan.md:88-91`, is neither
  implemented nor tested — and cannot be, against a plain
  `Path(raw)` construction (`src/sadana/config.py:67-72`,
  `tests/unit/test_config.py:68-78`).** `pathlib.Path()` does not reject
  malformed input the way `int()`/the recognized-token check in `env_bool`
  do; essentially any string constructs a `Path`. Right now the artifact
  chain asserts a proof that the code does not, and cannot, provide.

  **Resolved:** Adam chose option (a) — `spec.md`'s Interface and
  Acceptance criteria and `plan.md`'s Proof are amended to drop `env_path`
  from the "set-invalid raises" case, matching `env`'s existing no-raise
  behavior and `env_path`'s own docstring (which already had no "Raises"
  line, unlike `env_bool`'s and `env_int`'s). No code change; `config.py`
  and `test_config.py` are unchanged and already match the amended spec and
  plan.

### Nits

- `config.py:91-92` computes `state_default` and `config_dir` with
  different local structure (one named intermediate, one inline chained
  call) for what is otherwise the same two-tier pattern — purely
  stylistic, doesn't affect either correctness or the acceptance criteria.

## Decision
Approved by Adam, 2026-09-03, with the Important finding resolved by
amending `spec.md` and `plan.md` (option (a) — see Findings above); no code
change required.
