# Plan: D2 manifest validation (from intent.md 2026-09-07)

## Context

`docs/tasks/D2-manifest-validation/intent.md` and `spec.md` are approved.
D2 lets the system read a plugin's `plugin.toml` and say, before running a
single step of it, whether the description is internally coherent — seven
named checks (`plugin_blueprint.md §8`), each returning a distinct,
classified failure rather than an uncaught exception.

Two things this plan carries forward exactly as spec.md fixed them:
- `plugins.py` stays pure (no I/O); everything that touches a real file goes
  in the new `plugin_manifest.py`, per CLAUDE.md's I/O-module rule.
- Skill-loading (`SkillRef`, `_plugins_root`, `_skill_path`, `load_skill`,
  `SkillLoadError`, `_parse_skill_md`) relocates out of `conversation.py`,
  where it was built ahead of its real owner, into `plugins.py`/
  `plugin_manifest.py` along that same pure/I-O line — reusing it for check
  3 (`ask` nodes resolve), not rebuilding a second copy.

## Files that change

- **`src/sadana/plugins.py`** (existing, pure module) — gains `SkillRef`,
  `SkillLoadError`, `_parse_skill_md`, `_skill_path`, `_plugins_root`
  (relocated from `conversation.py`, logic unchanged, stale "zero runtime
  dependencies" docstring line corrected in the move), plus new `Entry`,
  `Node`, `Manifest`, and the 8-member `ManifestOutcome` union (`Valid`,
  `ManifestParseError`, `InvalidSchema`, `UnresolvedSkill`,
  `DuplicateNodeName`, `DanglingTarget`, `UnresolvedBody`,
  `UnreachableNode`). New imports needed: `Path`, `from sadana import
  config`.
- **`src/sadana/plugin_manifest.py`** (new, I/O module) — `load_skill`
  (relocated from `conversation.py`, the `path.exists()`/`path.read_text()`
  half) and new `validate(plugin_dir: Path) -> plugins.ManifestOutcome`,
  the seven ordered fail-fast checks.
- **`src/sadana/conversation.py`** — removes the six relocated names; adds
  `plugin_manifest` to the `from sadana import config, context, model_access,
  plugins` line (26); `run_child`'s call (was line 1365) becomes
  `plugin_manifest.load_skill(spec.skill)`; `ChildSpec.skill`'s type (line
  1304) becomes `plugins.SkillRef`; `run_child`'s docstring reference to
  `SkillLoadError`/`load_skill` (line 1348) is updated to name
  `plugin_manifest.load_skill`.
- **`pyproject.toml`** — `dependencies = []` (line 10) becomes
  `dependencies = ["jsonschema"]`. `jsonschema` also gets `pip install`ed
  into `.venv` — confirmed not present today (`ModuleNotFoundError` on
  `import jsonschema`), and nothing in this repo installs declared deps
  automatically.
- **`tests/unit/test_conversation.py`** — import list (lines 28-29, 53)
  drops `SkillLoadError`, `SkillRef`, `load_skill`; call sites use
  `plugins.SkillRef` via the module already imported here. The 7
  `test_load_skill_*` tests (1461-1515) move out. `_write_skill`/
  `_install_skill` (1437-1458) **stay** — `run_child`'s own tests (1590+)
  still call `_install_skill`.
- **`tests/unit/test_plugin_manifest.py`** (new) — the 7 relocated
  `test_load_skill_*` tests (bodies unchanged, now calling
  `plugin_manifest.load_skill`, importing `SkillRef`/`SkillLoadError` from
  `sadana.plugins`), with their own small `_write_skill` helper (a ~15-line
  duplicate of test_conversation.py's — not worth sharing across two test
  files for this); plus one test per `§8` check for `validate()`, hand-built
  `tmp_path` plugin directories, per `testing-conventions`.
- **`tests/unit/test_plugins.py`** — adds tests for `Entry`, `Node`,
  `Manifest` and the 8 `ManifestOutcome` members, following this file's
  existing per-class triad (constructs-with-fields, frozen, +
  behavior-specific where relevant).

## Order of work

1. **Relocate the six existing names, prove nothing broke.** Pure move: no
   new behavior. This is the one step touching code with a real existing
   caller (`run_child`), so it goes first and gets its own gate before any
   new logic is written on top of it.
   Narrow check: `bash scripts/run_tests.sh tests/unit/test_conversation.py
   tests/unit/test_plugin_manifest.py tests/unit/test_plugins.py` and `mypy
   src`.
2. **Add jsonschema.** `pip install jsonschema` into `.venv`; add to
   `pyproject.toml`. No code yet — just confirm `import jsonschema` works
   under the project's interpreter before anything depends on it.
3. **Add the pure data shapes to `plugins.py`.** `Entry`, `Node`,
   `Manifest`, `ManifestOutcome` (all 8 members), matching spec.md's Design
   section exactly. Tests in `test_plugins.py`.
   Narrow check: `bash scripts/run_tests.sh tests/unit/test_plugins.py` and
   `mypy src`.
4. **`validate()`'s first four checks** (parses; schema files exist and are
   valid JSON Schema; `ask` skills resolve; node names unique), fail-fast.
   One test per check.
   Narrow check: `bash scripts/run_tests.sh tests/unit/test_plugin_manifest.py`
   and `mypy src`.
5. **The remaining three checks plus `Valid`** (edges target declared
   nodes; bodies resolve via `importlib.util.spec_from_file_location`,
   mirroring `model_access._discover()`'s pattern; reachability via BFS over
   `next`/`ports` from every entry). This is the step with the most new
   logic and where the exact check order (parse → schema → skill → names →
   edges → bodies → reachability, each assuming the previous held) matters
   most.
   Narrow check: `bash scripts/run_tests.sh tests/unit/test_plugin_manifest.py`
   and `mypy src`.
6. **Self-check and verify.** `/ponytail-review` and `/simplify` against
   the diff; take what's worth taking now. Run `make verify`, paste output.

## Risks

- **What could this break?** Grep confirms the only callers of the six
  relocated names, anywhere in `src/` or `tests/`, are `conversation.py`'s
  `run_child` and `test_conversation.py`. `ChildSpec.skill`'s type change to
  `plugins.SkillRef` is why `_child_spec()`'s test helper (1574) also needs
  touching — same object, qualified import. No name collision with D1's
  existing `plugins.py` contents (`Artifact`, `NodeTrace`, `DagResult`).
- **Riskiest step, and why:** step 1 — the only step touching already-shipped,
  already-tested code (`run_child`, ~10 existing tests at 1590+). Ordered
  first with its own gate so this risk is proven closed before any new
  `validate()` logic exists; every later step is then purely additive.
- **Rejected alternatives, checked for drift:** (1) Python-importable
  manifest — not done; `tomllib`-parsed data only, never imported as code.
  (2) speculative fields for `each`/`wait` — not added; `Node` carries only
  the fields spec.md's own example fixes. (3) batched/collect-all outcome —
  not done; `validate()` stops at the first failing check, matching
  `model_access.classify()`. (4) cached/mutable validation state — not
  added; `validate()` stays a pure function of `plugin_dir`'s contents per
  call. No drift.

## Proof

- `bash scripts/run_tests.sh tests/unit/test_conversation.py
  tests/unit/test_plugin_manifest.py tests/unit/test_plugins.py` green
  after step 1, and again (full new set) after step 5.
- Each of the 7 `§8` checks has its own test asserting `validate()` returns
  the specific named `ManifestOutcome` member — not just "raises" or
  "is falsy."
- One `Valid` test: a hand-built well-formed `tmp_path` fixture plugin
  (`plugin.toml` + schema file + `SKILL.md` + `init.py` with the named
  function) validates as `Valid`, with every field of the returned
  `Manifest` asserted against what the fixture declared.
- `mypy src` green with `jsonschema` installed.
- `make verify` output pasted, ending `VERIFY OK`.
