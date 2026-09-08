"""The `sadana gateway run` subcommand.

`docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/spec.md`. Owns both
its parser and its handler in one file, matching `chat.py`'s and
`conversations.py`'s existing convention. Nested under `gateway` because
that's the literal two-word command spec.md names, not speculative
registry-building — only `run` is registered; the process is stopped
externally via signal, not a second subcommand.
"""

from __future__ import annotations

import argparse
import asyncio

from sadana import (
    config,
    conversation_store,
    gateway_daemon,
    gateway_dispatch,
    model_access,
    plugin_dispatch,
    plugin_manifest,
)
from sadana.gateway import MessageEvent
from sadana.persona import load_or_seed_persona, persona_path_from_config


def build_gateway_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("gateway", help="run sadana as a long-lived background process")
    gateway_subparsers = parser.add_subparsers(dest="gateway_command", required=True)
    run_parser = gateway_subparsers.add_parser("run", help="start the gateway daemon and block until stopped")
    run_parser.add_argument("--host", help="override the configured bind host")
    run_parser.add_argument("--port", type=int, help="override the configured bind port")
    run_parser.set_defaults(func=cmd_gateway_run)


def cmd_gateway_run(args: argparse.Namespace) -> int:
    host = args.host or config.env("SADANA_GATEWAY_HOST", "127.0.0.1")
    port = args.port or config.env_int("SADANA_GATEWAY_PORT", 8765)
    secret = config.env("SADANA_GATEWAY_WEBHOOK_SECRET", "")

    provider = config.env("SADANA_MODEL_ACCESS_PROVIDER", model_access.DEFAULT_PROVIDER)
    model = config.env("SADANA_MODEL_ACCESS_MODEL", model_access.DEFAULT_MODEL)
    persona = load_or_seed_persona(persona_path_from_config())
    plugin_set = plugin_dispatch.build_plugin_set(plugin_manifest.discover_plugins())
    conn = conversation_store.open_store(conversation_store.store_path_from_config())

    # gateway_dispatch.handle_inbound() serializes its own conn access
    # (its module-level _conn_lock) — no lock needed here.
    def on_message(event: MessageEvent) -> tuple[bool, str]:
        return asyncio.run(
            gateway_dispatch.handle_inbound(
                conn, event, plugin_set=plugin_set, persona=persona, provider=provider, model=model
            )
        )

    return gateway_daemon.run(host=host, port=port, secret=secret, on_message=on_message)
