# Review: Typing a command actually does something (from plan.md 2026-09-08)

Reviewed: HEAD (working tree, uncommitted) — 4 files, +97/-0
Reviewer context: fresh session
Second opinion: none — ran during build (self-check via `/simplify`), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/CLI-SHELL-02-bare-dispatch-shell: all present artifacts valid
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
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 13 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 20%]
........................................................................ [ 40%]
........................................................................ [ 60%]
........................................................................ [ 80%]
.......................................................................  [100%]
359 passed in 3.05s
TESTS OK
VERIFY OK
```

## Findings

Ran all three passes (Bugs, Security, Compliance) against `git diff HEAD`
for `CLAUDE.md`, `pyproject.toml`, `src/sadana/cli.py`, and
`tests/unit/test_cli.py`, and reconciled the diff against `plan.md` §
Files that change, `plan.md` § Proof, `spec.md` § Acceptance criteria,
`spec.md` § Rejected alternatives, the five design principles, and the
new CLAUDE.md rule this item itself added. Detail below.

### Important

- [Compliance] `git diff --stat HEAD` shows four files changed
  (`CLAUDE.md`, `pyproject.toml`, `src/sadana/cli.py`,
  `tests/unit/test_cli.py`), but `plan.md` § Files that change names only
  three (`pyproject.toml`, `src/sadana/cli.py`, `tests/unit/test_cli.py`).
  The `CLAUDE.md` addition — "A CLI subcommand's handler returns an int
  for its own exit code; argparse's own `--version`/`--help`/parse-error
  exits are never wrapped or re-raised as something else." — is a
  real, substantive, project-wide rule addition (`spec.md` § Concerns
  says it was "pinned to CLAUDE.md ... rather than left to live only in
  this file"), not a formatting artifact. Whatever the merits of the
  rule itself, `plan.md` no longer fully describes what the diff
  touches, and nothing in this chain declared the omission — it
  surfaced only in this review's own diff-vs-plan comparison, which is
  exactly the failure mode this step exists to catch.

### Compliance pass detail

**Proof items (`plan.md` § Proof):**
- "`tests/unit/test_cli.py`'s six tests cover: `--version` ... `--help`/
  `-h` ... empty argv ... an unrecognized command ... `argv=None` ...
  and `build_parser()` returning a fresh instance" — discharged. All six
  tests are present verbatim as planned
  (`tests/unit/test_cli.py:12-16,21-25,29-33,37-41,45-49,53-54`) and ran
  as part of the 359 passing in the Evidence above.
- "`make verify` ends `VERIFY OK`." — discharged, see Evidence.

**Acceptance criteria (`spec.md` § Acceptance criteria):**
- `main(["--version"])` → `SystemExit(0)`, version in stdout —
  `tests/unit/test_cli.py:12-16` (`test_version_flag_exits_zero_and_prints_version`).
- `main(["--help"])` / `main(["-h"])` → `SystemExit(0)`, stdout non-empty
  — `tests/unit/test_cli.py:21-25` (parametrized).
- `main([])` → `SystemExit(2)`, stderr non-empty —
  `tests/unit/test_cli.py:29-33`.
- `main(["nonsense-command"])` → `SystemExit(2)`, stderr non-empty —
  `tests/unit/test_cli.py:37-41`.
- `build_parser()` called twice returns distinct instances —
  `tests/unit/test_cli.py:53-54`.
- `pyproject.toml` has `[project.scripts]` with
  `sadana = "sadana.cli:main"` — `pyproject.toml:12-13`, present exactly.
- `make verify` ends `VERIFY OK` — see Evidence.
All satisfied.

**Rejected alternatives (`spec.md` § Rejected alternatives) — drift check:**
- No `add_subparsers()` call anywhere in `src/sadana/cli.py` — confirmed
  by reading the whole file (39 lines); the required-empty-subparsers
  approach was not reintroduced.
- `--version` uses `sadana.__version__` (`src/sadana/cli.py:20`,
  `from sadana import __version__`), not `importlib.metadata` —
  confirmed.
- Default `HelpFormatter`, no custom epilogue — `ArgumentParser(prog="sadana")`
  takes no `formatter_class` or `epilog` argument — confirmed.
No drift found.

**Five design principles:**
1. Learn from the reference first — `spec.md` § Design cites
   `docs/reference/cli_shell_blueprint.md` §4.1 explicitly for both the
   `build_parser()` shape (mirroring hermes's `build_top_level_parser()`)
   and the `action="version"` choice over hermes's own hand-rolled
   early-exit check. Prior art consulted and knowingly diverged from
   where hermes's choice didn't fit (no epilogue, no custom formatter,
   no subparsers yet) — each divergence is named in § Rejected
   alternatives. No unexplained reinvention.
2. Reduce the number of bets — the rejected `required=True`
   `add_subparsers()`-with-nothing-registered alternative would have
   been the cheaper-looking bet that actually locks in dispatch
   machinery before a second real subcommand exists; declining it in
   favor of a plain parser plus `parser.error()` is the lower-commitment
   choice, and it's the one actually shipped.
3. More plugins, not more core — not engaged by this item; there is no
   plugin-infrastructure interaction here, by design (`spec.md` §
   Non-goals: "Any real subcommand... item 3 and item 4").
4. Catch the scenario at the least step-cost — reusing `parser.error()`
   (already `NoReturn`, already the same code path argparse uses
   internally for a missing required argument) for the empty-argv case
   unifies it with the unrecognized-argument case at zero added
   machinery, rather than a bespoke branch with its own exit code.
5. Minimise mutable state — `build_parser()` builds and returns a fresh
   `ArgumentParser` on every call, no module-level parser instance, no
   cached state; `tests/unit/test_cli.py:53-54` asserts this directly.

**CLAUDE.md rule — "a handler returns an int ... argparse's own exits are
never wrapped or re-raised":** `main()` returns `int` (`src/sadana/cli.py:29`,
final `return 0` at line 35); the two argparse-owned exit paths
(`parser.error(...)` at line 33, and `parser.parse_args(args)` at line 34,
which raises `SystemExit` directly from inside argparse for `--version`,
`--help`, and any unrecognized token) are called with no surrounding
`try`/`except` and their `SystemExit` is left to propagate unmodified —
neither path is caught, wrapped, or converted into a return value.
Compliant with the rule as stated, and the rule's own addition is the one
file-scope issue flagged above.

### Nits

- [Bugs] The trailing `return 0` at `src/sadana/cli.py:35` is dead code
  for every input this item's own parser can receive (both `spec.md` §
  Interface and § Concerns say so explicitly, and the diff includes the
  same acknowledgment as an inline comment) — not a defect, just noting
  it's already flagged as a known, deliberate gap rather than an
  oversight.

### Bugs and Security passes

No Bugs or Security findings beyond the above. This module does no I/O,
touches no secrets, and reads no settings (`spec.md` requirement 7,
confirmed: no `open`, `os.environ`, network, or config-loader import
anywhere in `src/sadana/cli.py`). The diff for the three plan-named files
matches `plan.md`'s own proposed code block verbatim (`pyproject.toml`
insertion location, `src/sadana/cli.py` body, and all six tests in
`tests/unit/test_cli.py`), checked line by line against `git diff HEAD`.

## Decision

Approved by Adam Aubry, 2026-09-08, as-is — the Important finding (`plan.md`
§ Files that change omitting `CLAUDE.md`) is accepted without amending
`plan.md`; the `CLAUDE.md` edit itself was already made deliberately during
design, with separate approval, and is recorded in `spec.md` § Concerns.
