"""Tests for sadana.marketplace_webhook. No socket involved —
parse_webhook_request() is a pure function, tested directly."""

from __future__ import annotations

import json

import pytest

from sadana.marketplace_webhook import ReleaseSubmission, WebhookBadRequest, WebhookUnauthorized, parse_webhook_request

_SECRET = "s3cr3t"  # pragma: allowlist secret - a fixed test fixture value, not a real credential
_HEADERS = {"X-Sadana-Marketplace-Webhook-Secret": _SECRET}


@pytest.mark.unit
def test_parse_webhook_request_well_formed_returns_release_submission() -> None:
    body = json.dumps({"repo_url": "https://example.invalid/greeter.git", "tag": "v1.0.0"}).encode("utf-8")
    result = parse_webhook_request(body, _HEADERS, secret=_SECRET)
    assert result == ReleaseSubmission(repo_url="https://example.invalid/greeter.git", tag="v1.0.0")


@pytest.mark.unit
def test_parse_webhook_request_missing_secret_header_is_unauthorized() -> None:
    body = json.dumps({"repo_url": "https://example.invalid/greeter.git", "tag": "v1.0.0"}).encode("utf-8")
    result = parse_webhook_request(body, {}, secret=_SECRET)
    assert isinstance(result, WebhookUnauthorized)


@pytest.mark.unit
def test_parse_webhook_request_wrong_secret_is_unauthorized() -> None:
    body = json.dumps({"repo_url": "https://example.invalid/greeter.git", "tag": "v1.0.0"}).encode("utf-8")
    result = parse_webhook_request(body, {"X-Sadana-Marketplace-Webhook-Secret": "wrong"}, secret=_SECRET)
    assert isinstance(result, WebhookUnauthorized)


@pytest.mark.unit
def test_parse_webhook_request_secret_header_lookup_is_case_insensitive() -> None:
    body = json.dumps({"repo_url": "https://example.invalid/greeter.git", "tag": "v1.0.0"}).encode("utf-8")
    result = parse_webhook_request(body, {"x-sadana-marketplace-webhook-secret": _SECRET}, secret=_SECRET)
    assert result == ReleaseSubmission(repo_url="https://example.invalid/greeter.git", tag="v1.0.0")


@pytest.mark.unit
def test_parse_webhook_request_malformed_json_is_bad_request() -> None:
    result = parse_webhook_request(b"not json", _HEADERS, secret=_SECRET)
    assert isinstance(result, WebhookBadRequest)


@pytest.mark.unit
def test_parse_webhook_request_missing_repo_url_is_bad_request() -> None:
    body = json.dumps({"tag": "v1.0.0"}).encode("utf-8")
    result = parse_webhook_request(body, _HEADERS, secret=_SECRET)
    assert isinstance(result, WebhookBadRequest)


@pytest.mark.unit
def test_parse_webhook_request_missing_tag_is_bad_request() -> None:
    body = json.dumps({"repo_url": "https://example.invalid/greeter.git"}).encode("utf-8")
    result = parse_webhook_request(body, _HEADERS, secret=_SECRET)
    assert isinstance(result, WebhookBadRequest)
