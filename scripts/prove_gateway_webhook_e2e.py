#!/usr/bin/env python3
"""Standalone proof that gateway_daemon.run() serves a real webhook end to
end: a real bound TCP socket, a real HTTP request/response round trip, and a
real SIGTERM triggering a graceful stop that lets an in-flight request
finish before the process stops.

Not a pytest test — testing-conventions bars sockets from the unit suite,
and CLAUDE.md requires a block's first real external round trip to be
proved by a standalone script, output pasted into review.md's ## Evidence.
`model_access.send()` is monkeypatched here (a plain module-level
reassignment, not pytest's fixture) to a canned response — this script
proves the daemon/socket/lifecycle path is real, not `model_access`'s own
provider round trip (already proven by MODEL-ACCESS-01's own evidence).

`gateway_daemon.run()` must run on this process's main thread —
`signal.signal()` is a hard Python main-thread-only restriction, and this
daemon's own SIGTERM handling depends on it — so the HTTP-driving work below
runs on a second, genuinely background thread instead, sending the real
SIGTERM back to this same process once its checks are done.

See docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/spec.md's
acceptance criteria for the full contract this proves.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ["SADANA_STATE_DIR"] = tempfile.mkdtemp(prefix="sadana-gateway-e2e-")  # noqa: E402

from sadana import gateway_daemon, gateway_dispatch, model_access, plugin_dispatch  # noqa: E402
from sadana.conversation_store import open_store, store_path_from_config  # noqa: E402
from sadana.gateway import MessageEvent  # noqa: E402

HOST = "127.0.0.1"
PORT = 18765
SECRET = "prove-gateway-webhook-e2e-secret"  # pragma: allowlist secret - fixed local test value, no real deployment
URL = f"http://{HOST}:{PORT}/webhook"
SLOW_REPLY_DELAY_S = 0.5


def _content_text(content: object) -> str:
    """`request.messages[i]["content"]` is either a plain string or, once a
    turn's history crosses CONTEXT's own cache boundary, a list of
    Anthropic-style content blocks (`model_access.mark_cache_boundary()`) —
    handle both rather than assuming the plain-string shape."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


def _canned_send(request: model_access.Request) -> model_access.Response:
    last_user = next((_content_text(m["content"]) for m in reversed(request.messages) if m.get("role") == "user"), "")
    if "slow" in (last_user or ""):
        time.sleep(SLOW_REPLY_DELAY_S)
        return model_access.Response(
            content="slow reply", tool_calls=(), finish_reason="stop", usage=model_access.Usage()
        )
    return model_access.Response(
        content=f"echo: {last_user}", tool_calls=(), finish_reason="stop", usage=model_access.Usage()
    )


def _post(chat_id: str, text: str, *, secret: str = SECRET) -> tuple[int, dict]:
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=body,
        method="POST",
        headers={"X-Sadana-Webhook-Secret": secret, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed http://127.0.0.1 test URL
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _wait_for_port(host: str, port: int, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"gateway never bound {host}:{port}")


def _drive_http_checks() -> None:
    print("waiting for the gateway to bind its socket...")
    _wait_for_port(HOST, PORT)
    print(f"[ok] gateway is listening on {HOST}:{PORT}")

    print("\n=== turn 1: first message on a new chat_id ===")
    status1, body1 = _post("chat-e2e-1", "hello")
    print(f"status={status1} body={body1}")
    assert status1 == 200, f"expected 200, got {status1}"
    assert body1["ok"] is True, f"expected ok=true, got {body1}"
    assert body1["text"] == "echo: hello", f"unexpected reply text: {body1['text']!r}"
    print("[ok] first response is a real, successful reply")

    print("\n=== turn 2: second message, same chat_id — proves continuity ===")
    status2, body2 = _post("chat-e2e-1", "still there?")
    print(f"status={status2} body={body2}")
    assert status2 == 200
    assert body2["ok"] is True
    assert body2["text"] == "echo: still there?"
    print("[ok] second turn continued the same conversation")

    print("\n=== wrong secret ===")
    status3, body3 = _post("chat-e2e-1", "irrelevant", secret="wrong-secret")
    print(f"status={status3} body={body3}")
    assert status3 == 401, f"expected 401, got {status3}"
    print("[ok] a wrong secret is rejected with 401")

    print("\n=== SIGTERM lets an in-flight request finish ===")
    slow_result: dict[str, tuple[int, dict]] = {}

    def _slow_request() -> None:
        slow_result["result"] = _post("chat-e2e-1", "go slow now")

    slow_thread = threading.Thread(target=_slow_request)
    slow_thread.start()
    time.sleep(0.1)  # let the slow request actually start before we signal
    print(f"sending SIGTERM to pid {os.getpid()} while a slow request is in flight...")
    os.kill(os.getpid(), signal.SIGTERM)
    slow_thread.join(timeout=SLOW_REPLY_DELAY_S + 5)
    assert not slow_thread.is_alive(), "the slow in-flight request never finished"
    status4, body4 = slow_result["result"]
    print(f"status={status4} body={body4}")
    assert status4 == 200, f"expected the in-flight request to complete with 200, got {status4}"
    assert body4["text"] == "slow reply"
    print("[ok] SIGTERM let the in-flight request finish before the process stopped")

    print("\nALL ASSERTIONS PASSED")


def main() -> None:
    model_access.send = _canned_send  # type: ignore[assignment]

    conn = open_store(store_path_from_config())
    plugin_set = plugin_dispatch.PluginSet(catalog=(), tool_specs=(), by_tool={})

    # gateway_dispatch.handle_inbound() serializes its own conn access
    # (its module-level _conn_lock) — no lock needed here.
    def on_message(event: MessageEvent) -> tuple[bool, str]:
        return asyncio.run(
            gateway_dispatch.handle_inbound(
                conn, event, plugin_set=plugin_set, persona="You are a test persona.\n", provider="p", model="m"
            )
        )

    driver_thread = threading.Thread(target=_drive_http_checks)
    driver_thread.start()

    # gateway_daemon.run() owns the main thread; the driver thread above
    # does the HTTP round trip and eventually sends this process a real
    # SIGTERM.
    result = gateway_daemon.run(host=HOST, port=PORT, secret=SECRET, on_message=on_message)

    driver_thread.join()
    assert result == 0, f"gateway_daemon.run() should return 0 on a clean SIGTERM stop, got {result}"
    print(f"[ok] gateway_daemon.run() returned {result} after a clean stop")


if __name__ == "__main__":
    main()
