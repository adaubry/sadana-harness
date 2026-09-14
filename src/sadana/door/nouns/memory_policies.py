"""The `memory_policies` noun: the rule that decides what's worth
remembering about the account talking to this box (H26,
`docs/tasks/H26-door-nouns-agents-memory/spec.md`).

One row per account, always — `memory_store.get_rubric_override` returns
`""` for "no override yet," which isn't enough for the door to render a
real resource (no `id`, no `version` for `ETag`/`If-Match`). `get`/`list`
render the account's real override row when one exists, else a synthetic
default: `id = "rub_default"`, `version = 0`, `updated_by = "system"`,
`updated_at` = now. That timestamp is not a fact — nothing happened at a
fixed moment for an account with no override — documented here the same way
`console_fit_plan.md` §5(b) already documents a legacy row's filled-in
timestamp as a floor, not a fact.

`get`/`update` 404 when the path's `{id}` doesn't match the account's
current row id, the same one-row-per-account shape `persona_selections`
already uses. `create`/`remove`/every action are declined: there's nothing
to create or delete, only a value to set.
"""

from __future__ import annotations

from collections.abc import Mapping

from sadana import memory_store
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, unavailable

spec = NounSpec(
    plural="memory_policies",
    prefix="rub",
    filterable=frozenset(),
    orderable=frozenset({"created_at"}),
    states=frozenset(),
    actions={},
    parent=None,
)

_DEFAULT_ID = "rub_default"


def _account(principal: Principal) -> str:
    return f"console:{principal.sub}"


def _current(ctx: object, account: str) -> dict[str, object]:
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    row = memory_store.get_rubric_override_row(reader, account)
    if row is None:
        now = grammar.render_ts(ctx.clock())  # type: ignore[attr-defined]
        return {
            "id": _DEFAULT_ID,
            "created_at": now,
            "updated_at": now,
            "tags": {},
            "harness_id": harness_id,
            "version": 0,
            "rubric": "",
            "updated_by": "system",
        }
    return {
        "id": row.id,
        "created_at": grammar.render_ts(row.created_at),
        "updated_at": grammar.render_ts(row.updated_at),
        "tags": {},
        "harness_id": harness_id,
        "version": row.version,
        "rubric": row.rubric_text,
        "updated_by": row.updated_by or "system",
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    return grammar.page([_current(ctx, _account(principal))], params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    current = _current(ctx, _account(principal))
    if current["id"] != id:
        return problems.make("NOT_FOUND", f"no memory_policy {id}")
    return current


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable(spec.plural, "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> dict[str, object] | problems.Problem:
    account = _account(principal)
    current = _current(ctx, account)
    if current["id"] != id:
        return problems.make("NOT_FOUND", f"no memory_policy {id}")
    if if_match is None:
        return problems.make("PRECONDITION_FAILED", "If-Match is required for this request")
    if if_match != str(current["version"]):
        return problems.make("PRECONDITION_FAILED", f"version is {current['version']}, If-Match named {if_match}")
    rubric = body.get("rubric")
    if not isinstance(rubric, str):
        return problems.make("VALIDATION", "rubric is required")
    memory_store.set_rubric_override(ctx.conns.writer, account, rubric, now=ctx.clock())  # type: ignore[attr-defined]
    return _current(ctx, account)


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable(spec.plural, "remove")


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> problems.Problem:
    return unavailable(spec.plural, name)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title="Memory policy", facets={"updated_by": row.get("updated_by")})
