"""Tests for sadana.execution: run_http()'s never-raises contract.

The network boundary (``urllib.request.urlopen``) is monkeypatched, same
point ``tests/unit/test_model_providers_openrouter.py`` patches for the
identical reason — it's the one canonical shared module regardless of
import path.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from sadana.execution import Failure, HttpRequest, Success, run_http


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.mark.unit
def test_2xx_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(200, b"ok"))
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert outcome == Success(status=200, body=b"ok")


@pytest.mark.unit
def test_http_error_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise urllib.error.HTTPError(
            "https://example.com",
            404,
            "not found",
            hdrs=None,
            fp=io.BytesIO(b""),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert isinstance(outcome, Failure)
    assert "404" in outcome.detail


@pytest.mark.unit
def test_http_error_detail_reads_the_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise urllib.error.HTTPError(
            "https://example.com",
            429,
            "too many requests",
            hdrs=None,
            fp=io.BytesIO(b"quota exceeded, retry after 30s"),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert isinstance(outcome, Failure)
    assert "quota exceeded, retry after 30s" in outcome.detail


@pytest.mark.unit
def test_unsupported_scheme_is_failure_without_ever_calling_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def _tripwire(req: object, timeout: float | None = None) -> None:
        calls.append(req)

    monkeypatch.setattr(urllib.request, "urlopen", _tripwire)
    outcome = run_http(HttpRequest(method="GET", url="file:///etc/passwd"))
    assert isinstance(outcome, Failure)
    assert calls == []


@pytest.mark.unit
def test_malformed_url_from_urlopen_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise ValueError("Invalid header value b'bar\\r\\nX-Injected: evil'")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert isinstance(outcome, Failure)


@pytest.mark.unit
def test_connection_failure_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise OSError("Name or service not known")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://does-not-resolve.invalid"))
    assert isinstance(outcome, Failure)
    assert "Name or service not known" in outcome.detail
