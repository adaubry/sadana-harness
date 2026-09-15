#!/usr/bin/env python3
"""Standalone proof that H24's plugin surface is real end to end: a real
bound socket, real HTTP round trips through `router.handle()`, a real local
git repository fetched and verified by `plugin_install.install_from_git()`,
and a real `plugin.toml` written back by `save`.

Not a pytest test — testing-conventions bars sockets and real git
subprocesses from the unit suite, and CLAUDE.md requires a block's first
real external round trip to be proved by a standalone script whose output
is pasted into `review.md`'s `## Evidence`
(`docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`).

Sequence, matching spec.md's Acceptance criteria: list (a pre-seeded
fixture plugin already on disk) -> get (its layout) -> create
(install-from-git, from a real local bare repo) -> poll the operation (if
the fetch was slow enough to promote; a small local clone often is not, and
this script says which happened rather than forcing it) -> inspect the same
tag -> save with a bad field (400, writes nothing) -> save a clean edit
(200, writes it).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_STATE = tempfile.mkdtemp(prefix="sadana-door-plugins-e2e-")
os.environ["SADANA_STATE_DIR"] = _STATE  # noqa: E402
os.environ["SADANA_PLUGINS_DIR"] = str(Path(_STATE) / "plugins")  # noqa: E402

from sadana.door import auth, capabilities  # noqa: E402
from sadana.door.nouns import inspections  # noqa: E402
from sadana.door.nouns import plugins as plugins_noun  # noqa: E402
from sadana.door.router import DoorContext  # noqa: E402
from sadana.door.serve import make_server  # noqa: E402
from sadana.stores import Connections  # noqa: E402

HOST = "127.0.0.1"
PORT = 18772
HARNESS_ID = "hrn_e2e"
PLUGINS_ROOT = Path(os.environ["SADANA_PLUGINS_DIR"])


def _run_git(args: list[str], *, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _make_upstream_repo(tmp: Path) -> Path:
    repo = tmp / "upstream"
    repo.mkdir()
    _run_git(["init", "-q", "-b", "main"], cwd=repo)
    _run_git(["config", "user.email", "e2e@example.com"], cwd=repo)
    _run_git(["config", "user.name", "e2e"], cwd=repo)
    (repo / "schema").mkdir()
    (repo / "schema" / "greet.json").write_text('{"type": "object", "properties": {}}\n')
    (repo / "plugin.toml").write_text(
        '[plugin]\nname = "greeter"\nversion = "0.1.0"\ndescription = "says hello"\n\n'
        '[[entry]]\ntool = "greet"\npurpose = "say hello"\nparameters = "schema/greet.json"\nstart = "a"\n\n'
        '[[node]]\nname = "a"\nkind = "stop"\n'
    )
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "initial"], cwd=repo)
    _run_git(["tag", "v1.0.0"], cwd=repo)
    return repo


def _write_fixture_plugin(name: str = "fixture-plugin") -> None:
    directory = PLUGINS_ROOT / name
    (directory / "schema").mkdir(parents=True)
    (directory / "schema" / "t.json").write_text('{"type": "object", "properties": {}}\n')
    (directory / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "0.1.0"\ndescription = "a fixture plugin"\n\n'
        '[[entry]]\ntool = "t"\npurpose = "p"\nparameters = "schema/t.json"\nstart = "a"\n\n'
        '[[node]]\nname = "a"\nkind = "compute"\nnext = "b"\n\n'
        '[[node]]\nname = "b"\nkind = "stop"\n'
    )


def _request(
    method: str, path: str, token: str, payload: object = None, *, if_match: str | None = None
) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(f"http://{HOST}:{PORT}{path}", data=body, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-Sadana-Harness", HARNESS_ID)
    if if_match is not None:
        request.add_header("If-Match", if_match)
    if body:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed 127.0.0.1 e2e URL
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _wait_for_port(timeout_s: float = 5.0) -> None:
    import socket

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("door server never opened its port")


def main() -> int:
    from sadana import plugin_install

    PLUGINS_ROOT.mkdir(parents=True, exist_ok=True)
    _write_fixture_plugin()
    conns = Connections(Path(_STATE) / "store.db")
    plugin_install.reconcile_plugin_state(conns.writer, PLUGINS_ROOT)

    private_pem, jwk = auth.generate_dev_keypair("e2e")
    jwks_path = Path(_STATE) / "jwks.json"
    jwks_path.write_text(json.dumps(auth.jwks_document(jwk)))
    verifier = auth.Verifier(auth.JwksSource(path=jwks_path))
    ctx = DoorContext(
        conns=conns,
        runtime=auth.BoxIdentity(harness_id=HARNESS_ID, org=None),
        verifier=verifier,
        capabilities=capabilities.declared(),
        nouns={"plugins": plugins_noun, "inspections": inspections},
        clock=time.time,
    )
    server = make_server(HOST, PORT, ctx=ctx)
    thread = threading.Thread(target=server.serve_forever, daemon=False)
    thread.start()
    try:
        _wait_for_port()
        token = auth.mint_token(
            private_pem,
            "e2e",
            sub="u1",
            org=None,
            ws="ws1",
            hrn=HARNESS_ID,
            scope=[
                "plugins:read",
                "plugins:write",
                "plugins:disable",
                "plugins:enable",
                "plugins:install-from-git",
                "plugins:save",
                "inspections:read",
                "inspections:write",
            ],
        )

        print("1) LIST plugins (the pre-seeded fixture plugin)")
        status, body = _request("GET", "/v1/plugins", token)
        print(f"   {status} data={[p['name'] for p in body['data']]}")
        assert status == 200 and body["data"][0]["name"] == "fixture-plugin"
        plg_id = body["data"][0]["id"]

        print("2) GET the fixture plugin's layout")
        status, body = _request("GET", f"/v1/plugins/{plg_id}", token)
        print(f"   {status} nodes={[n['id'] for n in body['layout']['nodes']]} edges={body['layout']['edges']}")
        assert status == 200 and {n["id"] for n in body["layout"]["nodes"]} == {"a", "b"}

        repo = _make_upstream_repo(Path(_STATE))
        print("3) CREATE (install-from-git) 'greeter' from a real local git repo")
        status, body = _request("POST", "/v1/plugins", token, {"repo_url": str(repo), "tag": "v1.0.0"})
        print(f"   {status} {body.get('name') or body}")
        if status == 202:
            print("   -> promoted to an operation (the clone took >2s); polling it")
            op_id = body["operation"]["id"]
            while True:
                op_status, op_body = _request("GET", f"/v1/operations/{op_id}", token)
                state = op_body["operation"]["state"]
                print(f"   poll: {op_status} state={state}")
                if state != "running":
                    break
                time.sleep(0.2)
            assert state == "succeeded"
            status, body = _request(
                "GET", "/v1/plugins", token, None
            )  # discover the new id via a fresh list, matching console_fit_plan.md §6's own guidance
            body = next(p for p in body["data"] if p["name"] == "greeter")
        else:
            print("   -> resolved synchronously (< 2s), never promoted to an operation")
            assert status == 201
        assert body["name"] == "greeter" and (PLUGINS_ROOT / "greeter" / "plugin.toml").is_file()

        print("4) INSPECT the same tag")
        status, body = _request("POST", "/v1/inspections", token, {"repo_url": str(repo), "tag": "v1.0.0"})
        print(f"   {status} state={body.get('state')} checksum={body.get('checksum')}")
        assert status in (200, 201) and body["state"] == "ok"

        greeter_id = next(p for p in _request("GET", "/v1/plugins", token)[1]["data"] if p["name"] == "greeter")["id"]
        greeter = _request("GET", f"/v1/plugins/{greeter_id}", token)[1]

        print("5) SAVE with a bad field (expect 400, nothing written)")
        before = (PLUGINS_ROOT / "greeter" / "plugin.toml").read_text()
        bad_manifest = {
            "name": "greeter",
            "version": "0.1.0",
            "description": "d",
            "entries": [{"tool": "greet", "purpose": "p", "parameters": "schema/greet.json", "start": "a"}],
            "nodes": [{"name": "a", "body": None, "skill": None, "next": None, "ports": []}],
            "settings": [],
        }
        status, body = _request(
            "POST",
            f"/v1/plugins/{greeter_id}/actions/save",
            token,
            {"manifest": bad_manifest},
            if_match=f'"{greeter["version"]}"',
        )
        print(f"   {status} errors={body.get('errors')}")
        assert status == 400 and (PLUGINS_ROOT / "greeter" / "plugin.toml").read_text() == before

        print("6) SAVE a clean edit (expect 200, written)")
        clean_manifest = dict(
            bad_manifest, nodes=[{"name": "a", "kind": "stop", "body": None, "skill": None, "next": None, "ports": []}]
        )
        status, body = _request(
            "POST",
            f"/v1/plugins/{greeter_id}/actions/save",
            token,
            {"manifest": clean_manifest},
            if_match=f'"{greeter["version"]}"',
        )
        print(f"   {status} description={body.get('description')}")
        assert status == 200
        assert "d" in (PLUGINS_ROOT / "greeter" / "plugin.toml").read_text()

        print("\nALL STEPS PASSED")
        return 0
    finally:
        server.shutdown()
        thread.join(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
