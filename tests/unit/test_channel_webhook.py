"""Tests for sadana.channel_webhook. No socket involved — parse_webhook_request()
is a pure function, tested directly."""

from __future__ import annotations

import json

import pytest

from sadana.channel_webhook import WebhookBadRequest, WebhookUnauthorized, parse_webhook_request
from sadana.gateway import MessageEvent

_SECRET = "s3cr3t"  # pragma: allowlist secret - a fixed test fixture value, not a real credential
_HEADERS = {"X-Sadana-Webhook-Secret": _SECRET}


@pytest.mark.unit
def test_parse_webhook_request_well_formed_returns_message_event() -> None:
    body = json.dumps({"chat_id": "c1", "text": "hello"}).encode("utf-8")
    result = parse_webhook_request(body, _HEADERS, secret=_SECRET)
    assert result == MessageEvent(platform="webhook", chat_id="c1", thread_id=None, text="hello")


@pytest.mark.unit
def test_parse_webhook_request_with_thread_id() -> None:
    body = json.dumps({"chat_id": "c1", "text": "hello", "thread_id": "t1"}).encode("utf-8")
    result = parse_webhook_request(body, _HEADERS, secret=_SECRET)
    assert result == MessageEvent(platform="webhook", chat_id="c1", thread_id="t1", text="hello")


@pytest.mark.unit
def test_parse_webhook_request_missing_secret_header_is_unauthorized() -> None:
    body = json.dumps({"chat_id": "c1", "text": "hello"}).encode("utf-8")
    result = parse_webhook_request(body, {}, secret=_SECRET)
    assert isinstance(result, WebhookUnauthorized)


@pytest.mark.unit
def test_parse_webhook_request_wrong_secret_is_unauthorized() -> None:
    body = json.dumps({"chat_id": "c1", "text": "hello"}).encode("utf-8")
    result = parse_webhook_request(body, {"X-Sadana-Webhook-Secret": "wrong"}, secret=_SECRET)
    assert isinstance(result, WebhookUnauthorized)


@pytest.mark.unit
def test_parse_webhook_request_secret_header_lookup_is_case_insensitive() -> None:
    # HTTP header names are case-insensitive (RFC 7230 §3.2); do_POST's real
    # caller hands this function dict(self.headers), which preserves
    # whatever casing arrived on the wire — a correct secret must not be
    # rejected just because a client sent a differently-cased header name.
    body = json.dumps({"chat_id": "c1", "text": "hello"}).encode("utf-8")
    result = parse_webhook_request(body, {"x-sadana-webhook-secret": _SECRET}, secret=_SECRET)
    assert result == MessageEvent(platform="webhook", chat_id="c1", thread_id=None, text="hello")


@pytest.mark.unit
def test_parse_webhook_request_malformed_json_is_bad_request() -> None:
    result = parse_webhook_request(b"not json", _HEADERS, secret=_SECRET)
    assert isinstance(result, WebhookBadRequest)


@pytest.mark.unit
def test_parse_webhook_request_missing_chat_id_is_bad_request() -> None:
    body = json.dumps({"text": "hello"}).encode("utf-8")
    result = parse_webhook_request(body, _HEADERS, secret=_SECRET)
    assert isinstance(result, WebhookBadRequest)


@pytest.mark.unit
def test_parse_webhook_request_missing_text_is_bad_request() -> None:
    body = json.dumps({"chat_id": "c1"}).encode("utf-8")
    result = parse_webhook_request(body, _HEADERS, secret=_SECRET)
    assert isinstance(result, WebhookBadRequest)
