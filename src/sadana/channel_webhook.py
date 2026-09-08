"""sadana's one shipped channel adapter: a generic incoming webhook.

`docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/spec.md`. I/O:
binds a real TCP socket. `parse_webhook_request()` is a pure function inside
this file, unit-tested directly with no socket involved; the actual
`ThreadingHTTPServer` wiring is exercised only by
`scripts/prove_gateway_webhook_e2e.py` (CLAUDE.md's rule to prove a block's
first real external round trip outside `make test`).

The exchange is synchronous, not fire-and-forget: the HTTP response *is*
the reply (spec.md's own rejection of hermes's async-ingress model — a
generic webhook's caller is already waiting for an HTTP response, so there
is no separate outbound-delivery step to build).
"""

from __future__ import annotations

import hmac
import http.server
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sadana.gateway import MessageEvent

_PATH = "/webhook"
_SECRET_HEADER = "X-Sadana-Webhook-Secret"  # pragma: allowlist secret - a header name, not a secret value


@dataclass(frozen=True)
class WebhookUnauthorized:
    """A missing or wrong `X-Sadana-Webhook-Secret` header."""


@dataclass(frozen=True)
class WebhookBadRequest:
    """Malformed JSON, or a required field missing/wrong-typed."""

    detail: str


def _header(headers: Mapping[str, str], name: str) -> str:
    """HTTP header names are case-insensitive (RFC 7230 §3.2); a plain
    `Mapping[str, str]` is not. `channel_webhook.make_server()`'s real caller
    hands this function `dict(self.headers)`, which preserves whatever
    casing arrived on the wire — a `Mapping.get()` alone would silently
    reject a correct secret sent under different casing than this repo's own
    tests happen to use."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""


def parse_webhook_request(
    body: bytes, headers: Mapping[str, str], *, secret: str
) -> MessageEvent | WebhookUnauthorized | WebhookBadRequest:
    given = _header(headers, _SECRET_HEADER)
    if not hmac.compare_digest(given, secret):
        return WebhookUnauthorized()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return WebhookBadRequest(detail=f"invalid JSON: {exc}")
    if not isinstance(payload, dict):
        return WebhookBadRequest(detail="body must be a JSON object")

    chat_id = payload.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id:
        return WebhookBadRequest(detail="chat_id is required and must be a non-empty string")

    text = payload.get("text")
    if not isinstance(text, str):
        return WebhookBadRequest(detail="text is required and must be a string")

    thread_id = payload.get("thread_id")
    if thread_id is not None and not isinstance(thread_id, str):
        return WebhookBadRequest(detail="thread_id must be a string if present")

    return MessageEvent(platform="webhook", chat_id=chat_id, thread_id=thread_id, text=text)


def make_server(
    host: str, port: int, *, secret: str, on_message: Callable[[MessageEvent], tuple[bool, str]]
) -> http.server.ThreadingHTTPServer:
    """`daemon_threads` is left at `ThreadingHTTPServer`'s stdlib default
    (`False`) — deliberately, unlike hermes's own A2A adapter
    (`plugins/platforms/a2a/adapter.py`), which sets it `True`. sadana's own
    Lifecycle design (`gateway_daemon.run()`) relies on per-request threads
    being non-daemon so Python's interpreter-shutdown behavior — waiting for
    every non-daemon thread — is what lets an in-flight request finish
    before the process exits, with no separate drain timeout built."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # ponytail: quiet by default, add real logging when OBSERVABILITY exists

        def do_POST(self) -> None:
            if self.path != _PATH:
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            parsed = parse_webhook_request(body, dict(self.headers), secret=secret)

            if isinstance(parsed, WebhookUnauthorized):
                self._reply(401, ok=False, text="unauthorized")
                return
            if isinstance(parsed, WebhookBadRequest):
                self._reply(400, ok=False, text=parsed.detail)
                return

            try:
                ok, text = on_message(parsed)
            except Exception as exc:  # genuinely unexpected — a bug, not an expected outcome
                self._reply(500, ok=False, text=str(exc))
                return
            self._reply(200, ok=ok, text=text)

        def _reply(self, status: int, *, ok: bool, text: str) -> None:
            body = json.dumps({"ok": ok, "text": text}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return http.server.ThreadingHTTPServer((host, port), Handler)
