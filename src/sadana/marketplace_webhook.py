"""Tagging a new release triggers its own submission — no command run by
hand.

`docs/tasks/PLUGIN-MARKET-01-submit-vet-and-browse-safely/spec.md`. Mirrors
`channel_webhook.py`'s exact shape: a pure `parse_webhook_request()`
(unit-tested directly, no socket) plus `make_server()`'s
`ThreadingHTTPServer` wiring (proven only by
`scripts/prove_plugin_marketplace.py`, per CLAUDE.md's rule).

The payload is this project's own, not any specific git host's native
format — `{"repo_url": ..., "tag": ...}` — a creator's own tooling (an
Action, a post-tag hook, a script) calls it after tagging, the same choice
`channel_webhook.py` already made for chat messages rather than mimicking
a specific chat platform (spec.md's Design and Rejected alternatives).
"""

from __future__ import annotations

import hmac
import http.server
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sadana.gateway import header_value

_PATH = "/marketplace/webhook"
_SECRET_HEADER = "X-Sadana-Marketplace-Webhook-Secret"  # pragma: allowlist secret - a header name, not a secret


@dataclass(frozen=True)
class ReleaseSubmission:
    repo_url: str
    tag: str


@dataclass(frozen=True)
class WebhookUnauthorized:
    """A missing or wrong secret header."""


@dataclass(frozen=True)
class WebhookBadRequest:
    """Malformed JSON, or a required field missing/wrong-typed."""

    detail: str


def parse_webhook_request(
    body: bytes, headers: Mapping[str, str], *, secret: str
) -> ReleaseSubmission | WebhookUnauthorized | WebhookBadRequest:
    given = header_value(headers, _SECRET_HEADER)
    if not hmac.compare_digest(given, secret):
        return WebhookUnauthorized()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return WebhookBadRequest(detail=f"invalid JSON: {exc}")
    if not isinstance(payload, dict):
        return WebhookBadRequest(detail="body must be a JSON object")

    repo_url = payload.get("repo_url")
    if not isinstance(repo_url, str) or not repo_url:
        return WebhookBadRequest(detail="repo_url is required and must be a non-empty string")

    tag = payload.get("tag")
    if not isinstance(tag, str) or not tag:
        return WebhookBadRequest(detail="tag is required and must be a non-empty string")

    return ReleaseSubmission(repo_url=repo_url, tag=tag)


def make_server(
    host: str, port: int, *, secret: str, on_release: Callable[[ReleaseSubmission], tuple[bool, str]]
) -> http.server.ThreadingHTTPServer:
    """Same non-daemon-thread posture as `channel_webhook.make_server()`,
    for the same reason: `gateway_daemon.run()`'s shutdown relies on
    Python's interpreter-shutdown behavior waiting for every non-daemon
    thread, letting an in-flight request finish before the process exits."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # ponytail: quiet by default, matches channel_webhook.py

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
                ok, text = on_release(parsed)
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
