#!/usr/bin/env python3
"""Standalone proof that H14's own central promise holds for the one field
it is actually true for: a `providers.update` through the door — changing
`credential_ref` or `base_url` — reaches an *already-open*
`client_surface.Runtime`'s very next call, with no restart.

Not a pytest test — a live turn needs a real running `Runtime`, which the
unit and contract suites deliberately stub (`plan.md`'s own Order of work,
step 6). This script opens one for real: a real `Connections`, a real
`config.toml`/`.env` under an isolated `state_dir`/`config_dir`, and the
real `openrouter` provider code path (`_build_request`,
`chat_completions_url`) reading them live. Only the actual socket (that
provider's own `_post`, resolved through the registered `request_fn`'s
`__module__` rather than a dotted import — `model_access._discover()`
loads each `provider.py` via `importlib.util.spec_from_file_location`
under a synthetic module name, never as a real package, so a plain
`import` would patch a second, unused module instance) is stubbed, to
prove the config plumbing without needing a real `OPENROUTER_API_KEY` or
network access — the same posture `docs/tasks/
H14-tuned-config-settings-secrets/spec.md`'s own Concerns section takes
for a check that must not depend on what it cannot control.

Sequence: open a `Runtime`; take one turn, capturing the outgoing request's
`Authorization` header and URL; `PUT` a new secret and `PATCH /v1/providers`
through `router.handle()` (no socket — the same "a pure `handle()`" posture
`console_fit_plan.md` §5(e) already commits the door to) to point
`credential_ref` at it and change `base_url`; take a second turn on the
*same* `Runtime`, with no restart; assert the second request used the new
credential and the new URL.

Also demonstrates, and states plainly, the one field this does NOT prove:
`providers.update`'s own `model` field reaches the *next* `open_runtime()`
call, not this already-open one — `client_surface.Runtime.provider`/
`.model` are resolved once, at open time, by `CLIENT-SURFACE-01`'s own
settled design, which this work item does not reopen. See `spec.md`'s own
corrected Acceptance criterion and `plan.md`'s `## Risks` item 13.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_STATE = tempfile.mkdtemp(prefix="sadana-h14-no-restart-")
os.environ["SADANA_STATE_DIR"] = str(Path(_STATE) / "state")  # noqa: E402
os.environ["XDG_CONFIG_HOME"] = str(Path(_STATE) / "config")  # noqa: E402
os.environ["XDG_STATE_HOME"] = str(Path(_STATE) / "state")  # noqa: E402
# A real value from the start, deliberately: `send()`'s own credential
# preflight checks `manifest.env_vars`' static name (`OPENROUTER_API_KEY`)
# regardless of what `credential_ref` is configured to
# (`model_access.py`'s own documented limitation) — this proof rotates
# *which secret `credential_ref` names*, not whether one exists at all.
os.environ["OPENROUTER_API_KEY"] = "sk-original-value"  # noqa: E402  # pragma: allowlist secret
os.environ.pop("SADANA_MODEL_ACCESS_PROVIDER", None)  # noqa: E402
os.environ.pop("SADANA_MODEL_ACCESS_MODEL", None)  # noqa: E402

from sadana import client_surface, model_access  # noqa: E402
from sadana.door import auth, capabilities  # noqa: E402
from sadana.door.nouns import providers as providers_noun  # noqa: E402
from sadana.door.nouns import secrets as secrets_noun  # noqa: E402
from sadana.door.router import DoorContext, handle  # noqa: E402


def _request(
    ctx: DoorContext,
    token: str,
    method: str,
    path: str,
    body: dict | None = None,
    if_match: str | None = None,
):
    from sadana.door import request as door_request

    headers = {"Authorization": f"Bearer {token}", "X-Sadana-Harness": ctx.runtime.harness_id}
    if if_match is not None:
        headers["If-Match"] = if_match
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    resp = handle(door_request.DoorRequest(method=method, path=path, query="", headers=headers, body=payload), ctx=ctx)
    return resp.status, (json.loads(resp.body) if resp.body else {})


def main() -> int:
    captured: list[dict[str, object]] = []

    def _fake_post(url: str, headers: dict, body: dict, timeout: float):
        captured.append({"url": url, "authorization": headers.get("Authorization")})
        return 200, {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    # `model_access._discover()` loads each provider's `provider.py` via
    # `importlib.util.spec_from_file_location` under a synthetic module
    # name (`sadana._model_providers.<name>`) — never as a real dotted
    # import (provider directory names carry hyphens, not valid Python
    # identifiers). A plain `from sadana.model_providers.openrouter import
    # provider` would load a *second*, separate module instance and patch
    # the wrong one. Going through the registered `request_fn`'s own
    # `__module__` is what guarantees this patches the exact module object
    # `send()` will actually call into.
    manifest = model_access.get_provider("openrouter")
    provider_module = sys.modules[manifest.request_fn.__module__]  # type: ignore[union-attr]
    provider_module._post = _fake_post

    runtime = client_surface.open_runtime(provider="openrouter", model="deepseek/deepseek-v4-flash-0731")

    print("1) First turn, before any door reconfiguration")
    asyncio.run(client_surface.take_turn(runtime, account="a", conversation="c1", text="hi", create_as="chat"))
    first = captured[-1]
    print(f"   url={first['url']!r} authorization={first['authorization']!r}")
    assert first["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert first["authorization"] == "Bearer sk-original-value"

    private_pem, jwk = auth.generate_dev_keypair("e2e")
    jwks_path = Path(_STATE) / "jwks.json"
    jwks_path.write_text(json.dumps(auth.jwks_document(jwk)))
    verifier = auth.Verifier(auth.JwksSource(path=jwks_path))
    door_ctx = DoorContext(
        conns=runtime.connections,
        runtime=auth.BoxIdentity(harness_id="hrn_e2e", org=None),
        verifier=verifier,
        capabilities=capabilities.declared(),
        nouns={"providers": providers_noun, "secrets": secrets_noun},
        clock=time.time,
    )
    token = auth.mint_token(
        private_pem,
        "e2e",
        sub="u1",
        org=None,
        ws="ws1",
        hrn="hrn_e2e",
        scope=["providers:read", "providers:write", "secrets:write"],
    )

    print("2) Through the door: PUT a new secret, then PATCH the provider")
    status, body = _request(
        door_ctx, token, "POST", "/v1/secrets", {"name": "rotated_key", "value": "sk-rotated-value"}
    )
    assert status == 201, (status, body)

    status, body = _request(door_ctx, token, "GET", "/v1/providers")
    assert status == 200, (status, body)
    row = next(p for p in body["data"] if p["name"] == "openrouter")

    status, body = _request(
        door_ctx,
        token,
        "PATCH",
        f"/v1/providers/{row['id']}",
        {"credential_ref": "rotated_key", "base_url": "https://example.test/v1"},
        if_match=str(row["version"]),
    )
    print(f"   {status} credential_ref={body.get('credential_ref')!r} base_url={body.get('base_url')!r}")
    assert status == 200, (status, body)

    print("3) Second turn, on the SAME already-open Runtime — no restart")
    asyncio.run(client_surface.take_turn(runtime, account="a", conversation="c1", text="hi again", create_as=None))
    second = captured[-1]
    print(f"   url={second['url']!r} authorization={second['authorization']!r}")
    assert second["url"] == "https://example.test/v1/chat/completions"
    assert second["authorization"] == "Bearer sk-rotated-value"

    print("\nALL STEPS PASSED — credential_ref/base_url reached an already-open runtime with no restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
