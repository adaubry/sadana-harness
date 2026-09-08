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
