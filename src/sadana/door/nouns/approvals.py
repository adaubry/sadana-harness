"""The `approvals` noun: every wait on a person, parked and answerable from
outside the process that asked (H18, `docs/tasks/H18-parked-approvals/spec.md`).

A `plugin_pauses` row (`conversation_store.py`) is this project's own record
of where a run is paused; this table is its console-facing twin — one row
per wait or call, with a state machine and a TTL, readable and actionable
through the door's own grammar. `list`/`get` read straight off
`conversation_store`'s own accessors; `create`/`update`/`remove` are
`unavailable()` — an approval is only ever created by a paused run and only
ever changed through its three actions; `act` is where a person's decision
actually reaches `plugin_dispatch.resume_paused_run`.

`act` bridges the door's own synchronous verb contract (`NounModule.act`,
called from `router.py`'s worker-thread pool — see `operations.run_bounded`,
which never runs an asyncio event loop on that thread) onto
`resume_paused_run`'s async one with a fresh `asyncio.run()` per call —
`expire_due`, called from `scheduling.tick()`'s own already-running loop,
awaits it directly instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Literal, cast

from sadana import conversation_store, plugin_dispatch, plugin_manifest, stores
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc, unavailable

logger = logging.getLogger(__name__)

CAPABILITIES: tuple[str, ...] = ("approvals.wait", "approvals.call")

spec = NounSpec(
    plural="approvals",
    prefix="appr",
    filterable=frozenset({"state", "kind", "run_id"}),
    # `created_at` is required here even though the work item's own default
    # order is `requested_at asc`: `door/grammar.py:parse_list_params`
    # hardcodes `created_at desc` as the no-`order_by` default and validates
    # it against a noun's own `orderable` set, with no per-noun override
    # hook — extending that shared framework mid-parallel-build was declined
    # with the user (spec.md § Concerns). A bare `GET /v1/approvals` sorts
    # `created_at desc` like every other noun; a caller states
    # `order_by=requested_at asc` explicitly for the documented default.
    orderable=frozenset({"created_at", "requested_at"}),
    states=frozenset({"waiting", "approved", "declined", "answered", "expired"}),
    actions={
        "approve": ActionSpec(from_states=("waiting",), to_state="approved", capability=None, scope_verb="approve"),
        "decline": ActionSpec(from_states=("waiting",), to_state="declined", capability=None, scope_verb="decline"),
        "answer": ActionSpec(from_states=("waiting",), to_state="answered", capability=None, scope_verb="answer"),
    },
    parent=None,
)


def _render(row: conversation_store.ApprovalRow, *, harness_id: str) -> dict[str, object]:
    return {
        "id": row.id,
        "created_at": grammar.render_ts(row.created_at),
        "updated_at": grammar.render_ts(row.updated_at),
        "state": row.state,
        "tags": {},
        "harness_id": harness_id,
        "version": row.version,
        "conversation_key": row.conversation_key,
        "turn_seq": row.turn_seq,
        "seq_in_turn": row.seq_in_turn,
        "run_id": row.run_id,
        "plugin": row.plugin,
        "entry": row.entry,
        "node_id": row.node,
        "kind": row.kind,
        "question": row.question,
        "requested_at": grammar.render_ts(row.requested_at),
        "expires_at": grammar.render_ts(row.expires_at),
        "answered_by": row.answered_by,
        "answer": row.answer,
        "decided_at": grammar.render_ts(row.decided_at) if row.decided_at is not None else None,
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    rows = [_render(r, harness_id=harness_id) for r in conversation_store.list_approvals(ctx.conns.reader())]  # type: ignore[attr-defined]
    return grammar.page(rows, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    row = conversation_store.get_approval(ctx.conns.reader(), id=id)  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no approval {id}")
    return _render(row, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable(spec.plural, "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable(spec.plural, "update")


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable(spec.plural, "remove")


_STATES: dict[str, Literal["approved", "declined", "answered"]] = {
    "approve": "approved",
    "decline": "declined",
    "answer": "answered",
}

# `approve`/`decline` only ever make sense against a parked `call`;
# `answer` only against a parked `wait`. `NounSpec.actions` states all three
# from `waiting` (the wire-level state gate router.py already runs), which
# a wait-kind row satisfies just as well as a call-kind one — this is the
# second, noun-specific gate that distinguishes them, checked here so a
# mismatch is a clean 409 rather than reaching `resume_paused_run`'s own
# internal `kind`/`decision` assertion as an uncaught 500.
_REQUIRED_KIND: dict[str, Literal["wait", "call"]] = {"approve": "call", "decline": "call", "answer": "wait"}


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> dict[str, object] | problems.Problem:
    row = conversation_store.get_approval(ctx.conns.reader(), id=id)  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no approval {id}")
    state = _STATES.get(name)
    if state is None:
        return problems.make("HARNESS_CAPABILITY_MISSING", f"{spec.plural}.{name} is not available on this harness")
    decision = cast(Literal["approve", "decline", "answer"], name)
    required_kind = _REQUIRED_KIND[name]
    if row.kind != required_kind:
        return problems.make(
            "CONFLICT", f"{name} requires a {required_kind!r}-kind approval; this one is kind {row.kind!r}"
        )
    if if_match is None:
        return problems.make("PRECONDITION_FAILED", "If-Match is required for this request")
    if if_match != str(row.version):
        return problems.make("PRECONDITION_FAILED", f"version is {row.version}, If-Match named {if_match}")

    payload = body.get("reason") if name == "decline" else body.get("answer") if name == "answer" else None
    answered_by = f"console:{principal.sub}"
    with stores.conversation_lock(row.conversation_key):
        asyncio.run(
            plugin_dispatch.resume_paused_run(
                ctx.conns.writer,  # type: ignore[attr-defined]
                row.conversation_key,
                decision=decision,
                state=state,
                payload=payload,  # type: ignore[arg-type]
                answered_by=answered_by,
                approve=plugin_manifest.parking_approve,
            )
        )
    updated = conversation_store.get_approval(ctx.conns.reader(), id=id)  # type: ignore[attr-defined]
    assert updated is not None, f"approval {id} vanished mid-action"
    return _render(updated, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    question = str(row.get("question", ""))
    return SearchDoc(
        title=question[:80],
        facets={"state": row.get("state"), "kind": row.get("kind"), "run": row.get("run_id")},
    )


async def expire_due(conns: stores.Connections, now: float) -> int:
    """Every `waiting` approval past its own `expires_at`: declined, marked
    `expired` (not `declined` — see `conversation_store.resolve_pause`'s own
    docstring on why those are different facts), ledgered. Called from
    `scheduling.tick()`'s own already-running event loop, so this awaits
    `resume_paused_run` directly rather than bridging through `asyncio.run`
    the way `act` above has to. A row whose expiry fails is logged and
    skipped, never allowed to stop another row's expiry or the tick that
    called this — the same per-item posture `scheduling.tick()` itself
    already takes toward a failed trigger."""
    due = conversation_store.due_approvals(conns.reader(), now=now)
    expired = 0
    for row in due:
        try:
            with stores.conversation_lock(row.conversation_key):
                await plugin_dispatch.resume_paused_run(
                    conns.writer,
                    row.conversation_key,
                    decision="decline",
                    state="expired",
                    payload=None,
                    answered_by="system:expiry",
                    approve=plugin_manifest.parking_approve,
                )
        except Exception:
            logger.warning("failed to expire approval %r", row.id, exc_info=True)
            continue
        expired += 1
    return expired
