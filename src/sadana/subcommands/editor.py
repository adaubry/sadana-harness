"""The `sadana editor` subcommand: serve the plugin editor locally.

`docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save/spec.md`. Owns its parser and
its handler in one file, matching `chat.py`/`gateway.py`/`marketplace.py`, and
reuses `gateway_daemon.run()`'s lifecycle — the third caller of a seam
PLUGIN-MARKET-01 generalised for exactly this (lock file, bind, wait for a
signal, shut down cleanly).

There is no `--host` flag. The editor serves the machine it runs on and
refuses anything else, which is the whole of its access control: hermes
shipped a flag that let its dashboard bind publicly without auth and spent a
hardening release taking it back (`hermes_cli/subcommands/dashboard.py`'s own
`--insecure` help text). Taking the conclusion without the machinery costs one
check, and it is the single line a future hosted version replaces with "or an
auth provider is configured."
"""

from __future__ import annotations

import argparse
import http.server
import ipaddress
import sys

from sadana import config, editor_server, gateway_daemon, plugins


def build_editor_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("editor", help="edit plugins by drawing them, in a browser")
    parser.add_argument("--port", type=int, help="override the configured port")
    parser.set_defaults(func=cmd_editor)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cmd_editor(args: argparse.Namespace) -> int:
    host = config.env("SADANA_EDITOR_HOST", "127.0.0.1")
    port = args.port or config.env_int("SADANA_EDITOR_PORT", 8770)
    if not _is_loopback(host):
        print(
            f"SADANA_EDITOR_HOST is {host!r}; the editor serves this machine only and has no "
            "authentication yet, so it refuses to bind anywhere else",
            file=sys.stderr,
        )
        return 1

    plugins_root = plugins._plugins_root()
    plugins_root.mkdir(parents=True, exist_ok=True)

    def make_server() -> http.server.ThreadingHTTPServer:
        return editor_server.make_server(host, port, plugins_root=plugins_root)

    print(f"editing plugins in {plugins_root} — open http://{host}:{port}/")
    return gateway_daemon.run(make_server=make_server, lock_filename="editor.lock")
