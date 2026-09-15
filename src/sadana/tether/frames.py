"""Frame dataclasses and encode/decode for the tether wire protocol.

`docs/console/wire.md` §5, implemented verbatim. Pure — no socket, no
clock (`Heartbeat.at` is supplied by the caller). `decode()` never raises:
an unrecognised `"type"`, or a recognised one missing a required field,
becomes `UnknownFrame`, and every caller turns that into an `Error` frame
back over the wire rather than crashing the connection loop or closing it.

`request_to_door_request`/`door_response_to_response` are this frame
protocol's one seam onto `door.request`'s own `DoorRequest`/`DoorResponse` —
the pair `tether/client.py` uses to hand an inbound `request` frame to
`door.router.handle()` and turn its `DoorResponse` back into a `response`
frame. A decoded body over `MAX_RESPONSE_BYTES` answers as a `response`
frame carrying a `500 INTERNAL` Problem Details body — still correlated by
id, never a bare uncorrelated `error` frame — rather than trying to inline
an unbounded blob into one WebSocket frame (spec.md § Design, § Concerns).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass

from sadana import config
from sadana.door import problems
from sadana.door.request import DoorRequest, DoorResponse

#: A decoded response body over this many bytes is refused as a clean,
#: correlated `500 INTERNAL` response rather than inlined whole into one
#: WebSocket frame.
MAX_RESPONSE_BYTES = config.env_int("SADANA_TETHER_MAX_RESPONSE_BYTES", 8 * 1024 * 1024)


class _FieldError(Exception):
    """Internal to `decode()` — never escapes it."""


def _str(raw: Mapping[str, object], key: str, *, default: str | None = None) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise _FieldError(f"{key!r} must be a string, got {value!r}")
    return value


def _int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _FieldError(f"{key!r} must be an int, got {value!r}")
    return value


def _num(raw: Mapping[str, object], key: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _FieldError(f"{key!r} must be a number, got {value!r}")
    return float(value)


def _str_list(raw: Mapping[str, object], key: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _FieldError(f"{key!r} must be a list of strings, got {value!r}")
    return value


def _mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise _FieldError(f"{key!r} must be an object, got {value!r}")
    return value


def _str_mapping(raw: Mapping[str, object], key: str, *, default: Mapping[str, object] | None = None) -> dict[str, str]:
    value = raw.get(key, default if default is not None else {})
    if not isinstance(value, Mapping) or not all(isinstance(v, str) for v in value.values()):
        raise _FieldError(f"{key!r} must be an object of strings, got {value!r}")
    return {str(k): str(v) for k, v in value.items()}


@dataclass(frozen=True)
class Challenge:
    nonce: str


@dataclass(frozen=True)
class ChallengeResponse:
    harness_id: str
    signature: str  # base64 of the raw r‖s ES256 signature over the nonce bytes


@dataclass(frozen=True)
class Welcome:
    pass


@dataclass(frozen=True)
class Hello:
    harness_id: str
    version: str
    capabilities: tuple[str, ...]
    ledger_head: int


@dataclass(frozen=True)
class Heartbeat:
    at: float


@dataclass(frozen=True)
class Request:
    id: str
    method: str
    path: str
    query: str
    headers: Mapping[str, str]
    body: object  # a JSON value, or None for no body


@dataclass(frozen=True)
class Response:
    id: str
    status: int
    headers: Mapping[str, str]
    body: object  # a JSON value, None, or a base64 string when encoding == "base64"
    encoding: str | None = None


@dataclass(frozen=True)
class Event:
    cursor: int
    event: Mapping[str, object]


@dataclass(frozen=True)
class Ephemeral:
    name: str
    harness_id: str
    data: Mapping[str, object]


@dataclass(frozen=True)
class Error:
    code: str
    detail: str


@dataclass(frozen=True)
class UnknownFrame:
    """What `decode()` returns for a `"type"` this box does not recognise, or
    a recognised type missing/mistyping a required field."""

    raw: Mapping[str, object]
    reason: str


Frame = Challenge | ChallengeResponse | Welcome | Hello | Heartbeat | Request | Response | Event | Ephemeral | Error

_KNOWN_TYPES = frozenset(
    {
        "challenge",
        "challenge_response",
        "welcome",
        "hello",
        "heartbeat",
        "request",
        "response",
        "event",
        "ephemeral",
        "error",
    }
)


def encode(frame: Frame) -> dict[str, object]:
    if isinstance(frame, Challenge):
        return {"type": "challenge", "nonce": frame.nonce}
    if isinstance(frame, ChallengeResponse):
        return {"type": "challenge_response", "harness_id": frame.harness_id, "signature": frame.signature}
    if isinstance(frame, Welcome):
        return {"type": "welcome"}
    if isinstance(frame, Hello):
        return {
            "type": "hello",
            "harness_id": frame.harness_id,
            "version": frame.version,
            "capabilities": list(frame.capabilities),
            "ledger_head": frame.ledger_head,
        }
    if isinstance(frame, Heartbeat):
        return {"type": "heartbeat", "at": frame.at}
    if isinstance(frame, Request):
        return {
            "type": "request",
            "id": frame.id,
            "method": frame.method,
            "path": frame.path,
            "query": frame.query,
            "headers": dict(frame.headers),
            "body": frame.body,
        }
    if isinstance(frame, Response):
        payload: dict[str, object] = {
            "type": "response",
            "id": frame.id,
            "status": frame.status,
            "headers": dict(frame.headers),
            "body": frame.body,
        }
        if frame.encoding is not None:
            payload["encoding"] = frame.encoding
        return payload
    if isinstance(frame, Event):
        return {"type": "event", "cursor": frame.cursor, "event": dict(frame.event)}
    if isinstance(frame, Ephemeral):
        return {"type": "ephemeral", "name": frame.name, "harness_id": frame.harness_id, "data": dict(frame.data)}
    if isinstance(frame, Error):
        return {"type": "error", "code": frame.code, "detail": frame.detail}
    raise TypeError(f"not a known frame: {frame!r}")


def decode(raw: Mapping[str, object]) -> Frame | UnknownFrame:
    frame_type = raw.get("type")
    if not isinstance(frame_type, str) or frame_type not in _KNOWN_TYPES:
        return UnknownFrame(raw=raw, reason=f"unrecognised type {frame_type!r}")
    try:
        if frame_type == "challenge":
            return Challenge(nonce=_str(raw, "nonce"))
        if frame_type == "challenge_response":
            return ChallengeResponse(harness_id=_str(raw, "harness_id"), signature=_str(raw, "signature"))
        if frame_type == "welcome":
            return Welcome()
        if frame_type == "hello":
            return Hello(
                harness_id=_str(raw, "harness_id"),
                version=_str(raw, "version"),
                capabilities=tuple(_str_list(raw, "capabilities")),
                ledger_head=_int(raw, "ledger_head"),
            )
        if frame_type == "heartbeat":
            return Heartbeat(at=_num(raw, "at"))
        if frame_type == "request":
            return Request(
                id=_str(raw, "id"),
                method=_str(raw, "method"),
                path=_str(raw, "path"),
                query=_str(raw, "query", default=""),
                headers=_str_mapping(raw, "headers"),
                body=raw.get("body"),
            )
        if frame_type == "response":
            encoding = raw.get("encoding")
            return Response(
                id=_str(raw, "id"),
                status=_int(raw, "status"),
                headers=_str_mapping(raw, "headers"),
                body=raw.get("body"),
                encoding=encoding if isinstance(encoding, str) else None,
            )
        if frame_type == "event":
            return Event(cursor=_int(raw, "cursor"), event=dict(_mapping(raw, "event")))
        if frame_type == "ephemeral":
            return Ephemeral(
                name=_str(raw, "name"), harness_id=_str(raw, "harness_id"), data=dict(_mapping(raw, "data"))
            )
        assert frame_type == "error"
        return Error(code=_str(raw, "code"), detail=_str(raw, "detail"))
    except _FieldError as exc:
        return UnknownFrame(raw=raw, reason=str(exc))


def request_to_door_request(request: Request) -> DoorRequest:
    body = b"" if request.body is None else json.dumps(request.body).encode("utf-8")
    return DoorRequest(
        method=request.method, path=request.path, query=request.query, headers=request.headers, body=body
    )


def door_response_to_response(id: str, door_response: DoorResponse) -> Response:
    content_type = door_response.headers.get("Content-Type", "")
    if content_type.startswith(("application/json", "application/problem+json")):
        body = json.loads(door_response.body) if door_response.body else None
        return Response(id=id, status=door_response.status, headers=dict(door_response.headers), body=body)
    if len(door_response.body) > MAX_RESPONSE_BYTES:
        problem = problems.make(
            "INTERNAL",
            f"response body of {len(door_response.body)} bytes exceeds the {MAX_RESPONSE_BYTES}-byte tether limit",
        )
        return Response(
            id=id,
            status=problem.status,
            headers={"Content-Type": "application/problem+json"},
            body=problem.to_json(),
        )
    encoded = base64.b64encode(door_response.body).decode("ascii")
    return Response(
        id=id,
        status=door_response.status,
        headers=dict(door_response.headers),
        body=encoded,
        encoding="base64",
    )
