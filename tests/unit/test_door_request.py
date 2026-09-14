"""`door/request.py` — the pure request/response envelope."""

from __future__ import annotations

import json

import pytest

from sadana.door import problems, request


@pytest.mark.unit
def test_json_response_encodes_the_payload_as_json() -> None:
    resp = request.json_response(200, {"id": "hrn_abc"})

    assert resp.status == 200
    assert resp.headers["Content-Type"] == "application/json"
    assert json.loads(resp.body) == {"id": "hrn_abc"}


@pytest.mark.unit
def test_json_response_sets_a_quoted_etag_when_given() -> None:
    resp = request.json_response(200, {}, etag="3")

    assert resp.headers["ETag"] == '"3"'


@pytest.mark.unit
def test_json_response_omits_etag_by_default() -> None:
    resp = request.json_response(200, {})

    assert "ETag" not in resp.headers


@pytest.mark.unit
def test_problem_response_uses_the_problem_json_content_type() -> None:
    resp = request.problem_response(problems.make("NOT_FOUND"))

    assert resp.status == 404
    assert resp.headers["Content-Type"] == "application/problem+json"
    assert json.loads(resp.body)["code"] == "NOT_FOUND"


@pytest.mark.unit
def test_problem_response_carries_retry_after_on_a_429() -> None:
    resp = request.problem_response(problems.make("RATE_LIMITED"))

    assert resp.status == 429
    assert "Retry-After" in resp.headers


@pytest.mark.unit
def test_problem_response_carries_no_retry_after_off_a_429() -> None:
    resp = request.problem_response(problems.make("NOT_FOUND"))

    assert "Retry-After" not in resp.headers


@pytest.mark.unit
def test_door_request_is_a_frozen_data_envelope() -> None:
    req = request.DoorRequest(method="GET", path="/v1/harness", query="", headers={}, body=b"")

    with pytest.raises(AttributeError):
        req.method = "POST"  # type: ignore[misc]
