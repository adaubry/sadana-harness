"""The door's request/response envelope — pure data, no socket.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirement 41.
`router.handle()` takes a `DoorRequest` and returns a `DoorResponse`; a
transport (`serve.py`, or the conformance test itself) is the only thing that
ever constructs the first or reads the second.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from sadana.door import problems


@dataclass(frozen=True)
class DoorRequest:
    method: str
    path: str
    query: str
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class DoorResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


def json_response(status: int, payload: object, *, etag: str | None = None) -> DoorResponse:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if etag is not None:
        headers["ETag"] = f'"{etag}"'
    return DoorResponse(status=status, headers=headers, body=json.dumps(payload).encode("utf-8"))


def problem_response(problem: problems.Problem) -> DoorResponse:
    headers = {"Content-Type": "application/problem+json"}
    if problem.status == 429:
        headers["Retry-After"] = "1"
    return DoorResponse(status=problem.status, headers=headers, body=json.dumps(problem.to_json()).encode("utf-8"))
