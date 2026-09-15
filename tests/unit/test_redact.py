"""Tests for sadana.redact — the logging filter that keeps a secret's value
out of every log line."""

from __future__ import annotations

import base64
import json
import logging

import pytest

from sadana.redact import SecretRedactor


def _record(msg: str, *args: object, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _jwt(payload: dict[str, object] | None = None) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload or {"sub": "u1"}).encode()).decode().rstrip("=")
    return f"{header}.{body}.signature-not-checked"


@pytest.mark.unit
def test_redacts_an_openrouter_shaped_key_in_the_message() -> None:
    record = _record("using key %s", "sk-abcdefghijklmnopqrstuvwxyz")  # pragma: allowlist secret
    assert SecretRedactor().filter(record) is True
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in record.getMessage()  # pragma: allowlist secret
    assert "[REDACTED]" in record.getMessage()


@pytest.mark.unit
def test_redacts_a_jwt_shaped_string_in_the_message() -> None:
    token = _jwt()
    record = _record(f"authorization: Bearer {token}")
    SecretRedactor().filter(record)
    assert token not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()


@pytest.mark.unit
def test_does_not_redact_an_ordinary_dotted_string() -> None:
    record = _record("connecting to host.example.com version 1.2.3")
    SecretRedactor().filter(record)
    assert record.getMessage() == "connecting to host.example.com version 1.2.3"


@pytest.mark.unit
def test_redacts_a_name_matched_extra_field() -> None:
    record = _record("rotated %s", "a-credential", api_key="the-actual-secret", plugin="browse")
    SecretRedactor().filter(record)
    assert record.api_key == "[REDACTED]"  # type: ignore[attr-defined]
    assert record.plugin == "browse"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_a_field_merely_containing_a_sensitive_substring_is_not_redacted() -> None:
    """Name-based matching is whole-word: `keyboard`/`turkey`/`monkey` each
    contain "key" as a bare substring but are one word, not a match — the
    substring form this replaced wiped all three, destroying non-secret
    diagnostic data."""
    record = _record("using %s", "x", keyboard_layout="qwerty", turkey="gobble")
    SecretRedactor().filter(record)
    assert record.keyboard_layout == "qwerty"  # type: ignore[attr-defined]
    assert record.turkey == "gobble"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_a_neutral_field_name_is_not_redacted_by_shape_alone() -> None:
    """Only a field whose own name matches the sensitive pattern is
    redacted — a differently-named field is left alone even when it holds
    something that looks credential-shaped, since this filter's name-based
    check never inspects a value's shape."""
    record = _record("using %s", "cred_01977abc", ref="cred_01977abc")
    SecretRedactor().filter(record)
    assert record.ref == "cred_01977abc"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_leaves_an_unrelated_log_line_untouched() -> None:
    record = _record("turn %s completed in %s ms", "t1", 42, plugin="browse")
    SecretRedactor().filter(record)
    assert record.getMessage() == "turn t1 completed in 42 ms"
    assert record.plugin == "browse"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_never_drops_a_record() -> None:
    assert SecretRedactor().filter(_record("anything")) is True
