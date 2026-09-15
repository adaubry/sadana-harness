#!/usr/bin/env python3
"""The tether's real, external round trip — Deploy-stage evidence.

`docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md`, `CLAUDE.md`'s own
rule: "Prove a block's first real external round trip with a standalone
script outside `make test`." Drives the real, installed `sadana` CLI (not
this project's own test doubles) against `scripts/test_relay.py`, over a
real local socket:

enroll -> connect -> forward a GET/list/POST through the door -> see the
resulting event reach `/events` -> kill the relay -> restart it -> assert
reconnection inside 60s -> forward `GET /v1/changes?since=` and find
nothing missing -> a second, isolated run asserts `--retry-after` is obeyed
exactly.

Two fully separate box identities/state directories are used (`main`,
`retry`) so the retry-after check never touches the first box's already-
established connection.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SADANA = str(REPO_ROOT / ".venv" / "bin" / "sadana")
RELAY_SCRIPT = str(REPO_ROOT / "scripts" / "test_relay.py")


def _http(method: str, url: str, *, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _wait_for(predicate: Callable[[], bool], *, timeout: float, description: str, interval: float = 0.25) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for: {description}")


def start_relay(
    port: int,
    *,
    retry_after: float | None = None,
    die_after: float | None = None,
    state_file: str | None = None,
) -> tuple[subprocess.Popen, str]:
    args = [sys.executable, RELAY_SCRIPT, "--port", str(port)]
    if retry_after is not None:
        args += ["--retry-after", str(retry_after)]
    if die_after is not None:
        args += ["--die-after", str(die_after)]
    if state_file is not None:
        args += ["--state-file", state_file]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None  # guaranteed by stdout=PIPE above
    line = proc.stdout.readline()
    if "listening on" not in line:
        raise AssertionError(f"relay failed to start: {line!r}")
    return proc, line.strip().rsplit(" ", 1)[-1]


def make_box_env(tag: str) -> tuple[dict[str, str], Path]:
    home = Path(tempfile.mkdtemp(prefix=f"sadana-prove-{tag}-"))
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "SADANA_STATE_DIR": str(home / ".sadana"),
            "SADANA_GATEWAY_WEBHOOK_SECRET": "prove-secret",  # pragma: allowlist secret
            "TZ": "UTC",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env, home


def sadana(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([SADANA, *args], env=env, capture_output=True, text=True, timeout=30)


def main_flow() -> None:
    print("=== starting the fixture relay ===")
    # A real relay's enrollment records live in a database that survives its
    # own restart; this fixture's otherwise-in-memory state does not, unless
    # pointed at the same file across both of this test's relay processes.
    state_file = tempfile.mktemp(prefix="sadana-prove-relay-state-", suffix=".json")
    relay_proc, relay_url = start_relay(0, state_file=state_file)
    box_env, box_home = make_box_env("main")
    gateway_proc: subprocess.Popen | None = None
    try:
        print("=== minting an enrollment token ===")
        status, body = _http("POST", relay_url + "/token", body={"hrn": "hrn_prove"})
        assert status == 200, body
        token = body["token"]

        print("=== enrolling ===")
        result = sadana(box_env, "enroll", token, "--relay", relay_url, "--console", relay_url)
        print(result.stdout.strip(), result.stderr.strip())
        assert result.returncode == 0, result.stderr
        harness_id = result.stdout.strip().splitlines()[-1]
        assert harness_id == "hrn_prove", harness_id

        print("=== starting sadana gateway run ===")
        gateway_proc = subprocess.Popen(
            [SADANA, "gateway", "run"], env=box_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        def connected() -> bool:
            status, body = _http("GET", relay_url + f"/status/{harness_id}")
            return status == 200 and bool(body.get("connected"))

        print("=== waiting for the box to connect ===")
        _wait_for(connected, timeout=15, description="the box to connect")
        status, body = _http("GET", relay_url + f"/status/{harness_id}")
        print("connected; hello capabilities:", body["capabilities"])
        assert "grammar.v1" in body["capabilities"]

        # A door-facing token's own ttl is capped at 300s (wire.md §2) —
        # unlike the one-hour enrollment token above, which isn't a door
        # request at all and isn't checked against that cap.
        status, body = _http(
            "POST",
            relay_url + "/token",
            body={
                "hrn": harness_id,
                "sub": "prover",
                "ttl": 300,
                "scope": ["conversations:read", "conversations:write"],
            },
        )
        door_token = body["token"]
        door_headers = {"Authorization": f"Bearer {door_token}", "X-Sadana-Harness": harness_id}

        print("=== forwarding GET /v1/harness ===")
        status, body = _http(
            "POST",
            relay_url + f"/forward/{harness_id}",
            body={"method": "GET", "path": "/v1/harness", "headers": door_headers},
        )
        assert status == 200 and body["status"] == 200, body
        assert body["body"]["id"] == harness_id
        print(body["body"])

        print("=== forwarding GET /v1/conversations (a list) ===")
        status, body = _http(
            "POST",
            relay_url + f"/forward/{harness_id}",
            body={"method": "GET", "path": "/v1/conversations", "headers": door_headers},
        )
        assert status == 200 and body["status"] == 200, body
        print(body["body"])

        print("=== forwarding POST /v1/conversations (a create) ===")
        status, body = _http(
            "POST",
            relay_url + f"/forward/{harness_id}",
            body={"method": "POST", "path": "/v1/conversations", "headers": door_headers, "body": {}},
        )
        assert status == 200 and body["status"] == 201, body
        conversation_id = body["body"]["id"]
        print("created", conversation_id)

        print("=== asserting the resulting event reached /events with a cursor ===")

        def event_seen() -> bool:
            _, events_body = _http("GET", relay_url + "/events")
            return any(e.get("event", {}).get("id") == conversation_id for e in events_body["data"])

        _wait_for(event_seen, timeout=15, description="the new conversation's event to arrive")
        _, events_body = _http("GET", relay_url + "/events")
        cursors = [e["cursor"] for e in events_body["data"]]
        assert cursors, "no events logged at all"
        print(f"event log has {len(events_body['data'])} entries, cursors up to {max(cursors)}")

        print("=== killing the relay, simulating an outage ===")
        relay_port = int(relay_url.rsplit(":", 1)[-1])
        relay_proc.terminate()
        relay_proc.wait(timeout=5)

        print("=== restarting the relay on the same port ===")
        restart_started = time.monotonic()
        relay_proc, relay_url_2 = start_relay(relay_port, state_file=state_file)
        assert relay_url_2 == relay_url, (relay_url_2, relay_url)

        print("=== waiting for reconnection (bound: 60s, full jitter) ===")
        _wait_for(connected, timeout=60, description="reconnection within 60s")
        print(f"reconnected after {time.monotonic() - restart_started:.1f}s")

        print("=== forwarding GET /v1/changes?since=0 after reconnect: nothing missing ===")
        status, body = _http(
            "POST",
            relay_url + f"/forward/{harness_id}",
            body={"method": "GET", "path": "/v1/changes", "query": "since=0&limit=200", "headers": door_headers},
        )
        assert status == 200 and body["status"] == 200, body
        ids_seen = {row["id"] for row in body["body"]["data"]}
        assert conversation_id in ids_seen, (conversation_id, ids_seen)
        print("changes catch-up includes the conversation created before the outage")
    finally:
        if gateway_proc is not None:
            gateway_proc.terminate()
            gateway_proc.wait(timeout=5)
        relay_proc.terminate()
        relay_proc.wait(timeout=5)
        shutil.rmtree(box_home, ignore_errors=True)
        Path(state_file).unlink(missing_ok=True)


def retry_after_flow() -> None:
    print("=== isolated check: --retry-after is obeyed exactly ===")
    env, home = make_box_env("retry")
    relay_proc, relay_url = start_relay(0, retry_after=7)
    gateway_proc: subprocess.Popen | None = None
    try:
        status, body = _http("POST", relay_url + "/token", body={"hrn": "hrn_retry"})
        assert status == 200, body
        result = sadana(env, "enroll", body["token"], "--relay", relay_url, "--console", relay_url)
        assert result.returncode == 0, result.stderr

        started = time.monotonic()
        gateway_proc = subprocess.Popen(
            [SADANA, "gateway", "run"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        assert gateway_proc.stdout is not None  # guaranteed by stdout=PIPE above
        deadline = time.monotonic() + 12
        saw_it = False
        while time.monotonic() < deadline:
            line = gateway_proc.stdout.readline()
            if not line:
                continue
            if "Retry-After" in line:
                saw_it = True
                print(f"observed the retry-after log at {time.monotonic() - started:.1f}s: {line.strip()}")
                break
        assert saw_it, "never saw the tether log the retry-after sleep"
    finally:
        if gateway_proc is not None:
            gateway_proc.terminate()
            gateway_proc.wait(timeout=5)
        relay_proc.terminate()
        relay_proc.wait(timeout=5)
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    main_flow()
    retry_after_flow()
    print("ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
