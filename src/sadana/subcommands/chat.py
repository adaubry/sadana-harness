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

from sadana import (
    builtin_seed,
    config,
    conversation_store,
    memory,
    memory_store,
    model_access,
    observability,
    plugin_dispatch,
    plugin_manifest,
    plugins,
)
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
    parser.add_argument("--account", help="the person this conversation remembers things about")
    parser.set_defaults(func=cmd_chat)


async def _chat_loop(
    conn: sqlite3.Connection,
    conversation: Conversation,
    *,
    plugin_set: plugin_dispatch.PluginSet,
    persona: str,
    provider: str,
    model: str,
    account_key: memory.AccountKey,
) -> int:
    recorder = observability.make_recorder(conn)
    while True:
        try:
            user_input = await asyncio.to_thread(input, "> ")
        except EOFError:
            print()
            return 0

        now = time.monotonic()
        dispatch, tracker = plugin_dispatch.build_dispatch(
            conversation,
            plugin_set,
            stable_prompt=persona,
            provider=provider,
            model=model,
            now=now,
            record_turn=recorder.record_turn,
            record_plugin_run=recorder.record_plugin_run,
            memory_context=memory_store.DispatchContext(account_key=account_key, conn=conn),
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
            record_turn=recorder.record_turn,
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
    account_key = args.account or config.env("SADANA_MEMORY_ACCOUNT", "local")

    persona = load_or_seed_persona(persona_path_from_config())
    builtin_seed.seed_all(plugins._plugins_root())
    plugin_set = plugin_dispatch.build_plugin_set(plugin_manifest.discover_plugins())
    with closing(conversation_store.open_store(conversation_store.store_path_from_config())) as conn:
        memory_store.ensure_schema(conn)
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
            entries = memory_store.list_entries(conn, account_key)
            override = memory_store.get_rubric_override(conn, account_key)
            system_message = memory.system_message_for(entries, memory.default_rubric(), override)
            conversation, _template = create_conversation(
                template,
                key,
                system_message=system_message,
                iteration_budget=iteration_budget_from_config(),
                wall_clock_budget=wall_clock_budget_from_config(now),
            )
            conversation_store.create(conn, conversation, now=now)

        try:
            return asyncio.run(
                _chat_loop(
                    conn,
                    conversation,
                    plugin_set=plugin_set,
                    persona=persona,
                    provider=provider,
                    model=model,
                    account_key=account_key,
                )
            )
        except KeyboardInterrupt:
            print()
            return 130
