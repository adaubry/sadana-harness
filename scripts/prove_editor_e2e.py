#!/usr/bin/env python3
"""Standalone proof that the plugin editor's server is real: a real bound
socket, real HTTP round trips, and a real `plugin.toml` on disk that parses
back to exactly what the browser was last shown.

Not a pytest test — testing-conventions bars sockets from the unit suite, and
CLAUDE.md requires a block's first real external round trip to be proved by a
standalone script whose output is pasted into review.md's `## Evidence`.

What this proves and what it does not: the server, the routing, the writing,
and the round trip are exercised for real. The page's own drag-and-drop is
not — there is no browser here, and the honest claim is therefore "the page
is served and the API it talks to is proven," never "dragging a box works."
`docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save/plan.md` says so in its Risks
section, and this script does not pretend otherwise.
"""

from __future__ import annotations

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

_STATE = tempfile.mkdtemp(prefix="sadana-editor-e2e-")
os.environ["SADANA_STATE_DIR"] = _STATE  # noqa: E402
os.environ["SADANA_PLUGINS_DIR"] = str(Path(_STATE) / "plugins")  # noqa: E402

from sadana import editor_server, gateway_daemon, plugins  # noqa: E402

HOST = "127.0.0.1"
PORT = 18771
ROOT = Path(os.environ["SADANA_PLUGINS_DIR"])


def _request(method: str, path: str, payload: object = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(f"http://{HOST}:{PORT}{path}", data=body, method=method)
    if body:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed 127.0.0.1 test URL
            raw = response.read()
            kind = response.headers.get("Content-Type", "")
            return response.status, (json.loads(raw) if kind.startswith("application/json") else {"bytes": len(raw)})
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _wait_for_port(timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"editor never bound {HOST}:{PORT}")


def _on_disk(name: str) -> plugins.Manifest:
    return plugins._parse_manifest((ROOT / name / "plugin.toml").read_text(encoding="utf-8"))


def _drive() -> None:
    print("waiting for the editor to bind its socket...")
    _wait_for_port()
    print(f"[ok] editor is listening on {HOST}:{PORT}")

    print("\n=== the page and its files are served ===")
    for path in ("/", "/assets/editor.js", "/assets/editor.css"):
        status, body = _request("GET", path)
        print(f"GET {path} -> {status} {body}")
        assert status == 200, f"{path} returned {status}"

    print("\n=== the palette is the declared vocabulary ===")
    status, body = _request("GET", "/api/kinds")
    offered = {entry["kind"]: entry["runnable"] for entry in body["kinds"]}
    print(f"kinds offered: {offered}")
    assert set(offered) == set(plugins.NODE_KINDS), offered
    assert offered["each"] is False, "the loop step must be marked not-yet-runnable"
    assert offered["wait"] is True

    print("\n=== creating a plugin from nothing ===")
    status, created = _request("POST", "/api/plugins", {"name": "drawn-by-hand"})
    print(f"status={status} problems={created['problems']} waiting={created['waiting']}")
    assert status == 201, created
    assert created["problems"] == [] and created["waiting"] == [], "a new plugin must be valid immediately"
    assert (ROOT / "drawn-by-hand" / "schema").is_dir(), "the entry's schema file must be scaffolded"

    print("\n=== it appears in the list, ready ===")
    _, listed = _request("GET", "/api/plugins")
    print(f"plugins: {listed['plugins']}")
    assert listed["plugins"] == [{"name": "drawn-by-hand", "status": "ready"}]

    print("\n=== drawing: turn the first step into an ask, add a second, join them ===")
    manifest = created["manifest"]
    manifest["nodes"][0].update({"kind": "ask", "skill": "some-skill", "next": "step_2"})
    skill = ROOT / "drawn-by-hand" / "skills" / "some-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: some-skill\ndescription: d\n---\nDo the thing.\n")
    manifest["nodes"].append({"name": "step_2", "kind": "stop", "body": None, "skill": None, "next": None, "ports": []})
    status, saved = _request("PUT", "/api/plugins/drawn-by-hand", manifest)
    print(f"status={status} steps={[n['name'] for n in saved['manifest']['nodes']]} positions={saved['positions']}")
    assert status == 200, saved
    assert saved["positions"]["step_2"][0] > saved["positions"]["step_1"][0], "a successor sits to the right"

    print("\n=== the file on disk is what the browser was shown ===")
    from_browser = plugins.manifest_from_dict(saved["manifest"])
    from_disk = _on_disk("drawn-by-hand")
    print(f"on disk: {from_disk}")
    assert from_disk == from_browser, "the round trip lost or changed something"

    print("\n=== reopening shows the same thing back ===")
    _, reopened = _request("GET", "/api/plugins/drawn-by-hand")
    assert reopened["manifest"] == saved["manifest"], "reopening drew something different"
    assert reopened["positions"] == saved["positions"], "positions are not stable across reads"
    print("[ok] identical manifest and identical positions, with nothing stored to make it so")

    print("\n=== an unfinished plugin saves, and says what is waiting on a programmer ===")
    manifest = reopened["manifest"]
    manifest["nodes"][1].update({"kind": "compute", "body": "init:not_written_yet"})
    status, unfinished = _request("PUT", "/api/plugins/drawn-by-hand", manifest)
    print(f"status={status} problems={unfinished['problems']} waiting={unfinished['waiting']}")
    assert status == 200, unfinished
    assert unfinished["waiting"] == [{"step": "step_2", "for": "code"}], unfinished
    assert unfinished["problems"] == [], "a step waiting on code is a to-do, not a problem"
    assert _on_disk("drawn-by-hand").nodes[1].body == "init:not_written_yet", "it was still written to disk"

    print("\n=== a drawing mistake is reported, not refused ===")
    manifest["nodes"][1]["next"] = "no-such-step"
    status, broken = _request("PUT", "/api/plugins/drawn-by-hand", manifest)
    print(f"status={status} problems={broken['problems']}")
    assert status == 200 and broken["problems"], broken

    print("\n=== a name that tries to escape the plugins directory is refused ===")
    for name in ("../escape", "..", "/etc/passwd"):
        status, refused = _request("POST", "/api/plugins", {"name": name})
        print(f"POST name={name!r} -> {status} {refused.get('error')}")
        assert status == 400, refused
    assert sorted(p.name for p in ROOT.iterdir()) == ["drawn-by-hand"], "something was written outside"
    assert not (Path(_STATE) / "escape").exists()

    print("\nALL ASSERTIONS PASSED")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)

    def make_server():  # type: ignore[no-untyped-def]
        return editor_server.make_server(HOST, PORT, plugins_root=ROOT)

    driver = threading.Thread(target=_drive)
    driver.start()

    def stop_when_done() -> None:
        driver.join()
        os.kill(os.getpid(), signal.SIGTERM)

    stopper = threading.Thread(target=stop_when_done)
    stopper.start()

    result = gateway_daemon.run(make_server=make_server, lock_filename="editor-e2e.lock")

    stopper.join()
    assert result == 0, f"gateway_daemon.run() should return 0 on a clean SIGTERM stop, got {result}"
    print(f"[ok] gateway_daemon.run() returned {result} after a clean stop")


if __name__ == "__main__":
    main()
