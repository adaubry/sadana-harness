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
import threading

from sadana import (
    channel_webhook,
    client_surface,
    config,
    gateway_daemon,
    gateway_dispatch,
    gateway_service,
    scheduling,
)
from sadana.gateway import MessageEvent


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

    # One door, opened once: the provider, the model, the persona, the plugin
    # scan, the store, the memory schema and the recorder — the eight lines
    # this function used to own a copy of, and `cmd_chat` the other
    # (CLIENT-SURFACE-01). The daemon runs until killed, so the connection is
    # deliberately not closed here, exactly as before.
    runtime = client_surface.open_runtime()

    # client_surface.take_turn() serializes its own conn access
    # (client_surface.conn_lock) — no lock needed here.
    def on_message(event: MessageEvent) -> tuple[bool, str]:
        return asyncio.run(gateway_dispatch.handle_inbound(runtime, event))

    def make_server() -> http.server.ThreadingHTTPServer:
        return channel_webhook.make_server(host, port, secret=secret, on_message=on_message)

    # A daemon thread — needs no coordination with gateway_daemon.run()'s
    # own SIGTERM/lock/shutdown sequence (scheduling.run_tick_loop's own
    # docstring: it dies with the process, and this project's "best-effort,
    # no catch-up" posture already accepts an abrupt mid-tick kill).
    tick_interval = config.env_int("SADANA_SCHEDULING_TICK_SECONDS", 30)
    threading.Thread(
        target=scheduling.run_tick_loop,
        kwargs={"runtime": runtime, "interval_seconds": tick_interval},
        daemon=True,
        name="sadana-scheduling-tick",
    ).start()

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
