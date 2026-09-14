"""The `traces` noun: one per plugin dispatch inside a turn.

`docs/tasks/H20-door-nouns-turn-side/spec.md` requirements 24-25. A child of
`runs` — reached as `/v1/runs/{run_id}/traces[/{trace_id}]`, never as a bare
`/v1/traces`. Reads `plugin_runs`, scoped via the parent run's own
`(conversation_key, turn_seq)`, resolved from `turn_runs` by the parent run
id. `prefix="run"`, not the reserved `"trc"`: `plugin_runs.id` is already
minted `run_<uuid7>` by OBSERVABILITY-01 (`observability.py`, out of this
artifact's file set to re-mint) — spec.md § Design, "Where the trace-id
prefix stayed put."
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from sadana.door import grammar, problems
from sadana.door.auth import Principal, account_key_for
from sadana.door.nouns import NounSpec, SearchDoc, unavailable

CAPABILITIES: tuple[str, ...] = ()

spec = NounSpec(
    plural="traces",
    prefix="run",
    parent="runs",
    filterable=frozenset({"state", "started_at"}),
    orderable=frozenset({"created_at"}),
    states=frozenset({"open", "closed", "failed"}),
)


def _reader(ctx: object) -> sqlite3.Connection:
    return ctx.conns.reader()  # type: ignore[attr-defined]


def _parent_run(conn: sqlite3.Connection, account_key: str, parent_id: str | None) -> sqlite3.Row | None:
    if parent_id is None:
        return None
    return conn.execute(
        "SELECT r.conversation_key, r.turn_seq FROM turn_runs r "
        "JOIN conversation_accounts ca ON ca.conversation_key = r.conversation_key "
        "WHERE ca.account_key = ? AND r.id = ?",
        (account_key, parent_id),
    ).fetchone()


def _render(ctx: object, row: sqlite3.Row) -> dict[str, object]:
    state = "failed" if row["failed_node"] is not None else "closed"
    return {
        "id": row["id"],
        "created_at": grammar.render_ts(row["started_at"]),
        "updated_at": grammar.render_ts(row["ended_at"]),
        "state": state,
        "tags": {},
        "harness_id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
        "version": row["version"],
        # A DAG's execution always starts at its own entry node — no second
        # value derived or stored (spec.md requirement 25).
        "root_node": row["entry"],
        "node_count": row["node_count"],
        "started_at": grammar.render_ts(row["started_at"]),
        "ended_at": grammar.render_ts(row["ended_at"]) if row["ended_at"] is not None else None,
        "plugin": row["plugin"],
        "entry": row["entry"],
        "failed_node": row["failed_node"],
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    conn = _reader(ctx)
    parent = _parent_run(conn, account_key_for(principal), parent_id)
    if parent is None:
        return grammar.ListResponse(data=[], next_page_token=None, count=0 if params.count else None)
    rows = conn.execute(
        "SELECT * FROM plugin_runs WHERE conversation_key = ? AND turn_seq = ? AND id IS NOT NULL",
        (parent["conversation_key"], parent["turn_seq"]),
    ).fetchall()
    rendered = [_render(ctx, r) for r in rows]
    return grammar.page(rendered, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> Mapping[str, object] | problems.Problem:
    conn = _reader(ctx)
    parent = _parent_run(conn, account_key_for(principal), parent_id)
    if parent is None:
        return problems.make("NOT_FOUND", f"no trace {id}")
    row = conn.execute(
        "SELECT * FROM plugin_runs WHERE conversation_key = ? AND turn_seq = ? AND id = ?",
        (parent["conversation_key"], parent["turn_seq"], id),
    ).fetchone()
    if row is None:
        return problems.make("NOT_FOUND", f"no trace {id}")
    return _render(ctx, row)


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable("traces", "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable("traces", "update")


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable("traces", "remove")


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> problems.Problem:
    return unavailable("traces", name)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title=str(row.get("entry", "")), facets={"plugin": row.get("plugin")})
