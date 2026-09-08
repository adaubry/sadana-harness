"""The `sadana conversations` subcommand.

CLI-SHELL-03 of the CLI-SHELL block (`docs/reference/cli_shell_blueprint.md`
§6, work item 3); its full contract is
`docs/tasks/CLI-SHELL-03-conversations-subcommand/spec.md`.

Owns both its parser and its handler in one file, from the first
line — see spec.md's Design section for why.
"""

from __future__ import annotations

import argparse
import time
from contextlib import closing

from sadana import conversation_store
from sadana.conversation import Conversation


def build_conversations_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("conversations", help="find a saved conversation")
    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="text to search for (key, template name, or message content); omit to list every saved conversation",
    )
    parser.set_defaults(func=cmd_conversations)


def format_conversation_line(conversation: Conversation) -> str:
    count = len(conversation.messages)
    noun = "message" if count == 1 else "messages"
    return f"{conversation.key} ({conversation.template_name}, {count} {noun})"


def cmd_conversations(args: argparse.Namespace) -> int:
    path = conversation_store.store_path_from_config()
    with closing(conversation_store.open_store(path)) as conn:
        found = conversation_store.search_conversations(conn, args.query, now=time.monotonic())
    if not found:
        print("No conversations found.")
        return 0
    for conversation in found:
        print(format_conversation_line(conversation))
    return 0
