"""The `workflow` noun: one per manifest `Entry` — a plugin's own declared
entry point into its graph (H24,
`docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`).

Not a row — a pure projection over `_plugin_manifests.assessed_by_directory_name()`'s
already-parsed manifests, recomputed on every request, the same posture
`agent_templates.py` already takes for a resource with no table behind it.
Its id is a pure function of `(plugin directory name, entry.tool)` —
`"wfl_" + sha256(...)[:32]`, the same documented exception
`agent_templates.template_id`'s own `"tmpl_" + sha256(name)[:32]` already
established in `docs/console/nouns.md`. `node_count` is the plugin's whole
declared graph, not a per-entry reachable subset (spec.md § Rejected
alternatives: no console screen needs the narrower count, and computing it
would be a strictly heavier step than the one this design already pays for
in `editor_layout._depths`).

Identity is keyed by a plugin's *directory* name throughout — matching
`plugin_state.name` and `client_surface.enabled_plugin_set`'s own filter —
never a manifest's own declared name, which can differ from the directory a
plugin actually lives in. Read via `_plugin_manifests.py`, never
`plugin_manifest.discover_plugins()` (`check_bodies=True` would hide this
entry's own plugin the moment any of its steps still needs code — see that
module's own docstring).

Read-only: `create`/`update`/`remove`/every action answer
`HARNESS_CAPABILITY_MISSING` via `nouns.unavailable()`.
"""

from __future__ import annotations

from collections.abc import Mapping

from sadana import plugins
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, unavailable
from sadana.door.nouns._plugin_manifests import assessed_by_directory_name, synthetic_id

spec = NounSpec(
    plural="workflows",
    prefix="wfl",
    filterable=frozenset({"name"}),
    orderable=frozenset({"created_at"}),
    states=frozenset(),
    actions={},
    parent="plugins",
)

_EPOCH = grammar.render_ts(0.0)


def workflow_id(plugin_directory_name: str, tool: str) -> str:
    return synthetic_id("wfl", plugin_directory_name, tool)


def _render(
    directory_name: str, manifest: plugins.Manifest, entry: plugins.Entry, *, harness_id: str
) -> dict[str, object]:
    return {
        "id": workflow_id(directory_name, entry.tool),
        "created_at": _EPOCH,
        "updated_at": _EPOCH,
        "state": None,
        "tags": {},
        "harness_id": harness_id,
        "version": 1,
        "name": entry.tool,
        "node_count": len(manifest.nodes),
    }


def _plugin_directory_name(ctx: object, plugin_id: str) -> str | None:
    row = ctx.conns.reader().execute("SELECT name FROM plugin_state WHERE id = ?", (plugin_id,)).fetchone()  # type: ignore[attr-defined]
    return str(row["name"]) if row is not None else None


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse | problems.Problem:
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    assessed = assessed_by_directory_name(plugins._plugins_root())
    if parent_id is not None:
        directory_name = _plugin_directory_name(ctx, parent_id)
        if directory_name is None:
            return problems.make("NOT_FOUND", f"no plugin {parent_id}")
        found = assessed.get(directory_name)
        assessed = {directory_name: found} if found is not None else {}
    rows = [
        _render(name, manifest, e, harness_id=harness_id)
        for name, (manifest, _waiting) in assessed.items()
        for e in manifest.entries
    ]
    return grammar.page(rows, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    for name, (manifest, _waiting) in assessed_by_directory_name(plugins._plugins_root()).items():
        for entry in manifest.entries:
            if workflow_id(name, entry.tool) == id:
                return _render(name, manifest, entry, harness_id=harness_id)
    return problems.make("NOT_FOUND", f"no workflow {id}")


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
    return SearchDoc(title=str(row.get("name", "")), subtitle=f"{row.get('node_count', 0)} steps")
