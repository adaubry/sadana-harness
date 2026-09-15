"""`tether/frames.py` — the wire protocol's dataclasses, encode/decode, and
its one seam onto `door.request`."""

from __future__ import annotations

import base64
import json

import pytest

from sadana.door.request import DoorResponse
from sadana.tether import frames


@pytest.mark.unit
@pytest.mark.parametrize(
    "frame",
    [
        frames.Challenge(nonce="n1"),
        frames.ChallengeResponse(harness_id="hrn_x", signature="c2ln"),
        frames.Welcome(),
        frames.Hello(harness_id="hrn_x", version="0.0.1", capabilities=("grammar.v1", "changes"), ledger_head=7),
        frames.Heartbeat(at=123.5),
        frames.Request(
            id="r1", method="GET", path="/v1/harness", query="", headers={"Authorization": "Bearer t"}, body=None
        ),
        frames.Response(id="r1", status=200, headers={"Content-Type": "application/json"}, body={"ok": True}),
        frames.Event(cursor=3, event={"kind": "changed", "noun": "harness", "id": "hrn_x"}),
        frames.Ephemeral(name="message.delta", harness_id="hrn_x", data={"seq": 1}),
        frames.Error(code="INTERNAL", detail="boom"),
    ],
)
def test_encode_decode_round_trips_every_frame(frame: frames.Frame) -> None:
    decoded = frames.decode(frames.encode(frame))
    assert decoded == frame


@pytest.mark.unit
def test_decode_unrecognised_type_is_unknown_frame() -> None:
    result = frames.decode({"type": "not-a-real-frame", "x": 1})
    assert isinstance(result, frames.UnknownFrame)
    assert result.raw == {"type": "not-a-real-frame", "x": 1}


@pytest.mark.unit
def test_decode_missing_type_is_unknown_frame() -> None:
    result = frames.decode({"nonce": "n1"})
    assert isinstance(result, frames.UnknownFrame)


@pytest.mark.unit
def test_decode_recognised_type_missing_a_required_field_is_unknown_frame() -> None:
    result = frames.decode({"type": "challenge"})  # no "nonce"
    assert isinstance(result, frames.UnknownFrame)
    assert "nonce" in result.reason


@pytest.mark.unit
def test_decode_never_raises_on_wrong_typed_field() -> None:
    result = frames.decode({"type": "heartbeat", "at": "not-a-number"})
    assert isinstance(result, frames.UnknownFrame)


@pytest.mark.unit
def test_request_to_door_request_no_body_is_empty_bytes() -> None:
    door_request = frames.request_to_door_request(
        frames.Request(id="r1", method="GET", path="/v1/harness", query="", headers={}, body=None)
    )
    assert door_request.body == b""
    assert door_request.method == "GET"
    assert door_request.path == "/v1/harness"


@pytest.mark.unit
def test_request_to_door_request_json_body_round_trips() -> None:
    door_request = frames.request_to_door_request(
        frames.Request(id="r1", method="POST", path="/v1/conversations", query="", headers={}, body={"name": "n"})
    )
    assert json.loads(door_request.body) == {"name": "n"}


@pytest.mark.unit
def test_door_response_to_response_json_body_is_carried_as_a_value_not_a_string() -> None:
    door_response = DoorResponse(
        status=200, headers={"Content-Type": "application/json"}, body=json.dumps({"id": "hrn_x"}).encode("utf-8")
    )
    response = frames.door_response_to_response("r1", door_response)
    assert response.body == {"id": "hrn_x"}
    assert response.encoding is None


@pytest.mark.unit
def test_door_response_to_response_binary_body_is_base64_encoded() -> None:
    raw = b"\x00\x01binary-ish\xff"
    door_response = DoorResponse(status=200, headers={"Content-Type": "application/octet-stream"}, body=raw)
    response = frames.door_response_to_response("r1", door_response)
    assert response.encoding == "base64"
    assert base64.b64decode(response.body) == raw  # type: ignore[arg-type]


@pytest.mark.unit
def test_door_response_to_response_oversized_binary_body_is_a_clean_500_not_a_crash(monkeypatch) -> None:
    monkeypatch.setattr(frames, "MAX_RESPONSE_BYTES", 4)
    door_response = DoorResponse(status=200, headers={"Content-Type": "application/octet-stream"}, body=b"way too big")
    response = frames.door_response_to_response("r1", door_response)
    assert response.status == 500
    assert response.encoding is None
    assert isinstance(response.body, dict)
    assert response.body["code"] == "INTERNAL"
    assert response.id == "r1"
