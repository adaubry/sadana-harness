"""The `spans` noun: a plugin run's per-step detail.

`docs/tasks/H20-door-nouns-turn-side/spec.md` requirement 26, made real by
`docs/tasks/H21-watched-streaming-live-runs-stop/spec.md`, which is what
first gives the `spans` table (`observability.py`) any rows to read. A
child of `traces` — reached as `/v1/traces/{trace_id}/spans[/{span_id}]` —
mirroring `traces.py`'s own child-of-`runs` shape one level down:
`traces.py`'s `_parent_run` resolves a trace's own `(conversation_key,
turn_seq)`; this file's `_parent_trace` resolves one step further, to the
`(conversation_key, turn_seq, seq_in_turn)` a `spans` row is actually keyed
by.

`orderable` carries `created_at` for the same reason it always did:
`grammar.parse_list_params` defaults every unspecified `order_by` to
`"created_at desc"` and rejects that default if the field isn't in the
noun's own `orderable` set.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from sadana.door import grammar, problems
from sadana.door.auth import Principal, account_key_for
from sadana.door.nouns import NounSpec, SearchDoc, unavailable

CAPABILITIES: tuple[str, ...] = ()

spec = NounSpec(
    plural="spans",
    prefix="spn",
    parent="traces",
    filterable=frozenset({"status", "kind", "node_id"}),
    orderable=frozenset({"created_at"}),
    states=frozenset({"ok", "error", "skipped"}),
    actions={},
)


def _reader(ctx: object) -> sqlite3.Connection:
    return ctx.conns.reader()  # type: ignore[attr-defined]


def _parent_trace(conn: sqlite3.Connection, account_key: str, parent_id: str | None) -> sqlite3.Row | None:
    if parent_id is None:
        return None
    return conn.execute(
        "SELECT p.conversation_key, p.turn_seq, p.seq_in_turn FROM plugin_runs p "
        "JOIN conversation_accounts ca ON ca.conversation_key = p.conversation_key "
        "WHERE ca.account_key = ? AND p.id = ?",
        (account_key, parent_id),
    ).fetchone()


def _render(ctx: object, row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "created_at": grammar.render_ts(row["created_at"]),
        "updated_at": grammar.render_ts(row["updated_at"]),
        "state": row["status"],
        "tags": {},
        "harness_id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
        "version": row["version"],
        "node_id": row["node"],
        "kind": row["kind"],
        "status": row["status"],
        "started_at": grammar.render_ts(row["started_at"]),
        "ended_at": grammar.render_ts(row["ended_at"]) if row["ended_at"] is not None else None,
        "input_preview": row["input_preview"],
        "output_preview": row["output_preview"],
        "error": row["error"],
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    conn = _reader(ctx)
    parent = _parent_trace(conn, account_key_for(principal), parent_id)
    if parent is None:
        return grammar.ListResponse(data=[], next_page_token=None, count=0 if params.count else None)
    rows = conn.execute(
        "SELECT * FROM spans WHERE conversation_key = ? AND turn_seq = ? AND seq_in_turn = ?",
        (parent["conversation_key"], parent["turn_seq"], parent["seq_in_turn"]),
    ).fetchall()
    rendered = [_render(ctx, r) for r in rows]
    return grammar.page(rendered, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> Mapping[str, object] | problems.Problem:
    conn = _reader(ctx)
    parent = _parent_trace(conn, account_key_for(principal), parent_id)
    if parent is None:
        return problems.make("NOT_FOUND", f"no span {id}")
    row = conn.execute(
        "SELECT * FROM spans WHERE conversation_key = ? AND turn_seq = ? AND seq_in_turn = ? AND id = ?",
        (parent["conversation_key"], parent["turn_seq"], parent["seq_in_turn"], id),
    ).fetchone()
    if row is None:
        return problems.make("NOT_FOUND", f"no span {id}")
    return _render(ctx, row)


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable("spans", "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable("spans", "update")


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable("spans", "remove")


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> problems.Problem:
    return unavailable("spans", name)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title=str(row.get("node_id", "")), facets={"kind": row.get("kind"), "status": row.get("status")})
