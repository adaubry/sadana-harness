"""Tests for sadana.execution: run_http()'s never-raises contract.

The network boundary (``execution._open``) is monkeypatched, same
point ``tests/unit/test_model_providers_openrouter.py`` patches for the
identical reason — it's the one canonical shared module regardless of
import path.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from sadana import execution
from sadana.execution import Failure, HttpRequest, Success, run_http


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, amt: int | None = None) -> bytes:
        """Takes `amt` because the real one does, and `run_http` now passes a
        ceiling rather than reading whatever the other end decides to send."""
        return self._body if amt is None else self._body[:amt]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.mark.unit
def test_2xx_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execution, "_open", lambda req, timeout_s=None: _FakeResponse(200, b"ok"))
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

    monkeypatch.setattr(execution, "_open", _raise)
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

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert isinstance(outcome, Failure)
    assert "quota exceeded, retry after 30s" in outcome.detail


@pytest.mark.unit
def test_unsupported_scheme_is_failure_without_ever_calling_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def _tripwire(req: object, timeout: float | None = None) -> None:
        calls.append(req)

    monkeypatch.setattr(execution, "_open", _tripwire)
    outcome = run_http(HttpRequest(method="GET", url="file:///etc/passwd"))
    assert isinstance(outcome, Failure)
    assert calls == []


@pytest.mark.unit
def test_malformed_url_from_urlopen_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise ValueError("Invalid header value b'bar\\r\\nX-Injected: evil'")

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    assert isinstance(outcome, Failure)


@pytest.mark.unit
def test_connection_failure_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(req: object, timeout: float | None = None) -> None:
        raise OSError("Name or service not known")

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://does-not-resolve.invalid"))
    assert isinstance(outcome, Failure)
    assert "Name or service not known" in outcome.detail


@pytest.mark.unit
def test_a_redirect_is_refused_rather_than_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credential sent as a header would otherwise be re-sent to whatever
    the 3xx points at: urllib's default redirect handler keeps every header
    but content-length and content-type, cross-host included.

    Asserted against the handler this module installs rather than over a
    socket, so it stays inside testing-conventions' network ban."""
    assert execution._NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.invalid") is None


@pytest.mark.unit
@pytest.mark.parametrize(("asked", "used"), [(None, 7), (120, 120)])
def test_a_request_may_ask_for_longer_than_the_configured_default(
    monkeypatch: pytest.MonkeyPatch, asked: int | None, used: int
) -> None:
    """Drawing a picture takes tens of seconds; raising the default instead
    would make a search that should fail fast wait just as long."""
    seen: list[int] = []

    def _capture(_req: object, timeout_s: int) -> object:
        seen.append(timeout_s)
        return _FakeResponse(200, b"ok")

    monkeypatch.setenv("SADANA_EXECUTION_HTTP_TIMEOUT_S", "7")
    monkeypatch.setattr(execution, "_open", _capture)
    run_http(HttpRequest(method="GET", url="https://example.com", timeout_s=asked))
    assert seen == [used]


@pytest.mark.unit
def test_an_http_error_body_is_defanged_before_it_becomes_a_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """`detail` is built from up to 200 bytes of a stranger's response body and
    is read by a model. Defanged here rather than in each plugin body that
    reports it — every caller having to remember is how the third one silently
    forgets."""

    def _raise(_req: object, timeout_s: int = 0) -> object:
        raise urllib.error.HTTPError(
            "https://example.com",
            500,
            "Server Error",
            {},  # type: ignore[arg-type]
            io.BytesIO("oh \x1b[31mno\u200b\u202e".encode()),
        )

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))

    assert isinstance(outcome, Failure)
    assert "\x1b" not in outcome.detail
    assert "\u200b" not in outcome.detail
    assert "\u202e" not in outcome.detail
    assert "oh" in outcome.detail


@pytest.mark.unit
def test_a_reply_larger_than_the_ceiling_is_refused_rather_than_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing else bounds a reply: without a ceiling its size is whatever the
    other end decides to send, and the first caller that both expects
    megabytes and commits them to disk also asks for a 180-second window to
    receive them."""
    oversized = b"x" * (execution.MAX_BODY_BYTES + 1)
    monkeypatch.setattr(execution, "_open", lambda _req, timeout_s=0: _FakeResponse(200, oversized))

    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))

    assert isinstance(outcome, Failure)
    assert "larger than" in outcome.detail


@pytest.mark.unit
def test_a_reply_exactly_at_the_ceiling_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    at_limit = b"x" * execution.MAX_BODY_BYTES
    monkeypatch.setattr(execution, "_open", lambda _req, timeout_s=0: _FakeResponse(200, at_limit))

    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))

    assert isinstance(outcome, Success)
    assert len(outcome.body) == execution.MAX_BODY_BYTES


@pytest.mark.unit
def test_the_status_reason_is_defanged_too_not_only_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason phrase is as much the other end's as the body is —
    `http.client` reads it off the status line. Defanging only the body was
    this filter's first placement, and it was a narrowing."""

    def _raise(_req: object, timeout_s: int = 0) -> object:
        raise urllib.error.HTTPError(
            "https://example.com",
            500,
            "oops \x1b[31mEVIL",
            {},  # type: ignore[arg-type]
            io.BytesIO(b""),
        )

    monkeypatch.setattr(execution, "_open", _raise)
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))

    assert isinstance(outcome, Failure)
    assert "\x1b" not in outcome.detail
    assert "oops" in outcome.detail
