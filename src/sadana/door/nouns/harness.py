"""The `harness` noun: the box's own status, the change feed, inventory.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 37-40.
The only noun this work item wires up; every other noun is a later, separate
work item (H20, H24, H26, H27). `harness` is also the odd one out among
nouns: it is a singleton (`GET /v1/harness`, never `GET /v1/harnesses`), and
its three read paths and one action path are the "fixed" paths requirement 1
names — `router.py` dispatches to the functions below directly rather than
through the generic `{plural}`/`{plural}/{id}` grammar, so `list`/`create`/
`update`/`remove` below exist only to satisfy `nouns.NounModule`'s shape
uniformly (so `ctx.nouns["harness"]` type-checks like every other noun); none
of the four is ever reachable through this work item's own routing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sadana import __version__, ledger
from sadana.door import capabilities, grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc, unavailable

spec = NounSpec(
    plural="harness",
    prefix="hrn",
    filterable=frozenset(),
    orderable=frozenset(),
    states=frozenset(),
    actions={
        # `from_states`/`to_state` are placeholders: this action is
        # unreachable in H19 regardless (its capability is declared but not
        # turned on, so requirement 25's capability gate answers 501 before
        # any state check would run) -- H30 is what gives it real meaning.
        "upgrade": ActionSpec(from_states=(), to_state="", capability="upgrade", scope_verb="upgrade"),
    },
    parent=None,
)


def get_harness(ctx: object, principal: Principal) -> dict[str, object]:
    """`GET /v1/harness`. `ctx` is typed `object` here rather than
    `router.DoorContext` to avoid this leaf importing the module that
    imports it (`router.py`) -- the attributes used (`runtime`, `conns`,
    `nouns`) are exactly `DoorContext`'s own, checked by `make typecheck`
    once `router.py` exists and calls this with a real one."""
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    return {
        "id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
        "version": __version__,
        "capabilities": capabilities.declared(),
        "tether": "disconnected",
        "org": ctx.runtime.org,  # type: ignore[attr-defined]
        "ledger_head": ledger.ledger_head(reader),
        "leaves_the_box": tuple(ctx.nouns),  # type: ignore[attr-defined]
    }


def get_changes(ctx: object, principal: Principal, *, since: int, limit: int) -> dict[str, object]:
    """`GET /v1/changes`. Folds the ledger's three-valued `kind`
    (`created`/`changed`/`deleted`) into the console's two-valued one
    (spec.md requirement 32) and derives `search_doc` at render time by
    re-fetching the current row through `ctx.nouns` (requirement 33) --
    never stored on the ledger row itself."""
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    rows = ledger.changes_since(reader, since, limit)
    events = []
    for row in rows:
        event: dict[str, object] = {
            "kind": "deleted" if row.kind == "deleted" else "changed",
            "harness_id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
            "noun": row.noun,
            "id": row.id,
            "updated_at": grammar.render_ts(row.at),
        }
        if row.state is not None:
            event["state"] = row.state
        if row.version is not None:
            event["version"] = row.version
        if row.kind != "deleted":
            doc = _search_doc_for(ctx, principal, row.noun, row.id)
            if doc is not None:
                event["search_doc"] = doc
        events.append(event)
    next_cursor = rows[-1].cursor if rows else since
    return {"data": events, "next_cursor": next_cursor}


def _search_doc_for(ctx: object, principal: Principal, noun: str, id: str) -> dict[str, object] | None:
    module = ctx.nouns.get(noun)  # type: ignore[attr-defined]
    if module is None:
        return None
    current = module.get(ctx, principal, id)
    if isinstance(current, problems.Problem) or not isinstance(current, Mapping):
        return None
    doc = module.search_doc(current)
    rendered: dict[str, object] = {"title": doc.title, "facets": dict(doc.facets)}
    if doc.subtitle is not None:
        rendered["subtitle"] = doc.subtitle
    if doc.body is not None:
        rendered["body"] = doc.body
    return rendered


def get_inventory(ctx: object, principal: Principal) -> dict[str, Sequence[dict[str, object]]]:
    """`GET /v1/inventory`."""
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    inventory = ledger.inventory(reader)
    return {
        noun: [{"id": r.id, "updated_at": grammar.render_ts(r.updated_at), "version": r.version} for r in rows]
        for noun, rows in inventory.items()
    }


# ── the rest of `NounModule`'s shape, unreachable in H19 (see module docstring) ──
def list(ctx: object, principal: Principal, params: object, parent_id: str | None = None) -> problems.Problem:
    return unavailable("harness", "list")


def get(ctx: object, principal: Principal, id: str, parent_id: str | None = None) -> problems.Problem:
    return unavailable("harness", "get")


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable("harness", "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable("harness", "update")


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable("harness", "remove")


def act(
    ctx: object, principal: Principal, id: str, name: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable("harness", name)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title=f"Harness {row.get('id', '')}")
