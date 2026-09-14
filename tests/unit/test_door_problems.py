"""`door/problems.py` — the door's one Problem Details shape."""

from __future__ import annotations

import pytest

from sadana.door import problems


@pytest.mark.unit
def test_codes_is_exactly_the_closed_thirteen() -> None:
    assert set(problems.CODES) == {
        "UNAUTHENTICATED",
        "NOT_FOUND",
        "PAYMENT_REQUIRED",
        "FORBIDDEN",
        "VALIDATION",
        "CONFLICT",
        "PRECONDITION_FAILED",
        "RATE_LIMITED",
        "QUOTA_EXCEEDED",
        "HARNESS_OFFLINE",
        "HARNESS_CAPABILITY_MISSING",
        "IDEMPOTENCY_MISMATCH",
        "INTERNAL",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "code,status",
    [
        ("UNAUTHENTICATED", 401),
        ("NOT_FOUND", 404),
        ("PAYMENT_REQUIRED", 402),
        ("FORBIDDEN", 403),
        ("VALIDATION", 400),
        ("CONFLICT", 409),
        ("PRECONDITION_FAILED", 412),
        ("RATE_LIMITED", 429),
        ("QUOTA_EXCEEDED", 429),
        ("HARNESS_OFFLINE", 503),
        ("HARNESS_CAPABILITY_MISSING", 501),
        ("IDEMPOTENCY_MISMATCH", 422),
        ("INTERNAL", 500),
    ],
)
def test_make_sets_the_contract_status_for_each_code(code: str, status: int) -> None:
    assert problems.make(code).status == status


@pytest.mark.unit
def test_make_rejects_a_code_outside_the_closed_set() -> None:
    """A caller error, not data to tolerate — the same discipline
    `ids.make_id` already applies to an unregistered prefix."""
    with pytest.raises(ValueError):
        problems.make("TEAPOT")


@pytest.mark.unit
def test_to_json_carries_the_rfc_9457_shape() -> None:
    p = problems.make("VALIDATION", "page_size out of range")

    body = p.to_json()

    assert body["type"] == "https://sadana.dev/errors/VALIDATION"
    assert body["status"] == 400
    assert body["code"] == "VALIDATION"
    assert body["detail"] == "page_size out of range"


@pytest.mark.unit
def test_to_json_omits_optional_fields_when_absent() -> None:
    body = problems.make("NOT_FOUND").to_json()

    assert "detail" not in body
    assert "instance" not in body
    assert "errors" not in body


@pytest.mark.unit
def test_to_json_carries_field_errors_when_given() -> None:
    p = problems.make("VALIDATION", errors=({"field": "order_by", "code": "unknown_field"},))

    assert p.to_json()["errors"] == [{"field": "order_by", "code": "unknown_field"}]
