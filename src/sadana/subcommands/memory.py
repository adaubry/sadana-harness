"""The `sadana memory` subcommand.

`docs/tasks/MEMORY-01-write-recall-and-forget/spec.md`. Direct reads/writes
against `memory_store` — no model involved, since a person being able to
see and remove what's remembered about them (and set their own rubric
addition) has to work even if the model never volunteers to expose it.
Owns both its parser and its handlers in one file, mirroring
`subcommands/plugin.py`'s nested-subcommand shape and `subcommands/runs.py`'s
"no recorded runs"-style empty message.
"""

from __future__ import annotations

import argparse
import time

from sadana import memory_store, stores


def build_memory_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("memory", help="view, forget, and adjust what's remembered about an account")
    memory_subparsers = parser.add_subparsers(dest="memory_command", required=True)

    list_parser = memory_subparsers.add_parser("list", help="show everything remembered about an account")
    list_parser.add_argument("account", help="the account to list memories for")
    list_parser.set_defaults(func=cmd_memory_list)

    forget_parser = memory_subparsers.add_parser("forget", help="remove one remembered entry")
    forget_parser.add_argument("account", help="the account the entry belongs to")
    forget_parser.add_argument("entry_key", help="the entry's key, as shown by `memory list`")
    forget_parser.set_defaults(func=cmd_memory_forget)

    set_rubric_parser = memory_subparsers.add_parser(
        "set-rubric", help="set this account's own addition to the deployer's default rubric"
    )
    set_rubric_parser.add_argument("account", help="the account to set a rubric addition for")
    set_rubric_parser.add_argument("text", help="the rubric addition, in plain prose")
    set_rubric_parser.set_defaults(func=cmd_memory_set_rubric)


def cmd_memory_list(args: argparse.Namespace) -> int:
    with stores.open_cli_store() as conn:
        entries = memory_store.list_entries(conn, args.account)
    if not entries:
        print("Nothing remembered.")
        return 0
    for entry in entries:
        print(f"{entry.entry_key}: {entry.content}")
    return 0


def cmd_memory_forget(args: argparse.Namespace) -> int:
    with stores.open_cli_store() as conn:
        memory_store.delete_entry(conn, args.account, args.entry_key)
    print(f"forgot {args.entry_key!r} for {args.account!r}")
    return 0


def cmd_memory_set_rubric(args: argparse.Namespace) -> int:
    with stores.open_cli_store() as conn:
        memory_store.set_rubric_override(conn, args.account, args.text, now=time.time())
    print(f"rubric addition set for {args.account!r}")
    return 0
