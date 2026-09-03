"""OpenRouter — the one wired provider.

Rides the OpenAI-compatible chat-completions wire shape unmodified (that is
what OpenRouter's own API is), so ``request_fn`` needs no message-format
conversion: ``Request.messages``/``.tools`` go straight into the HTTP body.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from sadana import config
from sadana.model_access import ProviderManifest, Request, register_provider

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_COMPLETIONS_URL = f"{BASE_URL}/chat/completions"


def _post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> tuple[int | None, dict]:
    """One HTTP POST. Isolated so tests can monkeypatch this and never touch
    the network. Never raises: a transport-level failure (timeout,
    connection error) returns ``(None, {...})`` for ``classify()`` to treat
    as transient, exactly like a 5xx.
    """
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read() or b"{}")
        except ValueError:
            parsed = {"error": {"message": str(exc)}}
        return exc.code, parsed
    except OSError as exc:
        return None, {"error": {"message": str(exc)}}


def request_fn(request: Request) -> tuple[int | None, dict]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    body: dict[str, Any] = {"model": request.model, "messages": list(request.messages)}
    if request.tools:
        body["tools"] = list(request.tools)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout_s = config.env_int("SADANA_MODEL_ACCESS_TIMEOUT_S", 30)
    return _post(CHAT_COMPLETIONS_URL, headers, body, timeout_s)


register_provider(
    ProviderManifest(
        name="openrouter",
        env_vars=("OPENROUTER_API_KEY",),
        base_url=BASE_URL,
        request_fn=request_fn,
    )
)
