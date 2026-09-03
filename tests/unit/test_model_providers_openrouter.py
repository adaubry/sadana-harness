"""Tests for the openrouter provider's request_fn, via sadana.model_access.send().

The network boundary (``urllib.request.urlopen``) is monkeypatched, never
the provider module's own ``_post`` — the provider file gets imported twice
under different module names (a normal import here vs. the dynamic import
``_discover()`` performs), so patching module-local state would silently
miss the instance actually registered. ``urllib.request`` is the one
canonical shared module regardless of which import path loaded the
provider file.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from sadana.model_access import (
    Abort,
    Degenerate,
    NeedsContextCompression,
    NeedsCredentialOrProviderChange,
    Request,
    Response,
    Retry,
    send,
)


class _FakeResponse:
    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _http_error(status: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://openrouter.ai/api/v1/chat/completions",
        status,
        "error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(json.dumps(body).encode()),
    )


def _request(**overrides: object) -> Request:
    defaults: dict[str, object] = dict(
        messages=({"role": "user", "content": "hi"},),
        provider="openrouter",
        model="deepseek/deepseek-v4-flash-0731",
    )
    defaults.update(overrides)
    return Request(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
def test_normal_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    body = {
        "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(200, body))
    outcome = send(_request())
    assert isinstance(outcome, Response)
    assert outcome.content == "hello"


@pytest.mark.unit
def test_missing_credential_never_calls_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls: list[object] = []

    def _tripwire(req: object, timeout: float | None = None) -> _FakeResponse:
        calls.append(req)
        return _FakeResponse(200, {})

    monkeypatch.setattr(urllib.request, "urlopen", _tripwire)
    outcome = send(_request())
    assert isinstance(outcome, NeedsCredentialOrProviderChange)
    assert calls == []


@pytest.mark.unit
def test_401_needs_credential_or_provider_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-bad")

    def _raise(req: object, timeout: float | None = None) -> None:
        raise _http_error(401, {"error": {"message": "invalid api key"}})

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = send(_request())
    assert isinstance(outcome, NeedsCredentialOrProviderChange)


@pytest.mark.unit
def test_transport_timeout_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def _raise(req: object, timeout: float | None = None) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = send(_request())
    assert outcome == Retry(next_attempt=1)


@pytest.mark.unit
def test_context_length_message_needs_context_compression(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def _raise(req: object, timeout: float | None = None) -> None:
        raise _http_error(400, {"error": {"message": "maximum context length is 4096 tokens"}})

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = send(_request())
    assert isinstance(outcome, NeedsContextCompression)


@pytest.mark.unit
def test_unknown_model_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def _raise(req: object, timeout: float | None = None) -> None:
        raise _http_error(400, {"error": {"message": "model not found"}})

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = send(_request(model="bogus/does-not-exist"))
    assert isinstance(outcome, Abort)


@pytest.mark.unit
def test_empty_completion_is_degenerate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    body = {
        "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 0},
    }
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(200, body))
    outcome = send(_request())
    assert isinstance(outcome, Degenerate)


@pytest.mark.unit
def test_request_body_and_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    captured: dict[str, object] = {}

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data)  # type: ignore[arg-type]
        body = {
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return _FakeResponse(200, body)

    monkeypatch.setattr(urllib.request, "urlopen", _capture)
    send(_request())
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["body"]["model"] == "deepseek/deepseek-v4-flash-0731"  # type: ignore[index]
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]  # type: ignore[index]
    assert captured["headers"]["Authorization"] == "Bearer sk-test"  # type: ignore[index]
