"""OpenRouter — the one wired provider.

Rides the OpenAI-compatible chat-completions wire shape unmodified (that is
what OpenRouter's own API is), so ``request_fn`` needs no message-format
conversion: ``Request.messages``/``.tools`` go straight into the HTTP body.

H21 adds ``stream_fn``: the same request with ``stream: true``, read as a
Server-Sent-Events sequence and reassembled into the identical
non-streaming shape ``request_fn`` returns, so ``classify()`` never needs a
second code path. Reference corpus: hermes's own
``agent/chat_completion_helpers.py`` does the identical reassembly
(``_relay_final_response()``, lines 4147-4159) and the identical tool-call
fragment asymmetry — a delta's ``function.name`` is *assigned* (some
providers resend the full name every chunk), ``function.arguments`` is
*concatenated* (every provider observed splits it genuinely) — see
``docs/tasks/H21-watched-streaming-live-runs-stop/spec.md``'s own "What the
reference corpus showed". Declined from that same reference: its full
``event:``/``data:``/comment SSE state machine, built for many
non-conforming providers' error bodies — this route's own stream is a
plain, conforming ``data: {...}`` / ``data: [DONE]`` sequence, so a bare
``data:`` prefix check is enough.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterable
from typing import Any

from sadana import config
from sadana.model_access import OnDelta, ProviderManifest, Request, register_provider

#: The built-in default — `ProviderManifest`'s own static identity, and the
#: fallback `chat_completions_url()` uses when nothing has overridden it.
BASE_URL = "https://openrouter.ai/api/v1"


def chat_completions_url() -> str:
    """Read at the moment of use (H14, `docs/tasks/
    H14-tuned-config-settings-secrets/spec.md`): `providers.openrouter.
    base_url` in `config.toml`, or `BASE_URL` if nothing overrides it — a
    change through the door takes effect on the next call, no restart."""
    return f"{config.get('providers.openrouter.base_url', BASE_URL)}/chat/completions"


def _build_request(request: Request, *, stream: bool) -> tuple[dict[str, Any], dict[str, str], float]:
    """The body/headers/timeout `request_fn` and `stream_fn` both need —
    identical but for `stream: true`, so it is built once."""
    credential_ref = config.get("providers.openrouter.credential_ref", "OPENROUTER_API_KEY")
    api_key = config.secret(credential_ref) or ""
    body: dict[str, Any] = {"model": request.model, "messages": list(request.messages)}
    if stream:
        body["stream"] = True
    if request.tools:
        body["tools"] = list(request.tools)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout_s = config.get("model_access.timeout_s", 30)
    return body, headers, timeout_s


def _error_body(exc: urllib.error.HTTPError) -> dict:
    """An HTTP error's own JSON body, or a synthesized one when it isn't
    valid JSON — the same fallback `_post` and `stream_fn` both need for a
    4xx/5xx, whether or not `stream: true` framing was ever reached."""
    try:
        return json.loads(exc.read() or b"{}")
    except ValueError:
        return {"error": {"message": str(exc)}}


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
        return exc.code, _error_body(exc)
    except OSError as exc:
        return None, {"error": {"message": str(exc)}}


def request_fn(request: Request) -> tuple[int | None, dict]:
    body, headers, timeout_s = _build_request(request, stream=False)
    return _post(chat_completions_url(), headers, body, timeout_s)


def _accumulate_tool_call(tool_calls: dict[int, dict], tc_delta: dict) -> None:
    """Folds one tool-call delta fragment into ``tool_calls``, keyed by the
    delta's own ``index`` (defaulting to 0 — OpenAI's own wire contract for
    a single tool call with no index at all). ``function.name`` is
    *assigned*, never concatenated; ``function.arguments`` *is*
    concatenated. See this file's own module docstring for why."""
    index = tc_delta.get("index", 0)
    slot = tool_calls.setdefault(index, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
    if tc_delta.get("id"):
        slot["id"] = tc_delta["id"]
    function_delta = tc_delta.get("function") or {}
    if "name" in function_delta:
        slot["function"]["name"] = function_delta["name"]
    if "arguments" in function_delta:
        slot["function"]["arguments"] += function_delta["arguments"]


def parse_stream(lines: Iterable[bytes], on_delta: OnDelta) -> dict:
    """Reads ``lines`` (whatever ``resp`` or a recorded fixture iterates as)
    as OpenRouter's own SSE stream, calling ``on_delta`` with each text
    fragment as it arrives, and returns the non-streaming-shaped body once
    ``data: [DONE]`` is reached — a plain function so
    ``tests/fixtures/openrouter_stream.txt`` can drive it directly with no
    real HTTP response object involved."""
    content_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    finish_reason = "stop"
    usage: dict = {}
    for raw_line in lines:
        line = raw_line.decode("utf-8").strip() if isinstance(raw_line, bytes) else raw_line.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except ValueError:
            continue
        choice = (event.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("content"):
            content_parts.append(delta["content"])
            on_delta(delta["content"])
        for tc_delta in delta.get("tool_calls") or ():
            _accumulate_tool_call(tool_calls, tc_delta)
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]
        if event.get("usage"):
            usage = event["usage"]

    message: dict[str, Any] = {"content": "".join(content_parts) if content_parts else None}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return {"choices": [{"message": message, "finish_reason": finish_reason}], "usage": usage}


def stream_fn(request: Request, on_delta: OnDelta) -> tuple[int | None, dict]:
    body, headers, timeout_s = _build_request(request, stream=True)
    req = urllib.request.Request(chat_completions_url(), data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200, parse_stream(resp, on_delta)
    except urllib.error.HTTPError as exc:
        # A 4xx/5xx arriving before the first SSE line — never touched
        # `stream: true` framing at all, so it is read exactly as `_post`
        # already reads a non-streaming error.
        return exc.code, _error_body(exc)
    except OSError as exc:
        # A transport failure mid-stream is indistinguishable here from one
        # before the first byte — both return `(None, {...})`, which
        # `classify()` already treats as transient. See spec.md's own
        # "Declined, with reasons" for why this project does not adopt
        # hermes's richer partial-continuation behavior here.
        return None, {"error": {"message": str(exc)}}


register_provider(
    ProviderManifest(
        name="openrouter",
        env_vars=("OPENROUTER_API_KEY",),
        base_url=BASE_URL,
        request_fn=request_fn,
        stream_fn=stream_fn,
    )
)
