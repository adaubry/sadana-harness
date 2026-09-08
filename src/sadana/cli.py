"""sadana's command-line entry point.

CLI-SHELL-02 (`docs/reference/cli_shell_blueprint.md` §6, work item 2)
built the bare shell; CLI-SHELL-03 (work item 3) added its first real
subcommand. `build_parser()` stays separate from `main()` so each
later subcommand's own work item extends it the same way
`build_conversations_parser` did, without needing structural changes
to `main()` itself.
"""

from __future__ import annotations

import argparse
import sys

from sadana import __version__
from sadana.subcommands.conversations import build_conversations_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sadana")
    parser.add_argument("--version", action="version", version=f"sadana {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_conversations_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
