"""The `sadana chat` subcommand.

CLI-SHELL-04 of the CLI-SHELL block (`docs/reference/cli_shell_blueprint.md`
§6, work item 4); its full contract is
`docs/tasks/CLI-SHELL-04-chat-command/spec.md`, rebuilt on
`docs/tasks/CLIENT-SURFACE-01-one-door-in/spec.md`.

Owns both its parser and its handler in one file, from the first line — see
spec.md's Design section for why.

Everything about *taking a turn* now lives in `client_surface.py`. What is
left here is what makes this client this client: which conversation the flags
name, who the person is when nobody said, and how an answer is shown — words
to stdout, a diagnostic to stderr, a non-zero exit code when the turn did not
complete. That split is why the terminal stopped losing a paused plugin run:
this file used to hold its own copy of the turn body and the copy never
passed `persist_pause`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from contextlib import closing

from sadana import client_surface, memory
from sadana.conversation import ConversationKey


def build_chat_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("chat", help="have a live conversation with the agent")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--resume", metavar="KEY", help="continue a previously saved conversation by its exact key")
    target.add_argument("--key", metavar="NAME", help="name a new conversation; a name is generated if omitted")
    parser.add_argument("--provider", help="override the configured default provider for this run")
    parser.add_argument("--model", help="override the configured default model for this run")
    parser.add_argument("--account", help="the person this conversation remembers things about")
    parser.set_defaults(func=cmd_chat)


async def _chat_loop(
    runtime: client_surface.Runtime,
    conversation: ConversationKey,
    *,
    account: memory.AccountKey,
) -> int:
    """Read a line, take a turn, show what came back. Nothing else.

    `answer` to stdout and `diagnostic` to stderr, never both: a turn that
    ended badly can still carry real words — an exhausted iteration budget
    produces a summary (`conversation.py`'s EPILOGUE) — and those words are
    the answer, so they go to stdout and the exit code alone reports that
    something went wrong.
    """
    while True:
        try:
            user_input = await asyncio.to_thread(input, "> ")
        except EOFError:
            print()
            return 0

        outcome = await client_surface.take_turn(
            runtime,
            account=account,
            conversation=conversation,
            text=user_input,
            create_as=None,  # started before the first prompt, below
        )

        if outcome.answer:
            print(outcome.answer)
        else:
            print(outcome.diagnostic, file=sys.stderr)

        if not outcome.ok:
            return 1


def cmd_chat(args: argparse.Namespace) -> int:
    # The door resolves the provider and the model; the account it will not,
    # by design (CLIENT-SURFACE-01 requirement 3) — whoever asks states who
    # the person is, so this client holds its own default and says it out loud.
    account = args.account or memory.owner_account()
    runtime = client_surface.open_runtime(provider=args.provider, model=args.model)

    with closing(runtime.conn):
        # `--resume KEY` means it already exists; `--key NAME` (or a generated
        # name) means it does not and is created now, before the first prompt
        # appears. Both are claims about the flag the person typed, and the
        # door settles both in one call — this file used to probe the store
        # itself, which is a client reaching around the door, outside its
        # lock, for the next client to copy. The exceptions are unchanged:
        # `ConversationNotFound` for an unknown resume,
        # `ConversationAlreadyExists` for a name already taken.
        conversation = args.resume or args.key or time.strftime("chat-%Y%m%d-%H%M%S")
        client_surface.open_conversation(
            runtime,
            account=account,
            conversation=conversation,
            template_name=None if args.resume else "chat",
        )

        try:
            return asyncio.run(_chat_loop(runtime, conversation, account=account))
        except KeyboardInterrupt:
            print()
            return 130
