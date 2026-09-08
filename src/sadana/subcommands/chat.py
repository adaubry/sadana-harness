"""The `sadana chat` subcommand.

CLI-SHELL-04 of the CLI-SHELL block (`docs/reference/cli_shell_blueprint.md`
§6, work item 4); its full contract is
`docs/tasks/CLI-SHELL-04-chat-command/spec.md`.

Owns both its parser and its handler in one file, from the first
line — see spec.md's Design section for why.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
from contextlib import closing

from sadana import config, conversation_store, model_access, plugin_dispatch, plugin_manifest
from sadana.conversation import (
    Conversation,
    ConversationTemplate,
    ExitReason,
    TemplateRecipe,
    create_conversation,
    iteration_budget_from_config,
    wall_clock_budget_from_config,
)
from sadana.persona import load_or_seed_persona, persona_path_from_config


def build_chat_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("chat", help="have a live conversation with the agent")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--resume", metavar="KEY", help="continue a previously saved conversation by its exact key")
    target.add_argument("--key", metavar="NAME", help="name a new conversation; a name is generated if omitted")
    parser.add_argument("--provider", help="override the configured default provider for this run")
    parser.add_argument("--model", help="override the configured default model for this run")
    parser.set_defaults(func=cmd_chat)


async def _chat_loop(
    conn: sqlite3.Connection,
    conversation: Conversation,
    *,
    plugin_set: plugin_dispatch.PluginSet,
    persona: str,
    provider: str,
    model: str,
) -> int:
    while True:
        try:
            user_input = await asyncio.to_thread(input, "> ")
        except EOFError:
            print()
            return 0

        now = time.monotonic()
        dispatch, tracker = plugin_dispatch.build_dispatch(
            conversation, plugin_set, stable_prompt=persona, provider=provider, model=model, now=now
        )
        persist = conversation_store.bind_persist(conn, conversation, now=now)
        result, conversation = await plugin_dispatch.take_turn_and_reconcile(
            conversation,
            dispatch,
            tracker,
            user_input=user_input,
            provider=provider,
            model=model,
            now=now,
            persist=persist,
        )
        await asyncio.to_thread(conversation_store.save, conn, conversation, now=now)

        if result.final_text:
            # BUDGET_EXHAUSTED still carries a best-effort summary
            # (conversation.py's EPILOGUE) — never drop it just because
            # the exit reason isn't COMPLETED.
            print(result.final_text)
        else:
            print(f"[{result.exit_reason.value}] {result.detail or ''}", file=sys.stderr)

        if result.exit_reason != ExitReason.COMPLETED:
            return 1


def cmd_chat(args: argparse.Namespace) -> int:
    provider = args.provider or config.env("SADANA_MODEL_ACCESS_PROVIDER", model_access.DEFAULT_PROVIDER)
    model = args.model or config.env("SADANA_MODEL_ACCESS_MODEL", model_access.DEFAULT_MODEL)

    persona = load_or_seed_persona(persona_path_from_config())
    plugin_set = plugin_dispatch.build_plugin_set(plugin_manifest.discover_plugins())
    with closing(conversation_store.open_store(conversation_store.store_path_from_config())) as conn:
        now = time.monotonic()
        if args.resume:
            conversation = conversation_store.load(conn, args.resume, now=now)
        else:
            key = args.key or time.strftime("chat-%Y%m%d-%H%M%S")
            template = ConversationTemplate(
                name="chat",
                recipe=TemplateRecipe(
                    stable_prompt=persona, catalog=plugin_set.catalog, tool_specs=plugin_set.tool_specs
                ),
            )
            conversation, _template = create_conversation(
                template,
                key,
                system_message="",
                iteration_budget=iteration_budget_from_config(),
                wall_clock_budget=wall_clock_budget_from_config(now),
            )
            conversation_store.create(conn, conversation, now=now)

        try:
            return asyncio.run(
                _chat_loop(conn, conversation, plugin_set=plugin_set, persona=persona, provider=provider, model=model)
            )
        except KeyboardInterrupt:
            print()
            return 130
