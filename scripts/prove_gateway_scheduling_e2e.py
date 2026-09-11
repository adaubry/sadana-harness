#!/usr/bin/env python3
"""Standalone proof that a scheduled trigger starts a real conversation with
nobody asking: a real daemon, a real `tick()` against a real SQLite store,
and a real turn through `conversation.py`'s own machinery — no webhook call
anywhere in the path that fires it.

Not a pytest test — testing-conventions bars sockets and the wall clock from
the unit suite, and CLAUDE.md requires a block's first real external round
trip to be proved by a standalone script, output pasted into review.md's
`## Evidence`. `model_access.send()` is monkeypatched here (a plain
module-level reassignment) to a canned response — this script proves the
scheduling/persistence/turn path is real, not `model_access`'s own provider
round trip (already proven by MODEL-ACCESS-01's own evidence).

The real `gateway_daemon.run()`/`channel_webhook` lifecycle is exercised
alongside scheduling (matching `cmd_gateway_run`'s own wiring) even though
no HTTP request is made in this script — the interesting question this
proves is that the two coexist, not that either works alone (GATEWAY-DAEMON-01's
own webhook e2e proof already covers the daemon/socket path by itself).
`scheduling.tick()` is called directly from the driver thread rather than
via `run_tick_loop`'s own sleep loop, so firing assertions are deterministic
instead of racing a real background timer.
"""

from __future__ import annotations

import asyncio
import http.server
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ["SADANA_STATE_DIR"] = tempfile.mkdtemp(prefix="sadana-scheduling-e2e-")  # noqa: E402

from sadana import (  # noqa: E402
    channel_webhook,
    client_surface,
    gateway_daemon,
    model_access,
    scheduling,
)
from sadana.conversation_store import (  # noqa: E402
    due_triggers,
    upsert_scheduled_trigger,
)
from sadana.gateway import MessageEvent  # noqa: E402

HOST = "127.0.0.1"
PORT = 18766
SECRET = "prove-scheduling-e2e-secret"  # pragma: allowlist secret - fixed local test value, no real deployment


def _canned_send(request: model_access.Request) -> model_access.Response:
    return model_access.Response(
        content="scheduled reply", tool_calls=(), finish_reason="stop", usage=model_access.Usage()
    )


def _tick(runtime: client_surface.Runtime) -> int:
    return asyncio.run(scheduling.tick(runtime))


def _drive_checks(runtime: client_surface.Runtime) -> None:
    conn = runtime.conn
    print("=== registering one due-now trigger and one due-later trigger ===")
    now = time.time()
    upsert_scheduled_trigger(
        conn, name="due-now", trigger_text="hello from the schedule", next_run_at=now, interval_seconds=None
    )
    upsert_scheduled_trigger(
        conn, name="due-later", trigger_text="not yet", next_run_at=now + 3600.0, interval_seconds=None
    )
    print(f"[ok] registered; due_triggers(now) = {[t.name for t in due_triggers(conn, now=now)]!r}")

    print("\n=== tick #1: only the due-now trigger fires, with no webhook call ===")
    fired_count = _tick(runtime)
    print(f"[ok] tick fired {fired_count} trigger(s)")
    assert fired_count == 1, f"expected exactly 1 firing, got {fired_count}"

    remaining = {t.name for t in due_triggers(conn, now=now + 7200.0)}
    print(f"remaining triggers (as seen from far in the future): {remaining!r}")
    assert "due-now" not in remaining, "the one-shot trigger should be gone after firing"
    assert "due-later" in remaining, "the not-yet-due trigger should still exist"

    print("\n=== a long-overdue recurring trigger advances from *now*, not from its old next_run_at ===")
    upsert_scheduled_trigger(
        conn, name="weekly", trigger_text="weekly digest", next_run_at=0.0, interval_seconds=604800.0
    )
    before = time.time()
    fired_count2 = _tick(runtime)
    assert fired_count2 == 1
    (row,) = [t for t in due_triggers(conn, now=before + 604800.0 + 60.0) if t.name == "weekly"]
    print(f"weekly trigger's new next_run_at = {row.next_run_at!r}, tick started at {before!r}")
    assert row.next_run_at >= before + 604800.0 - 5, "next_run_at must be computed from *now*, not the old value"
    assert row.next_run_at < before + 604800.0 + 5, "a single missed week should not compound into a further delay"
    print("[ok] no backlog — advanced from the real time of firing, one interval forward")

    print("\nALL ASSERTIONS PASSED")


def main() -> None:
    model_access.send = _canned_send  # type: ignore[assignment]
    # One door, opened once — the store and the memory schema this script used
    # to assemble itself (CLIENT-SURFACE-01). `provider`/`model` are inert:
    # `model_access.send` is canned above, so nothing reaches a provider.
    runtime = client_surface.open_runtime(provider="p", model="m")

    def on_message(_event: MessageEvent) -> tuple[bool, str]:  # pragma: no cover - never invoked in this proof
        raise AssertionError("no webhook call should ever be made in this proof")

    def make_server() -> http.server.ThreadingHTTPServer:
        return channel_webhook.make_server(HOST, PORT, secret=SECRET, on_message=on_message)

    def driver() -> None:
        _drive_checks(runtime)
        print(f"\nsending SIGTERM to pid {os.getpid()} to stop the daemon cleanly...")
        os.kill(os.getpid(), signal.SIGTERM)

    driver_thread = threading.Thread(target=driver)
    driver_thread.start()

    result = gateway_daemon.run(make_server=make_server, lock_filename="gateway-scheduling-e2e.lock")

    driver_thread.join()
    assert result == 0, f"gateway_daemon.run() should return 0 on a clean SIGTERM stop, got {result}"
    print(f"[ok] gateway_daemon.run() returned {result} after a clean stop")


if __name__ == "__main__":
    main()
