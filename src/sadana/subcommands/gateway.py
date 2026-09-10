"""The `sadana gateway` subcommand family.

`run` is `docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/spec.md`'s
own work — starts the daemon in the foreground and blocks until a signal.
`install`/`start`/`stop`/`restart`/`status` are
`docs/tasks/CLI-SHELL-05-gateway-lifecycle-verb/spec.md`'s own extension of
this same file and parser tree — installing and controlling `run` as a real
systemd service. Owns both its parser and its handlers in one file, matching
`chat.py`'s and `conversations.py`'s existing convention.
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import sys

from sadana import (
    channel_webhook,
    config,
    conversation_store,
    gateway_daemon,
    gateway_dispatch,
    gateway_service,
    model_access,
    observability,
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

    install_parser = gateway_subparsers.add_parser("install", help="install the gateway as a systemd service")
    install_parser.add_argument(
        "--run-as-user", help="user account the service should run as (default: the user who ran sudo)"
    )
    install_parser.set_defaults(func=cmd_gateway_install)

    start_parser = gateway_subparsers.add_parser("start", help="start the installed gateway service")
    start_parser.set_defaults(func=cmd_gateway_start)

    stop_parser = gateway_subparsers.add_parser("stop", help="stop the installed gateway service")
    stop_parser.set_defaults(func=cmd_gateway_stop)

    restart_parser = gateway_subparsers.add_parser("restart", help="restart the installed gateway service")
    restart_parser.set_defaults(func=cmd_gateway_restart)

    status_parser = gateway_subparsers.add_parser("status", help="show whether the gateway service is running")
    status_parser.set_defaults(func=cmd_gateway_status)


def cmd_gateway_run(args: argparse.Namespace) -> int:
    host = args.host or config.env("SADANA_GATEWAY_HOST", "127.0.0.1")
    port = args.port or config.env_int("SADANA_GATEWAY_PORT", 8765)
    secret = config.env("SADANA_GATEWAY_WEBHOOK_SECRET", "")
    if not secret:
        print("SADANA_GATEWAY_WEBHOOK_SECRET is not set; refusing to start", file=sys.stderr)
        return 1

    provider = config.env("SADANA_MODEL_ACCESS_PROVIDER", model_access.DEFAULT_PROVIDER)
    model = config.env("SADANA_MODEL_ACCESS_MODEL", model_access.DEFAULT_MODEL)
    persona = load_or_seed_persona(persona_path_from_config())
    plugin_set = plugin_dispatch.build_plugin_set(plugin_manifest.discover_plugins())
    conn = conversation_store.open_store(conversation_store.store_path_from_config())
    recorder = observability.make_recorder(conn)  # built once; handle_inbound reuses it every inbound message

    # gateway_dispatch.handle_inbound() serializes its own conn access
    # (its module-level _conn_lock) — no lock needed here.
    def on_message(event: MessageEvent) -> tuple[bool, str]:
        return asyncio.run(
            gateway_dispatch.handle_inbound(
                conn,
                event,
                plugin_set=plugin_set,
                persona=persona,
                provider=provider,
                model=model,
                record_turn=recorder.record_turn,
                record_plugin_run=recorder.record_plugin_run,
            )
        )

    def make_server() -> http.server.ThreadingHTTPServer:
        return channel_webhook.make_server(host, port, secret=secret, on_message=on_message)

    return gateway_daemon.run(make_server=make_server, lock_filename="gateway.lock")


def cmd_gateway_install(args: argparse.Namespace) -> int:
    return gateway_service.install(run_as_user=args.run_as_user)


def cmd_gateway_start(args: argparse.Namespace) -> int:
    return gateway_service.start()


def cmd_gateway_stop(args: argparse.Namespace) -> int:
    return gateway_service.stop()


def cmd_gateway_restart(args: argparse.Namespace) -> int:
    return gateway_service.restart()


def cmd_gateway_status(args: argparse.Namespace) -> int:
    return gateway_service.status()
