# Plan: Typing a command actually does something (from intent.md 2026-09-08)

## Files that change

- `pyproject.toml` — add `[project.scripts]`.
- `src/sadana/cli.py` (new) — `build_parser()`, `main()`.
- `tests/unit/test_cli.py` (new) — the six tests below.

## Order of work

1. **`pyproject.toml`**: add, after the `dependencies = [...]` line and
   before `[tool.setuptools.packages.find]`:

   ```toml
   [project.scripts]
   sadana = "sadana.cli:main"
   ```

2. **`src/sadana/cli.py`**:

   ```python
   """sadana's command-line entry point.

   CLI-SHELL-02 of the CLI-SHELL block (`docs/reference/cli_shell_blueprint.md`
   §6, work item 2); its full contract is
   `docs/tasks/CLI-SHELL-02-bare-dispatch-shell/spec.md`.

   No subcommands exist yet, on purpose (see spec.md's Non-goals) — this
   module only proves the shell itself behaves correctly with nothing
   behind it. `build_parser()` is kept separate from `main()` so the
   first real subcommand's own work item can extend it
   (`add_subparsers(...)` gains its first `add_parser(...)` call there)
   without touching `main()`.
   """

   from __future__ import annotations

   import argparse
   import sys

   from sadana import __version__


   def build_parser() -> argparse.ArgumentParser:
       parser = argparse.ArgumentParser(prog="sadana")
       parser.add_argument("--version", action="version", version=f"sadana {__version__}")
       return parser


   def main(argv: list[str] | None = None) -> int:
       args = sys.argv[1:] if argv is None else argv
       parser = build_parser()
       if not args:
           parser.error("no command given — nothing is wired up yet")
       parser.parse_args(args)
       return 0  # unreachable today; real once item 3 adds a completable subcommand


   if __name__ == "__main__":
       raise SystemExit(main())
   ```

3. **`tests/unit/test_cli.py`**:

   ```python
   """Tests for sadana.cli: build_parser(), main()."""

   from __future__ import annotations

   import pytest

   from sadana import __version__
   from sadana.cli import build_parser, main


   @pytest.mark.unit
   def test_version_flag_exits_zero_and_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
       with pytest.raises(SystemExit) as exc:
           main(["--version"])
       assert exc.value.code == 0
       assert __version__ in capsys.readouterr().out


   @pytest.mark.unit
   @pytest.mark.parametrize("flag", ["--help", "-h"])
   def test_help_flag_exits_zero_and_prints_help(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
       with pytest.raises(SystemExit) as exc:
           main([flag])
       assert exc.value.code == 0
       assert capsys.readouterr().out


   @pytest.mark.unit
   def test_no_args_exits_two_and_prints_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
       with pytest.raises(SystemExit) as exc:
           main([])
       assert exc.value.code == 2
       assert capsys.readouterr().err


   @pytest.mark.unit
   def test_unrecognized_command_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
       with pytest.raises(SystemExit) as exc:
           main(["nonsense-command"])
       assert exc.value.code == 2
       assert capsys.readouterr().err


   @pytest.mark.unit
   def test_none_argv_reads_sys_argv(monkeypatch: pytest.MonkeyPatch) -> None:
       monkeypatch.setattr("sys.argv", ["sadana", "--version"])
       with pytest.raises(SystemExit) as exc:
           main()
       assert exc.value.code == 0


   @pytest.mark.unit
   def test_build_parser_returns_a_fresh_instance_each_call() -> None:
       assert build_parser() is not build_parser()
   ```

4. Run `scripts/run_tests.sh tests/unit/test_cli.py`, then `/ponytail-review`
   and `/simplify` against the diff, then `make verify`.

## Risks

- **What could this break?** Nothing existing — both source files are
  new, and the `pyproject.toml` edit only adds a section no existing
  tooling reads. No existing test imports `sadana.cli`, and nothing
  currently reads `[project.scripts]` (the disposable-venv test
  convention doesn't install the package — spec.md § Design). `make
  lint`'s "check toml" pre-commit hook is the only thing that will
  parse the edited file differently, and it just needs valid TOML.
- **Riskiest step**: trusting that `build_parser().parse_args(["nonsense-command"])`
  — with no subparsers and no positional defined at all — really does
  raise `SystemExit(2)` via argparse's own default "unrecognized
  arguments" handling, rather than silently ignoring the stray token.
  This is standard, well-documented argparse behavior (`parse_args`, as
  opposed to `parse_known_args`, errors on any leftover argument), but
  it's the one behavior in this plan not already proven by an existing
  test elsewhere in the repo — `test_unrecognized_command_exits_two` is
  ordered to run alongside the others in step 4, not skipped or assumed.
- **Drift check against spec.md's Rejected alternatives**: no
  `add_subparsers()` call anywhere — followed (declined the
  required-empty-subparsers approach entirely). `--version` uses
  `sadana.__version__`, not `importlib.metadata` — followed. Default
  `HelpFormatter`, no custom epilogue — followed. No drift.

## Proof

- `tests/unit/test_cli.py`'s six tests cover: `--version` (exit 0,
  version printed), `--help`/`-h` (exit 0, help printed), empty argv
  (exit 2 via `parser.error()`, stderr non-empty), an unrecognized
  command (exit 2, stderr non-empty), `argv=None` reading `sys.argv`,
  and `build_parser()` returning a fresh instance each call (no shared
  state).
- `make verify` ends `VERIFY OK`.
