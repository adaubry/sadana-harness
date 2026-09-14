"""The `artifacts` noun: what a run's plugin left behind.

`docs/tasks/H20-door-nouns-turn-side/spec.md` requirements 27-31. Reads the
`artifacts` table OBSERVABILITY-01 already writes
(`src/sadana/observability.py`); nothing here writes to it — `create` is
always `HARNESS_CAPABILITY_MISSING`.

`download` is the one action in this whole work item whose result is a real
byte stream, not a JSON resource — `router.py`'s own `_handle` now passes a
`door.request.DoorResponse` straight through when an `act()` returns one
(spec.md § Design "The operation-resource wall" is a different question;
this is the one router.py change plan.md actually authorizes). `act()`'s own
return annotation stays exactly what `NounModule` declares
(`Mapping[str, object] | Problem`) — the one `DoorResponse` return is a
deliberate, narrow `type: ignore`, not a widened protocol.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path

from sadana import artifact_store
from sadana.door import grammar, problems
from sadana.door.auth import Principal, account_key_for
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc, check_if_match, unavailable
from sadana.door.request import DoorResponse

CAPABILITIES: tuple[str, ...] = ("artifacts.download",)

spec = NounSpec(
    plural="artifacts",
    prefix="art",
    filterable=frozenset({"run_id", "kind", "mime", "created_at"}),
    orderable=frozenset({"created_at"}),
    states=frozenset({"ready", "expired"}),
    actions={
        "download": ActionSpec(
            from_states=("ready",),
            to_state="ready",
            capability="artifacts.download",
            scope_verb="download",
            consequential=False,
        ),
    },
)


def _reader(ctx: object) -> sqlite3.Connection:
    return ctx.conns.reader()  # type: ignore[attr-defined]


def _row(conn: sqlite3.Connection, account_key: str, id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT a.* FROM artifacts a "
        "JOIN conversation_accounts ca ON ca.conversation_key = a.conversation_key "
        "WHERE ca.account_key = ? AND a.id = ?",
        (account_key, id),
    ).fetchone()


def _run_id_for(conn: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    """The `plugin_runs` row this artifact was recorded alongside — same
    composite key `observability._insert_artifacts` files both under
    (spec.md requirement 28)."""
    found = conn.execute(
        "SELECT id FROM plugin_runs WHERE conversation_key = ? AND turn_seq = ? AND seq_in_turn = ?",
        (row["conversation_key"], row["turn_seq"], row["seq_in_turn"]),
    ).fetchone()
    return found["id"] if found is not None else None


def _render(ctx: object, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "created_at": grammar.render_ts(row["created_at"]),
        "updated_at": grammar.render_ts(row["updated_at"]),
        "state": row["state"],
        "tags": {},
        "harness_id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
        "version": row["version"],
        "run_id": _run_id_for(conn, row),
        "filename": row["name"],
        "mime": row["mime"],
        "size_bytes": row["size_bytes"],
        "kind": row["kind"],
        "url": row["ref"] if row["kind"] == "link" else None,
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    conn = _reader(ctx)
    account_key = account_key_for(principal)
    rows = conn.execute(
        "SELECT a.* FROM artifacts a "
        "JOIN conversation_accounts ca ON ca.conversation_key = a.conversation_key "
        "WHERE ca.account_key = ?",
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
        return problems.make("NOT_FOUND", f"no artifact {id}")
    return _render(ctx, conn, row)


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable("artifacts", "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable("artifacts", "update")


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable("artifacts", "remove")


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> Mapping[str, object] | problems.Problem:
    if name != "download":
        return problems.make("HARNESS_CAPABILITY_MISSING", f"artifacts.{name} is not available on this harness")
    conn = _reader(ctx)
    row = _row(conn, account_key_for(principal), id)
    if row is None:
        return problems.make("NOT_FOUND", f"no artifact {id}")
    precondition = check_if_match(row, if_match)  # type: ignore[arg-type]
    if precondition is not None:
        return precondition

    run_dir = artifact_store.for_run(row["conversation_key"], row["turn_seq"], row["seq_in_turn"])
    candidate = str(run_dir / row["ref"])
    if not artifact_store.contains(run_dir, candidate):
        return problems.make("VALIDATION", "artifact path escapes its run directory")

    path = Path(candidate)
    if not path.is_file():
        return problems.make("NOT_FOUND", f"no artifact file for {id}")

    mime = row["mime"] or "application/octet-stream"
    body_bytes = path.read_bytes()
    return DoorResponse(  # type: ignore[return-value]
        status=200,
        headers={"Content-Type": mime, "Content-Length": str(len(body_bytes))},
        body=body_bytes,
    )


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(
        title=str(row.get("filename", "")),
        facets={"kind": row.get("kind"), "run": row.get("run_id")},
    )
