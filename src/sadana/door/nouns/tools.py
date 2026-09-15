"""The `tool` noun: the tool surface a plugin actually exposes right now
(H24, `docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`).

Over the *enabled* set — `client_surface.enabled_plugin_set()`, the exact
filter the real turn path already applies — not a raw manifest walk: a
disabled plugin's tools must not appear here. Derived fresh per request,
never cached on `DoorContext` (spec.md § Rejected alternatives): the cost is
the same a `list`/`get` on any other noun already pays reading
`ctx.conns.reader()`, and a cached copy could go stale the moment a plugin
is installed or disabled mid-process.

Read-only.
"""

from __future__ import annotations

from collections.abc import Mapping

from sadana import client_surface
from sadana.conversation import ToolSpec
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, unavailable
from sadana.door.nouns._plugin_manifests import synthetic_id

spec = NounSpec(
    plural="tools",
    prefix="tool",
    filterable=frozenset({"name", "plugin_id"}),
    orderable=frozenset({"created_at"}),
    states=frozenset(),
    actions={},
    parent=None,
)

_EPOCH = grammar.render_ts(0.0)


def _plugin_set(ctx: object) -> client_surface.plugin_dispatch.PluginSet:
    return client_surface.enabled_plugin_set(ctx.conns.reader())  # type: ignore[attr-defined]


def _render(tool: ToolSpec, plugin_directory_name: str, *, harness_id: str) -> dict[str, object]:
    return {
        "id": synthetic_id("tool", plugin_directory_name, tool.name),
        "created_at": _EPOCH,
        "updated_at": _EPOCH,
        "state": None,
        "tags": {},
        "harness_id": harness_id,
        "version": 1,
        "name": tool.name,
        "description": tool.describe({}),
        "plugin_id": plugin_directory_name,
    }


def _rows(ctx: object) -> tuple[dict[str, object], ...]:
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    plugin_set = _plugin_set(ctx)
    rows = []
    for tool in plugin_set.tool_specs:
        entry = plugin_set.by_tool.get(tool.name)
        directory_name = entry[0].directory.name if entry is not None else ""
        rows.append(_render(tool, directory_name, harness_id=harness_id))
    return tuple(rows)


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    return grammar.page(_rows(ctx), params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    for row in _rows(ctx):
        if row["id"] == id:
            return row
    return problems.make("NOT_FOUND", f"no tool {id}")


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
    return SearchDoc(
        title=str(row.get("name", "")),
        subtitle=str(row.get("description") or ""),
        facets={"plugin": row.get("plugin_id")},
    )
