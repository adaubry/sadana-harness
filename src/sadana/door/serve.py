"""The loopback development listener: bytes in, `router.handle()`, bytes out.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 48-49.
Same shape as `editor_server.py`'s own `make_server`/`_from_this_machine`/
`_run` (spec.md § Design) — `editor_server.py` already proves a hand-parsed
`http.server.ThreadingHTTPServer` needs no framework at this repository's
scale, and reusing that shape here is a second use of the same proof, not a
new one.

Development only. The door's own bearer-token verification is the real
access control; the loopback + `Origin` check below is belt-and-braces the
same way it already is in `editor_server.py` — a browser will happily send a
page from anywhere to `127.0.0.1`, so binding to loopback alone keeps other
*machines* out, not other *sites* in the same browser. H30 replaces this
file with the tether; it does not extend it.
"""

from __future__ import annotations

import http.server
import traceback
from collections.abc import Mapping

from sadana import gateway
from sadana.door.request import DoorRequest
from sadana.door.router import DoorContext, handle


def _host_without_port(host_header: str) -> str:
    """A bracketed IPv6 literal (`[::1]:7001`) carries colons of its own, so
    a bare `split(":")[0]` — `editor_server.py`'s own shape — takes the
    literal's first segment instead of the whole address. Diverges from
    that precedent here on purpose, not by copying it uninspected."""
    if host_header.startswith("["):
        return host_header.partition("]")[0].lstrip("[")
    return host_header.split(":")[0]


def _from_this_machine(headers: Mapping[str, str]) -> bool:
    host = _host_without_port(gateway.header_value(headers, "Host"))
    if host not in ("localhost", "127.0.0.1", "::1"):
        return False
    origin = gateway.header_value(headers, "Origin")
    if origin:
        without_scheme = _host_without_port(origin.split("://", 1)[-1])
        return without_scheme in ("localhost", "127.0.0.1", "::1")
    return True


def make_server(host: str, port: int, *, ctx: DoorContext) -> http.server.ThreadingHTTPServer:
    """`daemon_threads` left at the stdlib default (`False`), matching
    `editor_server.make_server`'s own reasoning: `gateway_daemon.run()`
    relies on interpreter shutdown waiting for non-daemon threads, so an
    in-flight request finishes rather than being cut off mid-write."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # ponytail: quiet by default, same as editor_server.py

        def _run(self, method: str) -> None:
            headers = dict(self.headers)
            if not _from_this_machine(headers):
                body = b"the door serves this machine only"
                self.send_response(403)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length) if length else b""
            path, _, query = self.path.partition("?")
            try:
                response = handle(
                    DoorRequest(method=method, path=path, query=query, headers=headers, body=payload),
                    ctx=ctx,
                )
            except Exception:  # never expected — handle() itself never raises; this is the transport's own net
                body = b"the door hit an unexpected error; see the terminal it is running in"
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                traceback.print_exc()
                return
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:
            self._run("GET")

        def do_POST(self) -> None:
            self._run("POST")

        def do_PATCH(self) -> None:
            self._run("PATCH")

        def do_DELETE(self) -> None:
            self._run("DELETE")

    return http.server.ThreadingHTTPServer((host, port), Handler)
