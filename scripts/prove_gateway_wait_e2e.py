#!/usr/bin/env python3
"""Standalone proof that a plugin's DAG can pause mid-run and resume later
when a slow external answer arrives — GATEWAY-DAEMON-02's own concrete
scenario, exercised through a real daemon, a real socket, and a real
`plugin_pauses` row in a real SQLite store.

Not a pytest test — testing-conventions bars sockets from the unit suite,
and CLAUDE.md requires a block's first real external round trip to be
proved by a standalone script, output pasted into review.md's `## Evidence`.
`model_access.send()` is monkeypatched (a plain module-level reassignment)
to a canned, stateful response: it calls `plugin-d`'s entry tool on the
first turn, then a plain acknowledgement once a tool result is in context —
and is asserted never called a third time, since resuming a paused run must
never call the model at all. `builtins.input` is monkeypatched to always
approve, since the webhook path (`gateway_dispatch.handle_inbound`) uses
`run_graph`'s interactive default `approve` — a real gap this proof works
around rather than papers over (SAFETY doesn't exist yet; spec.md's own
Non-goals decline touching approval-gating here).
"""

from __future__ import annotations

import asyncio
import builtins
import http.server
import json
import os
import shutil
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

_STATE_DIR = tempfile.mkdtemp(prefix="sadana-wait-e2e-")
os.environ["SADANA_STATE_DIR"] = _STATE_DIR  # noqa: E402
# A copy, never the repo's own `tests/fixtures/plugins` in place:
# `client_surface.open_runtime()` seeds a `memory` plugin directory under
# whatever `SADANA_PLUGINS_DIR` resolves to, so pointing this at a tracked
# path would have the proof write into the repository it is proving.
# `tests/unit/test_subcommands_chat.py` fixes the same hazard the same way.
_PLUGINS_DIR = Path(_STATE_DIR) / "plugins"
shutil.copytree(Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "plugins", _PLUGINS_DIR)
os.environ["SADANA_PLUGINS_DIR"] = str(_PLUGINS_DIR)  # noqa: E402

from sadana import (  # noqa: E402
    channel_webhook,
    client_surface,
    gateway_daemon,
    gateway_dispatch,
    model_access,
    plugin_manifest,
)
from sadana.conversation_store import load_pause, open_store, store_path_from_config  # noqa: E402
from sadana.gateway import MessageEvent  # noqa: E402

HOST = "127.0.0.1"
PORT = 18767
SECRET = "prove-wait-e2e-secret"  # pragma: allowlist secret - fixed local test value, no real deployment
URL = f"http://{HOST}:{PORT}/webhook"
CHAT_ID = "wait-e2e-chat"

_send_calls = 0


def _canned_send(request: model_access.Request) -> model_access.Response:
    global _send_calls
    _send_calls += 1
    assert _send_calls <= 2, "the model must never be called a third time — resuming a pause calls no model at all"
    has_tool_result = any(m.get("role") == "tool" for m in request.messages)
    if has_tool_result:
        return model_access.Response(
            content="okay, I'll wait for it.", tool_calls=(), finish_reason="stop", usage=model_access.Usage()
        )
    return model_access.Response(
        content=None,
        tool_calls=({"function": {"name": "plugin_d_entry", "arguments": json.dumps({"job": "generate a report"})}},),
        finish_reason="tool_calls",
        usage=model_access.Usage(),
    )


def _post(text: str) -> tuple[int, dict]:
    body = json.dumps({"chat_id": CHAT_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        URL, data=body, method="POST", headers={"X-Sadana-Webhook-Secret": SECRET, "Content-Type": "application/json"}
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


def _drive_checks() -> None:
    print("waiting for the gateway to bind its socket...")
    _wait_for_port(HOST, PORT)
    print(f"[ok] gateway is listening on {HOST}:{PORT}")
    conn = open_store(store_path_from_config())
    key = f"webhook:{CHAT_ID}"

    print("\n=== POST 1: starts the job, the DAG pauses at the wait node ===")
    status1, body1 = _post("please start the job")
    print(f"status={status1} body={body1}")
    assert status1 == 200, f"expected 200, got {status1}"
    assert body1["ok"] is True, f"expected ok=true, got {body1}"
    # The HTTP response is the *model's own* final reply (it saw the pause's
    # "waiting for an external answer" text as its tool result, in context,
    # and chose to acknowledge it) — not the raw DagResult.text, which never
    # reaches the caller directly once run_turn loops back to the model.
    # The real proof the DAG actually paused is the plugin_pauses row below.
    assert body1["text"] == "okay, I'll wait for it.", body1["text"]
    print("[ok] the webhook response is the model's own acknowledgement")

    pause = load_pause(conn, conversation_key=key)
    assert pause is not None, "a real plugin_pauses row must exist after POST 1"
    assert pause.plugin == "plugin-d"
    assert pause.node == "await_answer"
    print(f"[ok] real plugin_pauses row: plugin={pause.plugin!r} node={pause.node!r}")

    print("\n=== POST 2: the external system's answer resumes the run, with no model call ===")
    status2, body2 = _post("42")
    print(f"status={status2} body={body2}")
    assert status2 == 200
    assert body2["ok"] is True
    assert body2["text"] == "the external job answered: 42", body2["text"]
    print("[ok] the resumed run reached its terminal node")

    assert load_pause(conn, conversation_key=key) is None, "the pause row must be gone after a terminal resume"
    print("[ok] the plugin_pauses row is gone")
    assert (
        _send_calls == 2
    ), f"the model should have been called exactly twice (never for the resume), got {_send_calls}"
    print(f"[ok] model_access.send was called exactly {_send_calls} times")

    print("\nALL ASSERTIONS PASSED")


def main() -> None:
    builtins.input = lambda *_args, **_kwargs: "y"  # type: ignore[assignment]
    model_access.send = _canned_send  # type: ignore[assignment]

    installed = plugin_manifest.discover_plugins()
    assert any(p.name == "plugin-d" for p in installed), f"plugin-d not found among {[p.name for p in installed]!r}"

    # One door, opened once — the plugin scan, the store and the memory schema
    # this script used to assemble itself (CLIENT-SURFACE-01). The scan above
    # stays: it is this script's own precondition check, not the assembly.
    runtime = client_surface.open_runtime(provider="p", model="m")

    def on_message(event: MessageEvent) -> tuple[bool, str]:
        return asyncio.run(gateway_dispatch.handle_inbound(runtime, event))

    def make_server() -> http.server.ThreadingHTTPServer:
        return channel_webhook.make_server(HOST, PORT, secret=SECRET, on_message=on_message)

    driver_thread = threading.Thread(target=_drive_checks)
    driver_thread.start()

    def _stop_after_driver() -> None:
        driver_thread.join()
        os.kill(os.getpid(), signal.SIGTERM)

    stopper_thread = threading.Thread(target=_stop_after_driver)
    stopper_thread.start()

    result = gateway_daemon.run(make_server=make_server, lock_filename="gateway-wait-e2e.lock")

    stopper_thread.join()
    assert result == 0, f"gateway_daemon.run() should return 0 on a clean SIGTERM stop, got {result}"
    print(f"[ok] gateway_daemon.run() returned {result} after a clean stop")


if __name__ == "__main__":
    main()
