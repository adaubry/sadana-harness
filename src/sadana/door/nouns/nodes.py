"""The `node` noun: one per declared step in a plugin's graph (H24,
`docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`).

Not a row — a pure projection over `_plugin_manifests.assessed_by_directory_name()`,
the same posture `workflows.py` already takes (never
`plugin_manifest.discover_plugins()` — see that module's own docstring for
why). Nested under a `workflow`, but a node listed under one is the *owning
plugin's whole graph*, not a subset reachable from that one entry —
`workflows.py`'s own `node_count` already takes this reading, for the same
reason (spec.md § Rejected alternatives): nothing in this design's field
list asks for a per-entry reachable subgraph, and computing one would be a
strictly heavier step than what this design already pays for elsewhere.

`update({label})` is the one write this noun offers: a rename, run through
`plugins.rename_node()` (which follows every arrow, port and entry that
named the old step) and `editor_server._write_manifest`'s own read-back
guard — never a field edit applied in place. Manually capability-gated: only
a declared *action* is auto-gated by `router.py`, never `update` itself.
"""

from __future__ import annotations

from collections.abc import Mapping

from sadana import editor_server, plugins
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, check_if_match, unavailable
from sadana.door.nouns._plugin_manifests import assessed_by_directory_name, external_refs, synthetic_id
from sadana.door.nouns.workflows import workflow_id

spec = NounSpec(
    plural="nodes",
    prefix="node",
    filterable=frozenset({"kind", "needs_code"}),
    orderable=frozenset({"created_at"}),
    states=frozenset(),
    actions={},
    parent="workflows",
)

_EPOCH = grammar.render_ts(0.0)


def node_id(plugin_directory_name: str, node_name: str) -> str:
    return synthetic_id("node", plugin_directory_name, node_name)


def _render(directory_name: str, node: plugins.Node, waiting: frozenset[str], *, harness_id: str) -> dict[str, object]:
    return {
        "id": node_id(directory_name, node.name),
        "created_at": _EPOCH,
        "updated_at": _EPOCH,
        "state": None,
        "tags": {},
        "harness_id": harness_id,
        "version": 1,
        "name": node.name,
        "kind": node.kind,
        "needs_code": node.name in waiting,
        "external_refs": external_refs(node),
        "body_preview": node.body,
    }


def _plugin_for_workflow(
    assessed: Mapping[str, tuple[plugins.Manifest, frozenset[str]]], wfl_id: str
) -> tuple[str, plugins.Manifest, frozenset[str]] | None:
    for name, (manifest, waiting) in assessed.items():
        for entry in manifest.entries:
            if workflow_id(name, entry.tool) == wfl_id:
                return name, manifest, waiting
    return None


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse | problems.Problem:
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    assessed = assessed_by_directory_name(plugins._plugins_root())
    if parent_id is not None:
        found = _plugin_for_workflow(assessed, parent_id)
        if found is None:
            return problems.make("NOT_FOUND", f"no workflow {parent_id}")
        name, manifest, waiting = found
        assessed = {name: (manifest, waiting)}
    rows = [
        _render(name, node, waiting, harness_id=harness_id)
        for name, (manifest, waiting) in assessed.items()
        for node in manifest.nodes
    ]
    return grammar.page(rows, params)


def _find(id: str) -> tuple[str, plugins.Manifest, plugins.Node, frozenset[str]] | None:
    for name, (manifest, waiting) in assessed_by_directory_name(plugins._plugins_root()).items():
        for node in manifest.nodes:
            if node_id(name, node.name) == id:
                return name, manifest, node, waiting
    return None


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    found = _find(id)
    if found is None:
        return problems.make("NOT_FOUND", f"no node {id}")
    name, _manifest, node, waiting = found
    return _render(name, node, waiting, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable(spec.plural, "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> dict[str, object] | problems.Problem:
    if "plugins.write" not in ctx.capabilities:  # type: ignore[attr-defined]
        return problems.make("HARNESS_CAPABILITY_MISSING", "nodes.update requires capability plugins.write")
    found = _find(id)
    if found is None:
        return problems.make("NOT_FOUND", f"no node {id}")
    name, manifest, node, waiting = found
    row = _render(name, node, waiting, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]
    precondition = check_if_match(row, if_match)
    if precondition is not None:
        return precondition

    new_label = body.get("label")
    if not isinstance(new_label, str) or not new_label:
        return problems.make("VALIDATION", "invalid update", errors=({"field": "label", "code": "required"},))
    directory = plugins._plugins_root() / name
    try:
        renamed = plugins.rename_node(manifest, node.name, new_label)
        editor_server._write_manifest(directory, renamed)
    except ValueError as exc:
        return problems.make("VALIDATION", str(exc))

    # Re-assess just this one directory, not `_find`'s full scan of every
    # plugin — the write only ever touched `directory`, so nothing else
    # could have changed (an efficiency finding from this item's own
    # self-review: the full scan was paid twice per rename for no reason).
    reassessed = editor_server.assess(directory)
    assert not isinstance(reassessed, str), f"plugin {name!r} unreadable immediately after its own rename"
    manifest_after, _problems_after, waiting_raw = reassessed
    waiting_after = frozenset(w["step"] for w in waiting_raw)
    node_after = next(n for n in manifest_after.nodes if n.name == new_label)
    return _render(name, node_after, waiting_after, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


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
        title=str(row.get("name", "")), subtitle=str(row.get("kind", "")), facets={"kind": row.get("kind")}
    )
