"""The gateway's own process lifecycle: start, stay up, stop cleanly.

`docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/spec.md`. I/O:
signals, sockets, the lock file, systemd's notify socket. Owns none of the
turn-handling logic — that's `gateway_dispatch.py` — and none of the wire
protocol — that's `channel_webhook.py`. This module only answers "is one
already running," "stay up until told to stop," and "tell systemd we're
ready."

Two pieces adopted close to verbatim from hermes's GATEWAY-DAEMON block
(`_notify_systemd`, the flock half of its two-tier lock — no PID file, see
spec.md's Non-goals); the SIGTERM/SIGINT + `threading.Event` shutdown shape
is original code, since hermes's own signal handling is asyncio-native
(`loop.add_signal_handler`) and doesn't apply to this module's synchronous,
thread-per-request `ThreadingHTTPServer`.
"""

from __future__ import annotations

import fcntl
import os
import signal
import socket
import sys
import threading
from collections.abc import Callable

from sadana import channel_webhook, config
from sadana.gateway import MessageEvent

_LOCK_FILENAME = "gateway.lock"


def _notify_systemd(message: str) -> bool:
    """`AF_UNIX`/`SOCK_DGRAM` write to `$NOTIFY_SOCKET`. Silent `False`
    no-op when that variable is unset, or on any failure — adopted from
    hermes's `gateway/systemd_notify.py`, its own "genuinely reusable
    regardless of hosting model" case."""
    address = os.environ.get("NOTIFY_SOCKET", "").strip()
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            sock.connect(address)
            sock.send(message.encode("utf-8"))
        return True
    except (OSError, UnicodeError, ValueError):
        return False


def run(*, host: str, port: int, secret: str, on_message: Callable[[MessageEvent], tuple[bool, str]]) -> int:
    """Blocks until SIGTERM/SIGINT. Returns `0` on a clean stop, `1` if the
    webhook secret is unset or the single-instance lock is already held
    (binds nothing in either case). Must be called from the process's main
    thread — `signal.signal()` requires it."""
    if not secret:
        print("SADANA_GATEWAY_WEBHOOK_SECRET is not set; refusing to start", file=sys.stderr)
        return 1

    lock_path = config.get_paths().state_dir / _LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+")  # noqa: SIM115 - the flock must outlive this line, held for run()'s lifetime
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        print(f"another gateway instance already holds {lock_path}", file=sys.stderr)
        return 1

    server = channel_webhook.make_server(host, port, secret=secret, on_message=on_message)
    server_thread = threading.Thread(target=server.serve_forever, name="sadana-gateway-http")
    server_thread.start()

    _notify_systemd("READY=1")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    stop.wait()

    server.shutdown()
    server.server_close()
    server_thread.join()
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    lock_file.close()
    return 0
