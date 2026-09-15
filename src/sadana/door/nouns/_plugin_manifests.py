"""Every plugin directory's manifest, read the way this door's own surfaces
need it — never `plugin_manifest.discover_plugins()` (H24:
`docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`).

Not itself a noun — the one thing under `door/nouns/` besides
`__init__.py` that isn't, imported by `plugins.py`, `workflows.py` and
`nodes.py` (a noun module never imports another noun module: H19).

`discover_plugins()` validates at `check_bodies=True` and excludes a plugin
*entirely* the moment any one step still needs code — exactly the state
`needs_code`/`layout` exist to show. `editor_server.assess()`'s own
`check_bodies=False` posture is what this module reuses instead, found
during this work item's own test-writing when a fixture plugin with one
unresolvable body reference silently vanished from every listing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from sadana import editor_server, plugins
from sadana.door import problems


def synthetic_id(prefix: str, *parts: str) -> str:
    """A pure function of `parts`, for a resource with no row of its own —
    `workflow_id`/`node_id`/`tool_id` all mint theirs this way, over
    `(plugin directory name, their own name)`. The same documented
    exception `agent_template.template_id`'s own `"tmpl_" +
    sha256(name)[:32]` already established."""
    return prefix + "_" + hashlib.sha256(":".join(parts).encode()).hexdigest()[:32]


def refs_for(kind: object, body: object) -> tuple[str, ...]:
    """A `call` node's own `module:function`; a `compute` node's module
    alone; nothing for any other kind — the one branching rule, over the
    two primitive values every caller already has, whether they hold a
    live `plugins.Node` (`external_refs` below) or a JSON-shaped dict
    (`inspections.py`'s own declared-shape nodes, which never had a real
    `Node` to begin with — `inspect_tag()` discards the clone before this
    module could parse one)."""
    if kind == "call" and body:
        return (str(body),)
    if kind == "compute" and body:
        return (str(body).partition(":")[0],)
    return ()


def external_refs(node: plugins.Node) -> tuple[str, ...]:
    """Shared by `plugins.py`'s `layout` and `nodes.py`'s own render —
    identical in both, so kept once."""
    return refs_for(node.kind, node.body)


def needs_code_for(kind: object, body: object, skill: object) -> bool:
    """Whether a node's own declared fields even name what its kind
    requires — `plugins.KIND_USES`'s own vocabulary, read structurally
    rather than re-typed as a second, driftable table
    (`compute`/`call`/`route` need `body`, `ask` needs `skill`)."""
    uses = plugins.KIND_USES.get(str(kind), ())
    if "body" in uses:
        return not body
    if "skill" in uses:
        return not skill
    return False


def assessed_by_directory_name(plugins_root: Path) -> dict[str, tuple[plugins.Manifest, frozenset[str]]]:
    """Every plugin directory whose `plugin.toml` at least parses and holds
    at `check_bodies=False`, keyed by directory name — matching
    `plugin_state.name`'s own key, never a manifest's own declared name
    (`client_surface.enabled_plugin_set`'s own reasoning). The value is the
    parsed `Manifest` and the set of step names `editor_server.waiting()`
    reports as still needing something."""
    if not plugins_root.is_dir():
        return {}
    out: dict[str, tuple[plugins.Manifest, frozenset[str]]] = {}
    for child in sorted(plugins_root.iterdir()):
        if not child.is_dir() or not (child / "plugin.toml").is_file():
            continue
        assessed = editor_server.assess(child)
        if isinstance(assessed, str):
            continue
        manifest, _problems, waiting = assessed
        out[child.name] = (manifest, frozenset(w["step"] for w in waiting))
    return out


def validate_repo_url_and_tag(body: Mapping[str, object]) -> tuple[str, str] | problems.Problem:
    """The one `{repo_url, tag}` shape both `plugins.py`'s `create`/
    `install-from-git` and `inspections.py`'s `create` need — installing
    and inspecting take the identical two required strings."""
    repo_url = body.get("repo_url")
    tag = body.get("tag")
    errors = []
    if not isinstance(repo_url, str) or not repo_url:
        errors.append({"field": "repo_url", "code": "required"})
    if not isinstance(tag, str) or not tag:
        errors.append({"field": "tag", "code": "required"})
    if errors:
        return problems.make("VALIDATION", "invalid request", errors=tuple(errors))
    assert isinstance(repo_url, str) and isinstance(tag, str)
    return repo_url, tag
