"""The `memory_entries` noun: what has been remembered about the account
talking to this box (H26, `docs/tasks/H26-door-nouns-agents-memory/spec.md`).

Scoped to the acting account (`console_fit_plan.md` §5(d)) on every read and
write — `memory_store.get_entry_by_id`'s own `WHERE` clause is the whole
enforcement, so a row that exists under a different account is `404`, never
`403` (wire.md's visibility rule).

Two read paths exist in `memory_store.py` on purpose: `list_entries`
excludes `forgotten` rows because it feeds the model (`memory.retrieve`
never sees them again); this noun reads `list_entries_full`, because a
person deciding whether `forget` actually took has to be able to see the
forgotten row, not have it hidden the way recall must.

`kind`/`source` render `NULL` columns (a legacy row, written before H26) as
`"fact"`/`"conversation"` at read time — a read-time default, never a
backfill write (`console_fit_plan.md` §5(b)).

`create` is declined: nothing writes a memory entry through the door yet —
the one real write path is the model's own `memory.remember` tool call.
`remove` is the purge (`memory_store.delete_entry`); `forget` (the action)
is the reversible one (`memory_store.forget_entry`).
"""

from __future__ import annotations

from collections.abc import Mapping

from sadana import memory_store
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc, unavailable

spec = NounSpec(
    plural="memory_entries",
    prefix="mem",
    filterable=frozenset({"state", "kind", "created_at"}),
    orderable=frozenset({"created_at"}),
    states=frozenset({"kept", "forgotten"}),
    actions={
        "forget": ActionSpec(from_states=("kept",), to_state="forgotten", capability=None, scope_verb="forget"),
    },
    parent=None,
)


def _account(principal: Principal) -> str:
    return f"console:{principal.sub}"


def _render(row: memory_store.MemoryEntryRow, *, harness_id: str) -> dict[str, object]:
    return {
        "id": row.id,
        "created_at": grammar.render_ts(row.created_at),
        "updated_at": grammar.render_ts(row.updated_at),
        "state": row.state,
        "tags": {},
        "harness_id": harness_id,
        "version": row.version,
        "content": row.content,
        "kind": row.kind or "fact",
        "source": row.source or "conversation",
        "conversation_id": row.conversation_key,
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    rows = [_render(row, harness_id=harness_id) for row in memory_store.list_entries_full(reader, _account(principal))]
    return grammar.page(rows, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    row = memory_store.get_entry_by_id(ctx.conns.reader(), _account(principal), id)  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no memory_entry {id}")
    return _render(row, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable(spec.plural, "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable(spec.plural, "update")


def _check_if_match(row: memory_store.MemoryEntryRow, if_match: str | None) -> problems.Problem | None:
    if if_match is None:
        return problems.make("PRECONDITION_FAILED", "If-Match is required for this request")
    if if_match != str(row.version):
        return problems.make("PRECONDITION_FAILED", f"version is {row.version}, If-Match named {if_match}")
    return None


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> None | problems.Problem:
    account = _account(principal)
    row = memory_store.get_entry_by_id(ctx.conns.reader(), account, id)  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no memory_entry {id}")
    precondition = _check_if_match(row, if_match)
    if precondition is not None:
        return precondition
    memory_store.delete_entry(ctx.conns.writer, account, row.entry_key)  # type: ignore[attr-defined]
    return None


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> dict[str, object] | problems.Problem:
    account = _account(principal)
    row = memory_store.get_entry_by_id(ctx.conns.reader(), account, id)  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no memory_entry {id}")
    if name != "forget":
        return problems.make("HARNESS_CAPABILITY_MISSING", f"{spec.plural}.{name} is not available on this harness")
    precondition = _check_if_match(row, if_match)
    if precondition is not None:
        return precondition
    memory_store.forget_entry(ctx.conns.writer, account, row.entry_key, now=ctx.clock())  # type: ignore[attr-defined]
    updated = memory_store.get_entry_by_id(ctx.conns.reader(), account, id)  # type: ignore[attr-defined]
    assert updated is not None, f"memory_entry {id} vanished mid-action"
    return _render(updated, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    content = str(row.get("content", ""))
    return SearchDoc(title=content[:80], facets={"kind": row.get("kind"), "state": row.get("state")})
