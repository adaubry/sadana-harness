#!/usr/bin/env python3
"""Standalone proof that `sadana gateway install/start/stop/restart` really
installs, controls, and supervises the daemon as a real systemd service —
not the webhook/model round trip itself, which
`scripts/prove_gateway_webhook_e2e.py` already proves for real. Requires
root and a real systemd.

Not a pytest test — testing-conventions bars a real system path outside a
tmp directory from the unit suite, and this genuinely needs both root and a
real service manager. Run manually, paste its output into review.md's
## Evidence, matching CLAUDE.md's rule for a block's first real external
round trip.

**Deviates from plan.md's own "POST a real webhook request and assert
200"**: `install` launches `sadana gateway run` as a *separate*
systemd-managed OS process — this script cannot monkeypatch that process's
own `model_access.send` the way `prove_gateway_webhook_e2e.py` could in its
own single process, so a full successful turn would need a real
`OPENROUTER_API_KEY` and a real network call, which is not what this item
is proving. A wrong-secret request returning `401` proves the real,
systemd-installed process is genuinely bound and serving the real webhook
protocol, without needing a real model call — the full successful-turn
round trip is `GATEWAY-DAEMON-01`'s own already-proven job.

See docs/tasks/CLI-SHELL-05-gateway-lifecycle-verb/spec.md's acceptance
criteria for the contract this proves.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import gateway_service, gateway_unit  # noqa: E402

SECRET = "prove-gateway-lifecycle-e2e-secret"  # pragma: allowlist secret - fixed local test value
ENV_PATH = Path("/etc/sadana/gateway.env")
PORT = 18766
URL = f"http://127.0.0.1:{PORT}/webhook"


def _is_active() -> bool:
    result = gateway_service._run_systemctl(
        ["is-active", gateway_unit.SERVICE_NAME], check=False, capture_output=True, text=True
    )
    return result.stdout.strip() == "active"


def _main_pid() -> int:
    result = gateway_service._run_systemctl(
        ["show", "-p", "MainPID", "--value", gateway_unit.SERVICE_NAME], check=True, capture_output=True, text=True
    )
    return int(result.stdout.strip())


def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 10.0, interval_s: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def _post_wrong_secret() -> int:
    body = json.dumps({"chat_id": "e2e", "text": "hi"}).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=body,
        method="POST",
        headers={
            "X-Sadana-Webhook-Secret": "wrong",  # pragma: allowlist secret - a deliberately wrong test value
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed http://127.0.0.1 test URL
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> None:
    if os.geteuid() != 0:
        print("this script must run as root (sudo) — it installs a real systemd service", file=sys.stderr)
        raise SystemExit(1)

    print(f"=== writing a throwaway {ENV_PATH} ===")
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text(f"SADANA_GATEWAY_WEBHOOK_SECRET={SECRET}\nSADANA_GATEWAY_PORT={PORT}\n", encoding="utf-8")
    print(f"[ok] wrote {ENV_PATH}")

    try:
        print("\n=== install ===")
        result = gateway_service.install()
        assert result == 0, f"install() should return 0, got {result}"
        assert gateway_unit.unit_path().exists(), "unit file was not written"
        enabled = gateway_service._run_systemctl(
            ["is-enabled", gateway_unit.SERVICE_NAME], check=False, capture_output=True, text=True
        ).stdout.strip()
        assert enabled == "enabled", f"expected enabled, got {enabled!r}"
        print(f"[ok] {gateway_unit.unit_path()} installed and enabled")

        print("\n=== start ===")
        result = gateway_service.start()
        assert result == 0, f"start() should return 0, got {result}"
        assert _wait_until(_is_active), "service never became active"
        print("[ok] service is active")

        print("\n=== a real HTTP request against the systemd-supervised process ===")
        # Type=notify means "active" already implies the socket is bound and
        # the daemon called sd_notify(READY=1) — no port-polling wait needed.
        status = _post_wrong_secret()
        assert status == 401, f"expected 401 for a wrong secret, got {status}"
        print("[ok] the real, systemd-supervised process answered a real HTTP request (401 for a wrong secret)")

        print("\n=== stop ===")
        result = gateway_service.stop()
        assert result == 0, f"stop() should return 0, got {result}"
        assert _wait_until(lambda: not _is_active()), "service never became inactive"
        print("[ok] service is inactive")

        print("\n=== restart ===")
        result = gateway_service.restart()
        assert result == 0, f"restart() should return 0, got {result}"
        assert _wait_until(_is_active), "service never became active again after restart"
        print("[ok] service is active again after restart")

        print("\n=== kill -9 the main PID, prove Restart=on-failure recovers it ===")
        pid_before = _main_pid()
        print(f"killing PID {pid_before}...")
        os.kill(pid_before, 9)
        # MainPID reads 0 during the brief window systemd is still tearing
        # down/respawning — that's not a recovered process, so require a
        # genuinely new nonzero PID, not just "!= pid_before" (0 always
        # satisfies that trivially and would pass as a false positive).
        assert _wait_until(
            lambda: _is_active() and _main_pid() not in (0, pid_before), timeout_s=15.0
        ), "service did not recover after being killed"
        pid_after = _main_pid()
        assert pid_after not in (0, pid_before), f"recovered PID is not a real new process: {pid_after}"
        print(f"[ok] service recovered with a new PID {pid_after} (was {pid_before})")

        print("\nALL ASSERTIONS PASSED")
    finally:
        print("\n=== cleanup ===")
        gateway_service._run_systemctl(["stop", gateway_unit.SERVICE_NAME], check=False)
        gateway_service._run_systemctl(["disable", gateway_unit.SERVICE_NAME], check=False)
        gateway_unit.unit_path().unlink(missing_ok=True)
        gateway_service._run_systemctl(["daemon-reload"], check=False)
        ENV_PATH.unlink(missing_ok=True)
        print("[ok] cleanup complete — unit removed, daemon-reloaded, env file removed")


if __name__ == "__main__":
    main()
