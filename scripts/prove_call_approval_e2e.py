#!/usr/bin/env python3
"""Standalone proof of H18's whole point: a `call` node reached through the
webhook path parks instead of blocking a keyboard nobody is at, and is
answered from outside the process — through a real `sadana door serve`
HTTP server, with a real signed token — never by the process that parked it.

Not a pytest test — testing-conventions bars sockets from the unit suite,
and CLAUDE.md requires a block's first real external round trip to be
proved by a standalone script, output pasted into review.md's `## Evidence`.

Two halves, deliberately different transports, matching the work item's own
acceptance test: `gateway_dispatch.handle_inbound()` is called directly (the
same function `channel_webhook.py`'s own HTTP handler calls — "the webhook
path" without a second real socket to stand it up) to park the run
non-interactively; the door is a real `http.server.ThreadingHTTPServer`,
approved over a real `urllib.request` POST carrying a token
`door.auth.mint_token` actually signed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_STATE_DIR = tempfile.mkdtemp(prefix="sadana-call-approval-e2e-")
os.environ["SADANA_STATE_DIR"] = _STATE_DIR  # noqa: E402
# A throwaway plugin, not `tests/fixtures/plugins` in place: this proof only
# needs one `call` node, and keeping it out of the tracked fixture tree
# means it never has to agree with what other fixtures there declare.
_PLUGINS_DIR = Path(_STATE_DIR) / "plugins"
_PLUGIN_DIR = _PLUGINS_DIR / "p"
_PLUGIN_DIR.mkdir(parents=True)
_PLUGIN_DIR.joinpath("init.py").write_text("def do_call(value):\n    return f'handled: {value}'\n")
_PLUGIN_DIR.joinpath("s.json").write_text(json.dumps({"type": "object"}))
_PLUGIN_DIR.joinpath("plugin.toml").write_text(
    '[plugin]\nname = "p"\nversion = "0.1.0"\ndescription = "a single call node, for H18\'s own proof"\n\n'
    '[[entry]]\ntool = "reach_out"\npurpose = "reaches outside, once approved"\nparameters = "s.json"\n'
    'start = "reach_out"\n\n'
    '[[node]]\nname = "reach_out"\nkind = "call"\nbody = "init:do_call"\n'
)
os.environ["SADANA_PLUGINS_DIR"] = str(_PLUGINS_DIR)  # noqa: E402

from sadana import client_surface, gateway_dispatch, model_access  # noqa: E402
from sadana.conversation_store import get_approval, list_approvals, load_pause  # noqa: E402
from sadana.door import auth, capabilities  # noqa: E402
from sadana.door.nouns import approvals as door_approvals  # noqa: E402
from sadana.door.router import DoorContext  # noqa: E402
from sadana.door.serve import make_server  # noqa: E402
from sadana.gateway import MessageEvent  # noqa: E402

HOST = "127.0.0.1"
PORT = 18789
HARNESS_ID = "hrn_prove_h18"
CHAT_ID = "call-approval-e2e-chat"
CONVERSATION_KEY = f"webhook:{CHAT_ID}"


def _canned_send(request: model_access.Request) -> model_access.Response:
    has_tool_result = any(m.get("role") == "tool" for m in request.messages)
    if has_tool_result:
        return model_access.Response(content="noted.", tool_calls=(), finish_reason="stop", usage=model_access.Usage())
    return model_access.Response(
        content=None,
        tool_calls=({"function": {"name": "reach_out", "arguments": json.dumps({"job": "call it"})}},),
        finish_reason="tool_calls",
        usage=model_access.Usage(),
    )


def _wait_for_port(host: str, port: int, *, timeout_s: float = 5.0) -> None:
    import socket

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"the door never bound {host}:{port}")


def _row_as_json(row: object) -> str:
    return json.dumps(
        {f: getattr(row, f) for f in ("id", "conversation_key", "kind", "state", "answered_by", "answer", "version")},
        default=str,
    )


def main() -> None:
    model_access.send = _canned_send  # type: ignore[assignment]

    print("=== parking a real `call` node through the webhook path ===")
    runtime = client_surface.open_runtime(provider="p", model="m")
    event = MessageEvent(platform="webhook", chat_id=CHAT_ID, thread_id=None, text="please reach out")
    ok, text = asyncio.run(gateway_dispatch.handle_inbound(runtime, event))
    print(f"handle_inbound -> ok={ok} text={text!r}")
    assert ok is True, "the turn itself must complete — parking is not a turn failure"

    pause = load_pause(runtime.connections.reader(), conversation_key=CONVERSATION_KEY)
    assert pause is not None, "a real plugin_pauses row must exist after parking"
    assert pause.kind == "call", f"expected a call-kind pause, got {pause.kind!r}"
    print(f"[ok] real plugin_pauses row: plugin={pause.plugin!r} node={pause.node!r} kind={pause.kind!r}")

    seeded = next(r for r in list_approvals(runtime.connections.reader()) if r.conversation_key == CONVERSATION_KEY)
    approval_before = get_approval(runtime.connections.reader(), id=seeded.id)
    assert approval_before is not None
    print("\n=== the approvals row before anyone answered it ===")
    print(_row_as_json(approval_before))
    assert approval_before.state == "waiting"

    print("\n=== starting a real `sadana door serve` process (in-thread) ===")
    private_pem, jwk = auth.generate_dev_keypair("prove")
    jwks_path = Path(_STATE_DIR) / "jwks.json"
    jwks_path.write_text(json.dumps(auth.jwks_document(jwk)))
    verifier = auth.Verifier(auth.JwksSource(path=jwks_path))
    ctx = DoorContext(
        conns=runtime.connections,
        runtime=auth.BoxIdentity(harness_id=HARNESS_ID, org=None),
        verifier=verifier,
        capabilities=capabilities.declared(),
        nouns={"approvals": door_approvals},
        clock=time.time,
    )
    server = make_server(HOST, PORT, ctx=ctx)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        _wait_for_port(HOST, PORT)
        print(f"[ok] the door is listening on http://{HOST}:{PORT}/")

        token = auth.mint_token(
            private_pem,
            "prove",
            sub="prover",
            org=None,
            ws="ws1",
            hrn=HARNESS_ID,
            scope=["approvals:approve"],
            ttl=300,
        )

        print("\n=== approving it over real HTTP, from a different transport than the one that parked it ===")
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/v1/approvals/{approval_before.id}/actions/approve",
            data=b"{}",
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Sadana-Harness": HARNESS_ID,
                "Content-Type": "application/json",
                "If-Match": f'"{approval_before.version}"',
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed http://127.0.0.1 proof URL
            status = resp.status
            body = json.loads(resp.read())
        print(f"POST .../actions/approve -> status={status} body={body}")
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["state"] == "approved", body
    finally:
        server.shutdown()
        server_thread.join(timeout=5)

    approval_after = get_approval(runtime.connections.reader(), id=approval_before.id)
    assert approval_after is not None
    print("\n=== the approvals row after ===")
    print(_row_as_json(approval_after))
    assert approval_after.state == "approved"
    assert approval_after.answered_by == "console:prover"
    assert load_pause(runtime.connections.reader(), conversation_key=CONVERSATION_KEY) is None
    print("[ok] the pause is cleared — the call node's body ran, off a real HTTP request")

    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
