"""The `plugin` noun: what a plugin is, its graph laid out by this harness,
installing one from a git tag, saving a canvas edit back through this
harness's own validation, and enabling/disabling one (H24,
`docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`).

Rows are enumerated from `plugin_state` — the authoritative existence/id/
state/source list, including a plugin that doesn't validate — left-joined
against `editor_server.assess()`'s own `check_bodies=False` read of each
directory for every manifest-derived field, **not**
`plugin_manifest.discover_plugins()`. Found while writing this noun's own
tests: `discover_plugins()` uses `check_bodies=True` and excludes a plugin
*entirely* the moment any one step still needs code — exactly the state
`needs_code`/`layout` exist to show, and the same reason
`editor_server._plugin_payload` never used `discover_plugins()` for its own
surface either. A plugin whose `plugin.toml` doesn't even parse renders
those fields empty/zero rather than 404 — still visible, nothing valid to
say about its graph.

Layout is computed fresh on every `get`, never stored (spec.md guideline
4): `editor_layout.positions()` already proved this is cheap and
deterministic, and storing it would be a second thing that can go stale
against the manifest file `editor_server._write_manifest`'s read-back guard
already protects as the one source of truth.

Nothing on this noun's own path ever executes a plugin's code:
`editor_server.assess()`/`waiting()` run at `check_bodies=False`, and
`plugin_install.install_from_git()` never imports what it fetches.
`editor_server._write_manifest` is imported and called directly — the same
cross-module reach into a sibling block's own underscore-prefixed function
`plugin_manifest.py` already takes into `plugins.py` (`_parse_manifest`,
`_node_index`, `_successors`, ...); promoting a fourth `editor_server.py`
name beyond the three this work item's own promotion already made would be
a second edit to that file, which spec.md scoped to exactly one.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from sadana import config, editor_layout, editor_server, plugin_install, plugins
from sadana.door import config_writer, grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc, check_if_match, unavailable
from sadana.door.nouns import secrets as secrets_noun
from sadana.door.nouns._plugin_manifests import assessed_by_directory_name, external_refs, validate_repo_url_and_tag

CAPABILITIES: tuple[str, ...] = ("plugins.install", "plugins.write")

spec = NounSpec(
    plural="plugins",
    prefix="plg",
    filterable=frozenset({"state", "name", "source_repo"}),
    orderable=frozenset({"created_at"}),
    states=frozenset(plugin_install.PLUGIN_STATES),
    actions={
        "disable": ActionSpec(
            from_states=("installed",), to_state="disabled", capability="plugins.write", scope_verb="disable"
        ),
        "enable": ActionSpec(
            from_states=("disabled",), to_state="installed", capability="plugins.write", scope_verb="enable"
        ),
        # `to_state=""`: neither action has one fixed target state —
        # `conversations.py`'s own rename action already takes this shape
        # for the same reason (`ActionSpec.to_state` is documentation only;
        # nothing in `router.py` reads it).
        "install-from-git": ActionSpec(
            from_states=(), to_state="", capability="plugins.install", scope_verb="install-from-git"
        ),
        "save": ActionSpec(from_states=(), to_state="", capability="plugins.write", scope_verb="save"),
        "set-settings": ActionSpec(from_states=(), to_state="", capability="settings.write", scope_verb="set-settings"),
    },
    parent=None,
)

#: Fields `manifest_from_dict`'s/`_write_manifest`'s own `ValueError` text
#: names literally, right after `node '<name>': '<field>'` — used to decide
#: whether a message can be turned into a field-level error (§ Interface,
#: spec.md).
_KNOWN_NODE_FIELDS = frozenset({"name", "kind", "body", "skill", "next", "ports"})
_STEP_ERROR_RE = re.compile(r"^(?:node|step) '([^']+)': '([^']+)'")


def _plugins_root() -> Path:
    return plugins._plugins_root()


def _manifest_summary(manifest: plugins.Manifest, waiting_steps: frozenset[str]) -> dict[str, object]:
    """The fields every render (list and get) needs from a manifest that at
    least parses — empty/zero when the caller has none to hand in."""
    return {
        "description": manifest.description,
        "declared_settings": [
            {"key": s.name, "type": "secret" if s.secret else "string", "required": True, "description": s.purpose}
            for s in manifest.settings
        ],
        "steps_count": len(manifest.nodes),
        "needs_code_count": len(waiting_steps),
    }


_EMPTY_MANIFEST_SUMMARY: dict[str, object] = {
    "description": "",
    "declared_settings": [],
    "steps_count": 0,
    "needs_code_count": 0,
}


def _layout(manifest: plugins.Manifest, waiting_steps: frozenset[str]) -> dict[str, object]:
    positions = editor_layout.positions(manifest)
    nodes = [
        {
            "id": node.name,
            "kind": node.kind,
            "x": positions[node.name][0],
            "y": positions[node.name][1],
            "w": 160,
            "h": 56,
            "label": node.name,
            "needs_code": node.name in waiting_steps,
            "external_refs": external_refs(node),
        }
        for node in manifest.nodes
    ]
    edges = [{"from": node.name, "to": target} for node in manifest.nodes for target in plugins._successors(node)]
    return {"nodes": nodes, "edges": edges}


def _render(
    row: Mapping[str, object],
    assessed: tuple[plugins.Manifest, frozenset[str]] | None,
    *,
    harness_id: str,
    with_layout: bool,
) -> dict[str, object]:
    summary = _manifest_summary(assessed[0], assessed[1]) if assessed is not None else _EMPTY_MANIFEST_SUMMARY
    out: dict[str, object] = {
        "id": row["id"],
        "created_at": grammar.render_ts(row["installed_at"]),  # type: ignore[arg-type]
        "updated_at": grammar.render_ts(row["updated_at"]),  # type: ignore[arg-type]
        "state": row["state"],
        "tags": {},
        "harness_id": harness_id,
        "version": row["version"],
        "name": row["name"],
        "source_repo": row["source_repo"],
        "source_tag": row["source_tag"],
        "description": summary["description"],
        "declared_settings": summary["declared_settings"],
        "steps_count": summary["steps_count"],
        "needs_code_count": summary["needs_code_count"],
    }
    if with_layout:
        out["layout"] = _layout(assessed[0], assessed[1]) if assessed is not None else {"nodes": [], "edges": []}
    return out


def _render_row(ctx: object, row: sqlite3.Row, *, with_layout: bool) -> dict[str, object]:
    """`_render`, but for exactly one already-fetched `plugin_state` row —
    the one-plugin lookup `get`/`_install_outcome_to_response`/`_save`/`act`
    each need, kept in one place rather than four."""
    assessed = assessed_by_directory_name(_plugins_root()).get(str(row["name"]))
    return _render(dict(row), assessed, harness_id=ctx.runtime.harness_id, with_layout=with_layout)  # type: ignore[attr-defined]


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    assessed_by_name = assessed_by_directory_name(_plugins_root())
    rows = reader.execute("SELECT * FROM plugin_state ORDER BY name").fetchall()
    rendered = [
        _render(dict(r), assessed_by_name.get(r["name"]), harness_id=ctx.runtime.harness_id, with_layout=False)  # type: ignore[attr-defined]
        for r in rows
    ]
    return grammar.page(rendered, params)


def _get_row(ctx: object, id: str) -> sqlite3.Row | None:
    return ctx.conns.reader().execute("SELECT * FROM plugin_state WHERE id = ?", (id,)).fetchone()  # type: ignore[attr-defined]


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    row = _get_row(ctx, id)
    if row is None:
        return problems.make("NOT_FOUND", f"no plugin {id}")
    return _render_row(ctx, row, with_layout=True)


def _install_outcome_to_response(
    ctx: object, outcome: plugin_install.InstallOutcome
) -> dict[str, object] | problems.Problem:
    if isinstance(outcome, plugin_install.Installed):
        reader = ctx.conns.reader()  # type: ignore[attr-defined]
        row = reader.execute("SELECT * FROM plugin_state WHERE name = ?", (outcome.name,)).fetchone()
        assert row is not None, f"plugin {outcome.name} vanished immediately after its own install"
        return _render_row(ctx, row, with_layout=False)
    if isinstance(outcome, plugin_install.AlreadyInstalled):
        return problems.make("CONFLICT", f"a plugin named {outcome.name!r} already exists")
    if isinstance(outcome, plugin_install.NameMismatch):
        return problems.make(
            "VALIDATION", f"the tag declares plugin {outcome.found!r}, not the expected {outcome.expected!r}"
        )
    if isinstance(outcome, plugin_install.InvalidName):
        return problems.make("VALIDATION", f"plugin name {outcome.name!r} is not a safe plugin name")
    if isinstance(outcome, plugin_install.UnknownPluginName):  # pragma: no cover - install_from_git never registers
        return problems.make("INTERNAL", "unexpected: install_from_git never consults the name registry")
    # TagMismatch | FetchFailed
    return problems.make("VALIDATION", plugin_install.describe_fetch_failure(outcome))


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    """`create` is `install-from-git`'s own alias (spec.md): a plugin being
    installed has no id yet, so there is no action route to reach it
    through. Manually capability-gated — `router.py` only auto-gates
    declared *actions*, never the generic verbs (the same posture
    `schedules.py`'s own `create` already takes)."""
    if "plugins.install" not in ctx.capabilities:  # type: ignore[attr-defined]
        return problems.make("HARNESS_CAPABILITY_MISSING", "plugins.create requires capability plugins.install")
    validated = validate_repo_url_and_tag(body)
    if isinstance(validated, problems.Problem):
        return validated
    repo_url, tag = validated
    outcome = plugin_install.install_from_git(ctx.conns.writer, repo_url, tag, plugins_root=_plugins_root())  # type: ignore[attr-defined]
    return _install_outcome_to_response(ctx, outcome)


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable(spec.plural, "update")


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable(spec.plural, "remove")


def _manifest_error_to_problem(detail: str) -> problems.Problem:
    """Best-effort field extraction from `manifest_from_dict`'s/
    `_write_manifest`'s own prose `ValueError` text — never a second
    validator (spec.md § Interface). A match names a step and a known field
    ('kind' failing its type check, e.g.); a value quoted where a field name
    would be (`"is not one of ..."`), an entry- or settings-level error, or
    the read-back guard's own sentence all fall back to `detail` whole —
    always correct, only sometimes less specific."""
    match = _STEP_ERROR_RE.match(detail)
    if match is not None and match.group(2) in _KNOWN_NODE_FIELDS:
        return problems.make(
            "VALIDATION", detail, errors=({"field": f"nodes.{match.group(1)}.{match.group(2)}", "code": "invalid"},)
        )
    return problems.make("VALIDATION", detail)


def _save(ctx: object, row: sqlite3.Row, body: Mapping[str, object]) -> dict[str, object] | problems.Problem:
    payload = body.get("manifest")
    if not isinstance(payload, dict):
        return problems.make("VALIDATION", "'manifest' is required and must be an object")
    try:
        manifest = plugins.manifest_from_dict(payload)
    except ValueError as exc:
        return _manifest_error_to_problem(str(exc))
    if manifest.name != row["name"]:
        return problems.make(
            "VALIDATION",
            f"this plugin's folder is {row['name']!r}; its name cannot be changed to {manifest.name!r}",
        )
    directory = _plugins_root() / str(row["name"])
    try:
        editor_server._write_manifest(directory, manifest)
    except ValueError as exc:
        return _manifest_error_to_problem(str(exc))
    updated = _get_row(ctx, row["id"])
    assert updated is not None, f"plugin {row['id']} vanished immediately after its own save"
    return _render_row(ctx, updated, with_layout=True)


def _set_settings(ctx: object, row: sqlite3.Row, body: Mapping[str, object]) -> dict[str, object] | problems.Problem:
    """``{settings: {key: value | {secret_ref: name}}}`` (H14,
    `docs/tasks/H14-tuned-config-settings-secrets/spec.md`).

    A secret-kind key accepts only ``{secret_ref: name}`` — a raw value
    there is ``400 VALIDATION`` naming the key, and the reference itself
    is validated against ``secrets.exists`` before anything is written. A
    non-secret key accepts a raw scalar and is refused a reference. Every
    key must already be declared in the plugin's own ``plugin.toml``; an
    unknown key is ``400 VALIDATION`` naming it.

    Writes: a secret-kind value stores the *reference* under
    ``plugins.<plugin>.<key>`` in ``config.toml`` — ``plugins.read_setting``
    follows it live, so rotating the referenced secret reaches this plugin
    on the next call with no second ``set-settings`` needed. A non-secret
    value writes the same dotted key with the value itself. All writes for
    one call are validated first, applied second — a body naming three
    settings where the third is invalid changes nothing, not the first two."""
    settings = body.get("settings")
    if not isinstance(settings, dict) or not settings:
        return problems.make("VALIDATION", "'settings' is required and must be a non-empty object")
    plugin_name = str(row["name"])
    assessed = assessed_by_directory_name(_plugins_root()).get(plugin_name)
    declared = {s.name: s for s in assessed[0].settings} if assessed is not None else {}

    for key, value in settings.items():
        if key not in declared:
            return problems.make("VALIDATION", f"{plugin_name} does not declare a setting named {key!r}")
        setting = declared[key]
        if setting.secret:
            if not isinstance(value, dict) or set(value) != {"secret_ref"}:
                return problems.make(
                    "VALIDATION", f"{key} is a secret setting; provide {{'secret_ref': <name>}}, not a raw value"
                )
            secret_ref = value["secret_ref"]
            if not isinstance(secret_ref, str) or not secret_ref:
                return problems.make("VALIDATION", f"{key}.secret_ref must be a non-empty string")
            if not secrets_noun.exists(secret_ref):
                return problems.make(
                    "VALIDATION", f"no secret named {secret_ref!r}; POST /v1/secrets to create it first"
                )
        elif isinstance(value, dict) or not isinstance(value, str | int | float | bool):
            return problems.make("VALIDATION", f"{key} is not a secret setting; provide a raw value")

    path = config.get_paths().config_dir / "config.toml"
    data = config.raw_toml()
    for key, value in settings.items():
        setting = declared[key]
        applied = value["secret_ref"] if setting.secret else value
        data = config_writer.apply(data, f"plugins.{plugin_name}.{key}", applied)
    config_writer.write(path, data)

    now = ctx.clock()  # type: ignore[attr-defined]
    plugin_install.set_state(ctx.conns.writer, str(row["id"]), str(row["state"]), now=now)  # type: ignore[attr-defined]
    updated = _get_row(ctx, str(row["id"]))
    assert updated is not None, f"plugin {row['id']} vanished immediately after its own set-settings"
    return _render_row(ctx, updated, with_layout=False)


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> dict[str, object] | problems.Problem:
    row = _get_row(ctx, id)
    if row is None:
        return problems.make("NOT_FOUND", f"no plugin {id}")
    if name not in spec.actions:
        return unavailable(spec.plural, name)
    precondition = check_if_match(row, if_match)  # type: ignore[arg-type]
    if precondition is not None:
        return precondition

    now = ctx.clock()  # type: ignore[attr-defined]
    if name == "disable":
        plugin_install.set_state(ctx.conns.writer, id, "disabled", now=now)  # type: ignore[attr-defined]
    elif name == "enable":
        plugin_install.set_state(ctx.conns.writer, id, "installed", now=now)  # type: ignore[attr-defined]
    elif name == "install-from-git":
        validated = validate_repo_url_and_tag(body)
        if isinstance(validated, problems.Problem):
            return validated
        repo_url, tag = validated
        outcome = plugin_install.install_from_git(
            ctx.conns.writer,  # type: ignore[attr-defined]
            repo_url,
            tag,
            plugins_root=_plugins_root(),
            expect_name=str(row["name"]),
            replace=True,
        )
        return _install_outcome_to_response(ctx, outcome)
    elif name == "save":
        return _save(ctx, row, body)
    elif name == "set-settings":
        return _set_settings(ctx, row, body)
    else:
        return unavailable(spec.plural, name)

    updated = _get_row(ctx, id)
    assert updated is not None, f"plugin {id} vanished immediately after its own {name}"
    return _render_row(ctx, updated, with_layout=False)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(
        title=str(row.get("name", "")),
        subtitle=str(row.get("description") or ""),
        facets={"state": row.get("state"), "source_repo": row.get("source_repo")},
    )
