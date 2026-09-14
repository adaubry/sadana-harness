"""The `runs` noun: one per turn, what it cost and how it ended.

`docs/tasks/H20-door-nouns-turn-side/spec.md` requirements 21-23. Reads
`turn_runs` only — a `plugin_runs` row (a turn's own plugin dispatches)
surfaces as `traces`, never here (`traces.py`'s own module docstring).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from sadana.door import grammar, problems, run_control
from sadana.door.auth import Principal, account_key_for
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc

CAPABILITIES: tuple[str, ...] = ()

#: wire.md §6's own table. `completed` degrades further to `"failed_node"`
#: when a `plugin_runs` row under this same turn recorded one — requirement 22.
_EXIT_REASON_MAP: dict[str, str] = {
    "completed": "completed",
    "budget_exhausted": "budget_exhausted",
    "wall_clock_exhausted": "budget_exhausted",
    "interrupted": "stopped",
    "persistence_failed": "error",
    "provider_failed": "error",
    "context_overflow_unhandled": "error",
    "invalid_tool_calls": "error",
}

spec = NounSpec(
    plural="runs",
    prefix="run",
    filterable=frozenset({"state", "exit_reason", "agent_id", "conversation_id", "created_at"}),
    orderable=frozenset({"created_at", "duration_ms"}),
    states=frozenset({"running", "waiting", "done", "failed", "stopped"}),
    actions={
        "stop": ActionSpec(
            from_states=("running", "waiting"), to_state="stopped", capability="runs.stop", scope_verb="stop"
        ),
    },
)


def _reader(ctx: object) -> sqlite3.Connection:
    return ctx.conns.reader()  # type: ignore[attr-defined]


def _row(conn: sqlite3.Connection, account_key: str, id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT r.* FROM turn_runs r "
        "JOIN conversation_accounts ca ON ca.conversation_key = r.conversation_key "
        "WHERE ca.account_key = ? AND r.id = ?",
        (account_key, id),
    ).fetchone()


def _agent_id_for(conn: sqlite3.Connection, conversation_key: str) -> str | None:
    convo = conn.execute("SELECT agent FROM conversations WHERE key = ?", (conversation_key,)).fetchone()
    if convo is None or not convo["agent"]:
        return None
    agent = conn.execute("SELECT id FROM agents WHERE name = ?", (convo["agent"],)).fetchone()
    return agent["id"] if agent is not None else None


def _has_failed_node(conn: sqlite3.Connection, conversation_key: str, turn_seq: int) -> bool:
    found = conn.execute(
        "SELECT 1 FROM plugin_runs WHERE conversation_key = ? AND turn_seq = ? AND failed_node IS NOT NULL LIMIT 1",
        (conversation_key, turn_seq),
    ).fetchone()
    return found is not None


def _render(ctx: object, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    conversation = conn.execute("SELECT id FROM conversations WHERE key = ?", (row["conversation_key"],)).fetchone()
    message = conn.execute(
        "SELECT id FROM messages WHERE run_id = ? AND role = 'user' LIMIT 1", (row["id"],)
    ).fetchone()

    exit_reason = _EXIT_REASON_MAP.get(row["exit_reason"], "error")
    if row["exit_reason"] == "completed" and _has_failed_node(conn, row["conversation_key"], row["turn_seq"]):
        exit_reason = "failed_node"

    return {
        "id": row["id"],
        "created_at": grammar.render_ts(row["recorded_at"]),
        "updated_at": grammar.render_ts(row["recorded_at"]),
        "state": row["state"],
        "tags": {},
        "harness_id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
        "version": row["version"],
        "conversation_id": conversation["id"] if conversation is not None else None,
        "message_id": message["id"] if message is not None else None,
        "agent_id": _agent_id_for(conn, row["conversation_key"]),
        # Never derivable in this work item: nothing records which
        # `plugin_runs` row (if any) spawned this turn as a child conversation
        # — spec.md requirement 22's own named gap.
        "plugin_id": None,
        "exit_reason": exit_reason,
        "duration_ms": row["duration_s"] * 1000,
        "iterations": row["model_calls"],
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    conn = _reader(ctx)
    account_key = account_key_for(principal)
    rows = conn.execute(
        "SELECT r.* FROM turn_runs r "
        "JOIN conversation_accounts ca ON ca.conversation_key = r.conversation_key "
        "WHERE ca.account_key = ? AND r.id IS NOT NULL",
        (account_key,),
    ).fetchall()
    rendered = [_render(ctx, conn, r) for r in rows]
    return grammar.page(rendered, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> Mapping[str, object] | problems.Problem:
    conn = _reader(ctx)
    row = _row(conn, account_key_for(principal), id)
    if row is None:
        return problems.make("NOT_FOUND", f"no run {id}")
    return _render(ctx, conn, row)


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return problems.make("HARNESS_CAPABILITY_MISSING", "runs.create is not available on this harness")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return problems.make("HARNESS_CAPABILITY_MISSING", "runs.update is not available on this harness")


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return problems.make("HARNESS_CAPABILITY_MISSING", "runs.remove is not available on this harness")


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> Mapping[str, object] | problems.Problem:
    if name != "stop":
        return problems.make("HARNESS_CAPABILITY_MISSING", f"runs.{name} is not available on this harness")
    # H21. `router.py`'s own `from_states=("running", "waiting")` gate
    # already confirmed, via `get()`, that `id` names a run this account
    # can see and that its own DB row is still in a live state, before
    # dispatching here — this call does not itself flip any state; the
    # observer's own `turn_finished` is what eventually settles it to
    # `stopped`, cooperatively, once the turn notices. `False` here means
    # this *process* is not holding a run by that id right now — already
    # finished between the router's own check and this call, or the
    # process restarted since the run started.
    if not run_control.request_stop(id):
        return problems.make("CONFLICT", f"run {id} is not currently held by this process")
    conn = _reader(ctx)
    row = _row(conn, account_key_for(principal), id)
    if row is None:
        return problems.make("NOT_FOUND", f"no run {id}")
    return _render(ctx, conn, row)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    id_value = str(row.get("id", ""))
    return SearchDoc(
        title=f"Run {id_value[-6:]}",
        subtitle=str(row.get("exit_reason") or row.get("state") or ""),
        body=str(row.get("agent_id") or ""),
        facets={
            "state": row.get("state"),
            "exit_reason": row.get("exit_reason"),
            "conversation": row.get("conversation_id"),
            "harness": row.get("harness_id"),
        },
    )
