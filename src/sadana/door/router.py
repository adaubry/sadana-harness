"""`handle()`: one pure function that answers every request the console
sends, in the console's grammar.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 1-3,
24-27, 41-46. The fixed pipeline: verify -> route -> scope -> idempotency
replay (POST) -> state/capability/scope gates (actions) -> dispatch through
`operations.run_bounded` -> ETag -> idempotency store (POST) -> a promoted
call becomes `202`. `If-Match` is not a separate router-level step: the
`if_match` header value is threaded into `update`/`remove`/`act` and the
noun's own implementation checks it atomically against the row it is about
to write, which is what avoids a check-then-write race a router-level
comparison would have.

Pure: no socket, no clock except `ctx.clock`, no environment read outside
`DoorContext` construction (requirement 42). `handle` never raises — an
unexpected exception is caught once, here, logged, and answered as `500
INTERNAL` with no traceback in the body (requirement 3).
"""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl

from sadana import gateway
from sadana.door import grammar, idempotency, operations, problems
from sadana.door.auth import BoxIdentity, Principal, Verifier, require_scope
from sadana.door.nouns import ActionSpec, NounModule, harness
from sadana.door.request import DoorRequest, DoorResponse, json_response, problem_response

if TYPE_CHECKING:
    from sadana import stores

_logger = logging.getLogger("sadana.door")

#: Anything a `POST` takes longer than this to answer becomes a durable
#: `Operation` and a `202` instead of blocking the response.
_OPERATION_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class DoorContext:
    conns: stores.Connections
    runtime: BoxIdentity
    verifier: Verifier
    capabilities: tuple[str, ...]
    nouns: Mapping[str, NounModule]
    clock: Callable[[], float]


@dataclass(frozen=True)
class _Route:
    kind: str
    plural: str | None = None
    id: str | None = None
    action_name: str | None = None
    since: int = 0
    limit: int = 100
    operation_id: str | None = None


def _not_found(method: str, path: str) -> problems.Problem:
    return problems.make("NOT_FOUND", f"no route for {method} {path}")


def _route(method: str, path: str, query: str) -> _Route | problems.Problem:
    if not path.startswith("/v1/"):
        return _not_found(method, path)
    segments = [s for s in path[len("/v1/") :].split("/") if s]
    if not segments:
        return _not_found(method, path)
    head = segments[0]

    if head == "harness" and len(segments) == 1:
        if method == "GET":
            return _Route(kind="harness")
        return _not_found(method, path)
    if head == "changes" and len(segments) == 1:
        if method == "GET":
            params = dict(parse_qsl(query))
            try:
                since = int(params.get("since", "0"))
                limit = int(params.get("limit", "100"))
            except ValueError:
                return problems.make("VALIDATION", "since/limit must be integers")
            return _Route(kind="changes", since=since, limit=limit)
        return _not_found(method, path)
    if head == "inventory" and len(segments) == 1:
        if method == "GET":
            return _Route(kind="inventory")
        return _not_found(method, path)
    if head == "operations" and len(segments) == 2:
        if method == "GET":
            return _Route(kind="operation", operation_id=segments[1])
        return _not_found(method, path)
    if head == "harness" and len(segments) == 3 and segments[1] == "actions":
        if method == "POST":
            return _Route(kind="act", plural="harness", action_name=segments[2])
        return _not_found(method, path)

    plural = head
    if len(segments) == 1:
        if method == "GET":
            return _Route(kind="list", plural=plural)
        if method == "POST":
            return _Route(kind="create", plural=plural)
        return _not_found(method, path)
    if len(segments) == 2:
        if method == "GET":
            return _Route(kind="get", plural=plural, id=segments[1])
        if method == "PATCH":
            return _Route(kind="update", plural=plural, id=segments[1])
        if method == "DELETE":
            return _Route(kind="remove", plural=plural, id=segments[1])
        return _not_found(method, path)
    if len(segments) == 4 and segments[2] == "actions":
        if method == "POST":
            return _Route(kind="act", plural=plural, id=segments[1], action_name=segments[3])
        return _not_found(method, path)

    # One level of child nesting is in the grammar (requirement 1) for a
    # later noun to use; nothing registered in H19 declares a `parent`, and
    # the noun protocol itself carries only one `parent_id`, so a path
    # shaped deeper than that (a grandchild segment) has no dispatch target
    # yet. Reserved, not implemented: a real 404 today, not a guess.
    return _not_found(method, path)


_READ_KINDS = frozenset({"list", "get"})
_ETAG_KINDS = frozenset({"get", "create", "update"})


def _scope_for(route: _Route, noun: NounModule, action: ActionSpec | None) -> str:
    if route.kind == "act":
        verb = action.scope_verb if action is not None else (route.action_name or "")
        return f"{noun.spec.plural}:{verb}"
    verb = "read" if route.kind in _READ_KINDS else "write"
    return f"{noun.spec.plural}:{verb}"


def _dispatch(ctx: DoorContext, principal: Principal, route: _Route, noun: NounModule, request: DoorRequest) -> object:
    """The verb call only. State/capability gates for an action are resolved
    in `_handle`, before this is ever reached (see its own comment) — purely
    static checks against `noun.spec`/`ctx.capabilities` and one `noun.get`
    read, none of which benefit from `run_bounded`'s thread or need to be
    exposed to its promotion race."""
    if_match = gateway.header_value(request.headers, "If-Match").strip('"') or None
    if route.kind == "list":
        params = grammar.parse_list_params(
            request.query, filterable=noun.spec.filterable, orderable=noun.spec.orderable
        )
        if isinstance(params, problems.Problem):
            return params
        return noun.list(ctx, principal, params)
    if route.kind == "get":
        return noun.get(ctx, principal, route.id)  # type: ignore[arg-type]
    if route.kind == "create":
        return noun.create(ctx, principal, _json_body(request.body))
    if route.kind == "update":
        return noun.update(ctx, principal, route.id, _json_body(request.body), if_match)  # type: ignore[arg-type]
    if route.kind == "remove":
        return noun.remove(ctx, principal, route.id, if_match)  # type: ignore[arg-type]
    if route.kind == "act":
        return noun.act(ctx, principal, route.id, route.action_name, _json_body(request.body), if_match)  # type: ignore[arg-type]
    raise AssertionError(f"unreachable route kind {route.kind!r}")


def _json_body(body: bytes) -> Mapping[str, object]:
    if not body:
        return {}
    return dict(json.loads(body))


def handle(request: DoorRequest, *, ctx: DoorContext) -> DoorResponse:
    try:
        return _handle(request, ctx=ctx)
    except Exception:  # noqa: BLE001 -- the one place an unexpected bug becomes a response, not a crash
        _logger.error("unhandled error answering %s %s\n%s", request.method, request.path, traceback.format_exc())
        return problem_response(problems.make("INTERNAL", "the door hit an unexpected error"))


def _handle(request: DoorRequest, *, ctx: DoorContext) -> DoorResponse:
    principal = ctx.verifier.verify(request.headers, box=ctx.runtime)
    if isinstance(principal, problems.Problem):
        return problem_response(principal)

    route = _route(request.method, request.path, request.query)
    if isinstance(route, problems.Problem):
        return problem_response(route)

    if route.kind == "harness":
        return json_response(200, harness.get_harness(ctx, principal))
    if route.kind == "changes":
        return json_response(200, harness.get_changes(ctx, principal, since=route.since, limit=route.limit))
    if route.kind == "inventory":
        return json_response(200, harness.get_inventory(ctx, principal))
    if route.kind == "operation":
        op = operations.get(ctx.conns.reader(), route.operation_id or "")
        if op is None:
            return problem_response(problems.make("NOT_FOUND", "no such operation"))
        return json_response(200, {"operation": op.to_wire()})

    noun = ctx.nouns.get(route.plural or "")
    if noun is None:
        return problem_response(_not_found(request.method, request.path))

    action: ActionSpec | None = None
    if route.kind == "act":
        action = noun.spec.actions.get(route.action_name or "")
        if action is None:
            # Not a declared verb at all -- resolved before scope, since
            # there is no scope name to check for an action that doesn't
            # exist (requirement 26; distinct from a declared action whose
            # *capability* isn't turned on, which _dispatch answers later).
            return problem_response(
                problems.make(
                    "HARNESS_CAPABILITY_MISSING",
                    f"{noun.spec.plural}.{route.action_name} is not available on this harness",
                )
            )

    scope_required = _scope_for(route, noun, action)
    scope_problem = require_scope(principal, scope_required)
    if scope_problem is not None:
        return problem_response(scope_problem)

    idem_key = gateway.header_value(request.headers, "Idempotency-Key") if route.kind in ("create", "act") else ""
    if idem_key:
        replayed = idempotency.replay(
            ctx.conns.reader(),
            key=idem_key,
            sub=principal.sub,
            method=request.method,
            path=request.path,
            body=request.body,
        )
        if replayed is not None:
            if not replayed.matched:
                mismatch = problems.make("IDEMPOTENCY_MISMATCH", "Idempotency-Key was reused with a different body")
                return problem_response(mismatch)
            return DoorResponse(
                status=replayed.status or 200, headers=replayed.headers or {}, body=replayed.body or b""
            )

    # State, then capability: requirement 25's own order, both resolved here
    # -- before dispatch -- because both are answerable without ever calling
    # the verb. State needs one `noun.get` read; capability needs nothing but
    # `ctx.capabilities`. Neither belongs behind `run_bounded`'s thread pool
    # or its promotion race, which exist for the verb call itself, not for a
    # gate that was already decidable.
    if route.kind == "act":
        assert action is not None  # the undeclared case already returned, above
        if action.from_states:
            current = noun.get(ctx, principal, route.id)  # type: ignore[arg-type]
            if isinstance(current, problems.Problem):
                return problem_response(current)
            current_state = current.get("state")
            if current_state not in action.from_states:
                return problem_response(
                    problems.make(
                        "CONFLICT",
                        f"{route.action_name} requires state {list(action.from_states)}; "
                        f"current state is {current_state}",
                    )
                )
        if action.capability is not None and action.capability not in ctx.capabilities:
            return problem_response(
                problems.make("HARNESS_CAPABILITY_MISSING", f"requires capability {action.capability}")
            )

    resource = (noun.spec.plural, route.id) if route.id else None
    if route.kind in _READ_KINDS:
        # Always fast, never promoted: skip the thread pool entirely rather
        # than submit-then-immediately-block on every list/get.
        bounded = operations.BoundedResult(done=True, value=_dispatch(ctx, principal, route, noun, request))
    else:
        bounded = operations.run_bounded(
            ctx.conns,
            lambda: _dispatch(ctx, principal, route, noun, request),
            timeout=_OPERATION_TIMEOUT_SECONDS,
            resource=resource,
        )

    if not bounded.done:
        response = json_response(202, {"operation": bounded.operation.to_wire()})  # type: ignore[union-attr]
    else:
        result = bounded.value
        if isinstance(result, problems.Problem):
            response = problem_response(result)
        elif route.kind == "remove":
            response = DoorResponse(status=204, headers={}, body=b"")
        elif route.kind == "list":
            response = json_response(200, _render_list(result))
        else:
            status = 201 if route.kind == "create" else 200
            etag = str(result.get("version")) if route.kind in _ETAG_KINDS and isinstance(result, Mapping) else None
            response = json_response(status, result, etag=etag)

    if idem_key:
        idempotency.store(
            ctx.conns.writer,
            key=idem_key,
            sub=principal.sub,
            method=request.method,
            path=request.path,
            body=request.body,
            response=response,
            now=ctx.clock(),
        )
    return response


def _render_list(result: object) -> dict[str, object]:
    body: dict[str, object] = {"data": list(result.data), "next_page_token": result.next_page_token}  # type: ignore[attr-defined]
    if result.count is not None:  # type: ignore[attr-defined]
        body["count"] = result.count  # type: ignore[attr-defined]
    return body
