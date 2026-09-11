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

from sadana import __version__, builtin_seed, config, plugins
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
    # The plugins shipped inside this package, placed where
    # `discover_plugins()` looks — once, here, rather than at whichever
    # subcommands remembered to. Doing it per-subcommand left a first-run trap
    # with no way out: `plugin set` refuses a plugin that is not installed, a
    # builtin only appeared once `chat` had seeded it, and `chat`'s first run
    # is exactly what fails for want of a key. `editor` had the same hole from
    # the other side, opening onto an empty plugin list on a fresh machine.
    #
    # *After* `parse_args`, deliberately: `--version`, `--help` and a parse
    # error all exit inside it, and none of them should create directories or
    # be able to fail on an unwritable state dir. The first placement was
    # above this line with a comment claiming exactly this property, which it
    # did not have.
    builtin_seed.seed_all(plugins._plugins_root())
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
