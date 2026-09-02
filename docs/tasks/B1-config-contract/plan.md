# Plan: CONFIG as a contract, not a module (from intent.md 2026-09-03)

## Context

`spec.md` for B1-config-contract defines `sadana/config.py` as two things:
the only genuinely global bootstrap values (`Paths` / `get_paths()`, i.e.
`state_dir` + `config_dir`), and four reusable, stdlib-only resolution
primitives (`env`, `env_bool`, `env_int`, `env_path`) that every future
block will use to build its *own* config file, so this file never grows
again after this work item. This is the first real code in `src/sadana/` —
everything here is additive, nothing existing calls into it yet.

Prior art consulted (per build-skill step 2, beyond the fork research
already in spec.md's Design section): read `gateway/config.py:31-42`
(`_coerce_bool`) and `:287-320` (`_getenv`/`_getenv_str`/`_getenv_int`)
directly. `_coerce_bool` silently falls back to its default on an
unrecognized string instead of raising — **declined**, matches spec's
explicit rejection of hermes's fail-safe-by-default coercion in favor of
failing loudly on a malformed value. Also read `hermes_constants.py:113-140`
(`get_hermes_home`): a three-tier override → env var → platform-default
chain, whose own docstring says raising on a bad value "would brick 30+
module-level callers that import this at load time" — direct evidence for
why hermes couldn't afford to fail loudly: its singleton-style callers
resolve config at import time, scattered everywhere. Our primitives are
called explicitly inside each block's own accessor function, never at
module import time, so raising is safe here in a way it wasn't for hermes.

## Files that change

- `src/sadana/config.py` (new) — `Paths` dataclass, `get_paths()`, `env`,
  `env_bool`, `env_int`, `env_path`.
- `tests/unit/test_config.py` (new) — covers both.

## Order of work

1. Implement the four primitives (`env`, `env_bool`, `env_int`, `env_path`)
   in `src/sadana/config.py` — pure functions over `os.environ`, no
   dependents yet within the file. Each: env var unset → return `default`;
   set and parseable → return parsed value; set and unparseable (typed
   variants only) → raise `ValueError`.
2. Add the matching cases to `tests/unit/test_config.py` for step 1 and run
   the narrow test command before continuing — the primitives are the
   foundation everything else composes on top of.
3. Implement `Paths` (frozen dataclass: `state_dir: Path`, `config_dir:
   Path`) and `get_paths()`, built from `env_path` (step 1):
   - `state_dir` = `env_path("SADANA_STATE_DIR", default=env_path("XDG_STATE_HOME", default=Path.home()/".local"/"state") / "sadana")`
   - `config_dir` = `env_path("XDG_CONFIG_HOME", default=Path.home()/".config") / "sadana"`
   No caching — `get_paths()` recomputes from `os.environ` on every call.
4. Add the precedence-tier cases to `tests/unit/test_config.py` for step 3:
   each env var set; only the XDG-level var set; neither set (hardcoded
   fallback); frozen-ness; no-caching (two calls around a changed env var
   differ).
5. `make verify` — chain, lint, typecheck, test, all green.

This is deliberately ordered so the riskier piece (step 3/4, the two-level
precedence chain) lands only after the primitives it's built from are
already proven in isolation (step 1/2) — see Risks.

## Risks

**What could this break?** Nothing existing — `src/sadana/` currently has
only `__init__.py`, and no test besides the new one imports `sadana.config`.
The one thing this change must respect rather than break is
`tests/conftest.py`'s autouse `_isolated_state` fixture, which already sets
`HOME`, `XDG_STATE_HOME`, `XDG_CONFIG_HOME`, and `SADANA_STATE_DIR` for
every test. Testing the "XDG var also unset" fallback tier means explicitly
`monkeypatch.delenv`-ing what the fixture set, inside that one test, not
assuming a blank slate.

**Which step is riskiest?** Step 3, the `state_dir`/`config_dir` precedence
chain — it nests two `env_path` calls and depends on `Path.home()` /
`expanduser` behavior, which has more edge-case surface than the flat
primitives from step 1. Mitigated by ordering: primitives are fully tested
before `Paths` is built on top of them, so a failure in step 3/4 is
isolated to the composition, not the underlying resolution logic.

**Drift check against `spec.md`'s Rejected alternatives.** Re-read before
writing code: no shared growing `Config` object (only the two named
bootstrap fields exist); no central registry; no bare module-level
singleton (access is through `get_paths()`, a function); no dict-based
config (a frozen dataclass); no mtime cache (recomputed every call). The one
rejected pattern easiest to drift back into by habit is hermes's
fail-safe-on-bad-value coercion (`gateway/config.py`'s `_coerce_bool`,
confirmed above) — the typed primitives must raise `ValueError` on a
set-but-invalid value, not silently return the default.

## Proof

- `tests/unit/test_config.py` asserts, per primitive: unset → default,
  set-valid → parsed value. `env_bool` and `env_int` additionally assert
  set-invalid → raises `ValueError`. `env` and `env_path` have no invalid
  case to assert (any string is a valid string; `pathlib.Path` accepts
  essentially any string).
- Asserts `get_paths().state_dir` for: `SADANA_STATE_DIR` set (wins
  outright); unset with `XDG_STATE_HOME` set (`<XDG_STATE_HOME>/sadana`);
  both unset (`~/.local/state/sadana` under the test's patched `HOME`).
  Same shape for `config_dir` against `XDG_CONFIG_HOME`.
- Asserts `Paths` is frozen (`dataclasses.FrozenInstanceError` or equivalent
  on attribute assignment).
- Asserts two `get_paths()` calls straddling a `monkeypatch.setenv` differ
  (no caching).
- `make verify` output pasted in full, ending `VERIFY OK`.
