"""The `sadana plugin` subcommand.

`docs/tasks/PLUGIN-INSTALL-01-registry-fetch-verify-place/spec.md`. Owns
both its parser and its handlers in one file, mirroring
`subcommands/runs.py`'s shape. Every `RegisterOutcome`/`InstallOutcome`
variant maps to a stderr line and an exit code here — `plugin_install.py`
itself never prints or exits.
"""

from __future__ import annotations

import argparse
import sys
import time
from contextlib import closing

from sadana import plugin_install, plugins
from sadana.conversation_store import open_store, store_path_from_config


def build_plugin_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("plugin", help="register and install plugins from a git repository")
    plugin_subparsers = parser.add_subparsers(dest="plugin_command", required=True)

    register_parser = plugin_subparsers.add_parser("register", help="record a name for a plugin's git repository")
    register_parser.add_argument("name", help="the name to register")
    register_parser.add_argument("repo_url", help="the plugin's git repository URL")
    register_parser.set_defaults(func=cmd_plugin_register)

    install_parser = plugin_subparsers.add_parser("install", help="fetch, verify, and place a registered plugin")
    install_parser.add_argument("name", help="a name already registered")
    install_parser.add_argument("tag", help="the exact released git tag to install")
    install_parser.add_argument(
        "--replace", action="store_true", help="replace an already-installed plugin of this name"
    )
    install_parser.set_defaults(func=cmd_plugin_install)


def cmd_plugin_register(args: argparse.Namespace) -> int:
    with closing(open_store(store_path_from_config())) as conn:
        outcome = plugin_install.register(conn, args.name, args.repo_url, now=time.time())
    match outcome:
        case plugin_install.Registered():
            print(f"registered {args.name!r} -> {args.repo_url}")
            return 0
        case plugin_install.NameTaken():
            print(f"error: {args.name!r} is already registered", file=sys.stderr)
            return 1


def cmd_plugin_install(args: argparse.Namespace) -> int:
    with closing(open_store(store_path_from_config())) as conn:
        outcome = plugin_install.install(
            conn, args.name, args.tag, plugins_root=plugins._plugins_root(), replace=args.replace
        )
    match outcome:
        case plugin_install.Installed(directory=directory):
            print(f"installed {args.name!r} at {args.tag!r} -> {directory}")
            return 0
        case plugin_install.UnknownPluginName():
            print(f"error: {args.name!r} is not registered", file=sys.stderr)
            return 1
        case plugin_install.AlreadyInstalled():
            print(f"error: {args.name!r} is already installed; pass --replace to reinstall", file=sys.stderr)
            return 1
        case plugin_install.NameMismatch(expected=expected, found=found):
            print(f"error: plugin.toml declares {found!r}, expected {expected!r}", file=sys.stderr)
            return 1
        case plugin_install.TagMismatch() | plugin_install.FetchFailed():
            print(f"error: {plugin_install.describe_fetch_failure(outcome)}", file=sys.stderr)
            return 1
