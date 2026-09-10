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

from sadana import __version__, config
from sadana.subcommands.chat import build_chat_parser
from sadana.subcommands.conversations import build_conversations_parser
from sadana.subcommands.editor import build_editor_parser
from sadana.subcommands.gateway import build_gateway_parser
from sadana.subcommands.marketplace import build_marketplace_parser
from sadana.subcommands.memory import build_memory_parser
from sadana.subcommands.plugin import build_plugin_parser
from sadana.subcommands.runs import build_runs_parser
from sadana.subcommands.setup import build_setup_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sadana")
    parser.add_argument("--version", action="version", version=f"sadana {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_conversations_parser(subparsers)
    build_chat_parser(subparsers)
    build_editor_parser(subparsers)
    build_gateway_parser(subparsers)
    build_marketplace_parser(subparsers)
    build_memory_parser(subparsers)
    build_plugin_parser(subparsers)
    build_runs_parser(subparsers)
    build_setup_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    # The state-dir .env that `sadana setup` writes: loaded before any
    # subcommand reads config, never overriding a live env var.
    config.load_dotenv()
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
