# Spec: Typing a command actually does something

Intent: docs/tasks/CLI-SHELL-02-bare-dispatch-shell/intent.md

## Requirements

1. Running the command with `--version` prints a real version and exits
   successfully. (Intent §Proposed outcome.)
2. Running it with `--help` (or `-h`) prints real help instead of
   crashing. (Intent §Proposed outcome.)
3. Running it with nothing at all gives a clear message and a
   consistently-nonzero exit. (Intent §Proposed outcome.)
4. Running it with something it doesn't recognize gives a clear message
   and a consistently-nonzero exit. (Intent §Proposed outcome.)
5. No subcommand does anything real. Nothing beyond the four behaviors
   above exists yet. (Intent §Constraints.)
6. Exactly one shape for "this went wrong" and one for "this is fine" is
   decided now, for whatever gets built on top of this later to inherit
   rather than reinvent. (Intent §Constraints.)
7. Nothing about how settings are found or read changes. (Intent
   §Constraints — not otherwise engaged: this item reads no settings.)

## Design

**Reference corpus.** `docs/reference/cli_shell_blueprint.md` §4.1 is the
design authority here (already written for this block; re-read rather than
re-derived). Adopting: a bare `argparse.ArgumentParser` built by its own
`build_parser()` function (mirrors hermes's `build_top_level_parser()`,
`hermes_cli/_parser.py:136`), `--version` checked as a plain
`action="version"` argument rather than hand-rolled early-exit code
(hermes checks `--version` explicitly before heavier work,
`hermes_cli/main.py:14754-14756`; `action="version"` gets the same effect
for free from argparse itself — a native-platform-feature win over
hand-rolling it). Declining, per §4.1's "mistake not to repeat": no
god-function, no subcommand modules for a subcommand that doesn't exist,
no injected-callable indirection.

**Where this lives.** A new module, `src/sadana/cli.py` — the dispatch
layer, matching the naming pattern the blueprint documents (hermes splits
argv-dispatch from its interactive-engine module; sadana's package is
one directory, so the dispatch module is simply `sadana/cli.py` and a
future interactive engine, if `hermes chat`'s analogue needs one, gets
its own name then — no collision to avoid pre-emptively).

**No subparsers tree yet.** The obvious-looking alternative — call
`add_subparsers(dest="command", required=True)` with zero
`add_parser(...)` calls, letting argparse's own "missing required
argument" and "invalid choice" errors cover requirements 3 and 4 for
free — was tried on paper and rejected (see §Rejected alternatives): it
leans on an edge case (a required subparsers action with an empty choice
set) rather than argparse's ordinary, heavily-exercised path, and it
builds a piece of dispatch machinery (the subparsers tree) before item 3
has a first subcommand to put in it. Instead:

- `build_parser()` returns a plain parser with just `--version`
  registered. `--help`/`-h` exists automatically (argparse's default,
  requirement 2 free).
- `main(argv: list[str] | None = None) -> int` treats an empty `argv`
  (or empty `sys.argv[1:]` when `argv` is `None`) as the "no command"
  case explicitly: `parser.error("no command given — nothing is wired
  up yet")` — argparse's own public method, the same one it calls
  internally for a missing required argument — rather than a hand-rolled
  print. Everything else is handed to `parser.parse_args(args)` — for
  any token that isn't `--version`/`--help`, argparse's own default
  "unrecognized arguments" handling raises `SystemExit(2)` with a usage
  message on `sys.stderr`, satisfying requirement 4 with no new code.
  `parser.error()` raises the identical `SystemExit(2)` shape (usage
  line, `prog: error: ...`, `sys.stderr`) for requirement 3, so both
  invalid-invocation cases now share one mechanism and one exit code —
  requirement 6 with nothing left to unify later.

**The exit-code convention, decided once (requirement 6).** `main()`
returns an `int`; the console entry point and the `if __name__ ==
"__main__"` guard both do `raise SystemExit(main())`. Argparse's own
`--version`/`--help`/parse-error paths raise `SystemExit` directly from
inside `parse_args()` — they never flow through `main()`'s return value,
the same way hermes's own `cmd_*` handlers return an int for `main()` to
act on while `--version`/argparse errors are always argparse's own exit,
never converted to that convention (`hermes_cli/main.py:14754-14828`
confirms both paths coexist there too). This item's convention is: **a
handler returns int; argparse's own built-in exits are left alone,
never wrapped or re-raised as something else.** Once item 3 adds a real
subcommand, its handler follows the same "return an int" shape.

**`--version`'s value.** `sadana.__version__` (`src/sadana/__init__.py:3`,
already `"0.0.1"`, already kept in step with `pyproject.toml`'s own
`version =`). Reused as-is — see §Rejected alternatives for why not
`importlib.metadata`.

**Registration.** `pyproject.toml` gains:

```toml
[project.scripts]
sadana = "sadana.cli:main"
```

This is inert for local dev/test — the project's own pytest config
(`pyproject.toml`'s `pythonpath = ["src"]` comment) deliberately avoids an
editable install so the venv stays disposable; adding this entry doesn't
change that, since nothing installs the package as part of running tests.
It only takes effect once the package is actually installed (a real
deploy image build, which is sadana-harness's own packaging concern, not
the fleet-provisioning concern `cli_shell_blueprint.md` §1.1 puts out of
scope).

## Interface

```python
def build_parser() -> argparse.ArgumentParser: ...
def main(argv: list[str] | None = None) -> int: ...
```

- `build_parser()`: a fresh `argparse.ArgumentParser(prog="sadana")` each
  call — no shared/cached instance, no module-level state (guideline 4).
  Exposed separately from `main()` so a later work item's subcommand can
  extend it (`build_parser().add_subparsers(...)` gains its first real
  `add_parser(...)` call then) without needing to touch `main()`.
- `main(argv)`: `argv=None` reads `sys.argv[1:]`, matching the
  console-script convention; an explicit list is what tests pass.
  - Empty `argv`: raises `SystemExit(2)` via `parser.error(...)`; usage
    + error message to `sys.stderr`.
  - `["--version"]`: raises `SystemExit(0)`; version string to `stdout`
    (argparse's own behavior).
  - `["--help"]` / `["-h"]`: raises `SystemExit(0)`; help to `stdout`.
  - Anything else: raises `SystemExit(2)`; usage + error to `stderr`
    (argparse's own "unrecognized arguments" behavior — this module
    writes no code for this case; it's the same exit shape as the empty
    case above, by construction).
  - There is currently no input that returns `0`: nothing exists yet to
    succeed at. `main()` still ends with an explicit `return 0` after the
    `parse_args()` call for the type checker's sake (`parser.error()` is
    typed `NoReturn`, so mypy narrows past it, but `parse_args()` itself
    returns `Namespace` and mypy cannot prove this specific zero-argument
    parser never falls through) — unreachable today, becomes real the
    moment item 3 adds a subcommand whose own handler can complete and
    return `0`.

## Acceptance criteria

- [ ] `main(["--version"])` raises `SystemExit` with code `0`; captured
      stdout contains `sadana.__version__`'s value.
- [ ] `main(["--help"])` and `main(["-h"])` each raise `SystemExit` with
      code `0`; captured stdout is non-empty.
- [ ] `main([])` raises `SystemExit` with code `2`; captured stderr is
      non-empty.
- [ ] `main(["nonsense-command"])` raises `SystemExit` with code `2`;
      captured stderr is non-empty.
- [ ] `build_parser()` called twice returns two distinct
      `ArgumentParser` instances (no shared/cached state).
- [ ] `pyproject.toml` has a `[project.scripts]` entry:
      `sadana = "sadana.cli:main"`.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Any real subcommand, or the `add_subparsers()` tree it would attach
  to — item 3 and item 4 (`cli_shell_blueprint.md` §6).
- Custom help formatting, an epilogue, or shell-completion generation —
  `cli_shell_blueprint.md` §4.2's "genuinely-later polish" bucket; nothing
  to document a worked example around yet.
- Reading or resolving any configuration. Nothing here needs settings.
- Anything that installs the package as part of this repo's own test or
  dev workflow — registration only, per §Design.

## Rejected alternatives

- **A `required=True` `add_subparsers()` with zero registered
  subcommands**, to get requirements 3 and 4 "for free" from argparse's
  own missing-argument/invalid-choice errors — declined. It works, but it
  leans on a less-common corner of argparse (a required subparsers
  action with an empty choice set renders an empty, slightly confusing
  "choose from ()" message on the invalid-choice path) instead of
  argparse's ordinary, universally-exercised "unrecognized arguments"
  error a plain parser already gives for any stray token. It would also
  mean building the subparsers tree — real dispatch machinery — before
  item 3 has a first subcommand to register on it, the same
  "infrastructure before a second consumer" pattern `plugin_blueprint.md`
  §2 warns against, transplanted to this block.
- **`importlib.metadata.version("sadana")`** for `--version` — declined.
  It only resolves correctly once the package is actually installed
  (matching or not matching `pyproject.toml`'s `version =` depending on
  install state); `sadana.__version__` is a plain, already-existing,
  already-correct constant with no install dependency. Reuse over a new
  mechanism for a value that already exists.
- **Custom `RawDescriptionHelpFormatter` + a worked-examples epilogue**
  (hermes's own choice, `hermes_cli/_parser.py:97-133,146-147`) —
  declined for this item specifically: there is no worked example to
  show yet (zero subcommands), so a curated epilogue would either be
  empty or invented. Argparse's default formatter already satisfies
  requirement 2. Revisit once there's a real command to show using.

## Concerns

- **`main()`'s trailing `return 0` is unreachable given this item's own
  parser** (see §Interface) — every currently-possible input either
  raises `SystemExit` (version, help, anything unrecognized) or is the
  explicit empty-argv case (`return 1`). It's kept anyway so `-> int`
  type-checks and so item 3 has a real value to return instead of a
  structural change to make. Flagging this explicitly rather than
  quietly declaring `-> None` or something less accurate to the eventual
  shape.
- **Policy skills named by design-skill but absent from this repo**:
  `project-structure` and `reference-lookup` still don't exist under
  `.claude/skills/` (same gap noted in CLI-SHELL-01's own spec.md) —
  nothing new to apply beyond CLAUDE.md itself (module layout, addressed
  above) and the reference-corpus instruction (addressed above against
  `cli_shell_blueprint.md`).
- **`testing-conventions` is applied**: the new test file is
  `tests/unit/test_cli.py` (module `sadana/cli.py`, per the naming rule),
  touches no network/filesystem/clock, uses `capsys` and
  `pytest.raises(SystemExit)` rather than a subprocess or an installed
  binary — this is deliberate (§Design, §Rejected alternatives on
  `importlib.metadata`): testing this module never depends on the
  package actually being installed, matching the project's own
  disposable-venv convention.
- No policy conflict found. The one real tension this item surfaces —
  argparse's own exit behavior versus a uniform "handler returns int"
  convention — is resolved in §Design (argparse's built-in exits are
  left alone; the convention governs subcommand handlers, which don't
  exist yet) rather than papered over, and it matches hermes's own
  precedent of the same two paths coexisting. This rule binds beyond
  this one work item, so it was pinned to `CLAUDE.md` (with the
  maintainer's approval) rather than left to live only in this file.
