"""The `spans` noun: a plugin run's per-step detail.

`docs/tasks/H20-door-nouns-turn-side/spec.md` requirement 26. No table exists
yet — H21 adds one. `list` always answers an empty page and `get` always
answers `404 NOT_FOUND`, never an error naming the missing table, which would
leak an implementation detail the grammar has no code for.

`orderable` carries `created_at` even though no row is ever produced:
`grammar.parse_list_params` defaults every unspecified `order_by` to
`"created_at desc"` and rejects that default if the field isn't in the
noun's own `orderable` set — an empty `orderable` would make a bare
`GET /v1/spans` refuse itself before ever reaching this module's `list`.
"""

from __future__ import annotations

from collections.abc import Mapping

from sadana.door import grammar, problems
from sadana.door.auth import Principal
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


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    return grammar.ListResponse(data=[], next_page_token=None, count=0 if params.count else None)


def get(ctx: object, principal: Principal, id: str, parent_id: str | None = None) -> problems.Problem:
    return problems.make("NOT_FOUND", f"no span {id}")


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
    return SearchDoc(title=str(row.get("id", "")))
