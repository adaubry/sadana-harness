"""The `inspection` noun: how the console's own plugin registry asks a
harness to look at a git tag before anyone installs it (H24,
`docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`).

Owns its own table — the first `door/nouns/*.py` module to do so — because
what it stores (a checked tag's declared shape, at the moment it was
checked) has no other home: the tag itself may never be installed, so
nothing in `plugin_state` or on disk can answer "what did this tag look
like" later. `create` runs `marketplace.inspect_tag()` (extracted from
`marketplace.submit()` for exactly this reuse); a slow clone promotes to an
`Operation` automatically, through `router.run_bounded` — this module knows
nothing about that machinery.

Never executes anything from the inspected tag, on any path —
`marketplace.inspect_tag()`'s own `check_bodies=False` clone-then-discard is
the whole safety property (CLAUDE.md's rule on a check that could execute
code as a side effect of validating it).

`declared_shape.nodes[].needs_code` is computed here, structurally, from the
manifest alone — never by reopening the clone `inspect_tag()` already
discarded. It answers a narrower question than the `plugin` noun's own
`needs_code` (which checks a real installed file for a real function): here
it is "does this node's declared fields even name what its kind requires" —
`compute`/`call`/`route` need a `body`, `ask` needs a `skill`. A body or
skill that is *named* but does not actually resolve to real code is not
caught by an inspection; it is caught the moment the tag is actually
installed and the plugin noun's own `layout` is read.

`declared_shape.permissions_requested` is always `[]` — nothing in this
harness's manifest model (`plugins.Manifest`) captures a requested
permission yet. Rendered as the wire shape's own promised field rather than
omitted, so a console reading this contract never has to special-case its
absence; populated the day a work item actually adds the concept to
`plugins.py`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from typing import cast

from sadana import ids, ledger, marketplace, plugin_install
from sadana.conversation_store import write_txn
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, unavailable
from sadana.door.nouns._plugin_manifests import needs_code_for, refs_for, validate_repo_url_and_tag

CAPABILITIES: tuple[str, ...] = ("plugins.inspect",)

spec = NounSpec(
    plural="inspections",
    prefix="insp",
    filterable=frozenset({"state", "repo_url", "tag", "created_at"}),
    orderable=frozenset({"created_at"}),
    states=frozenset({"ok", "failed"}),
    actions={},
    parent=None,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inspections (
    id                   TEXT PRIMARY KEY,
    repo_url             TEXT NOT NULL,
    tag                  TEXT NOT NULL,
    revision             TEXT,
    declared_shape_json  TEXT,
    checksum             TEXT,
    state                TEXT NOT NULL,
    error_json           TEXT,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    version              INTEGER NOT NULL DEFAULT 1
);
"""

#: Rows older than this are swept away on the first write of each new day —
#: the console's own registry contract (spec.md). Same shape as
#: `door/idempotency.py`'s `_sweep_if_new_hour`, one grain coarser.
_RETENTION_SECONDS = 7 * 24 * 60 * 60

_sweep_lock = threading.Lock()
_last_swept_day: int | None = None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _sweep_if_new_day(conn: sqlite3.Connection, *, now: float) -> None:
    global _last_swept_day
    day = int(now // 86400)
    with _sweep_lock:
        if _last_swept_day == day:
            return
        _last_swept_day = day
    conn.execute("DELETE FROM inspections WHERE created_at < ?", (now - _RETENTION_SECONDS,))


def _declared_shape(manifest: Mapping[str, object]) -> dict[str, object]:
    """`manifest` (`plugins.manifest_to_dict()`'s own shape) reworked into
    the console's inspection contract: nodes keyed by their own `id` (their
    manifest `name` — nothing else identifies a node before it is ever
    installed), each carrying `needs_code`/`external_refs` instead of the
    raw `body`/`skill`/`next`/`ports` a plugin's own file format uses."""
    nodes_raw = cast("Sequence[dict[str, object]]", manifest.get("nodes", []))
    return {
        "name": manifest.get("name"),
        "version": manifest.get("version"),
        "description": manifest.get("description"),
        "entries": manifest.get("entries", []),
        "nodes": [
            {
                "id": node.get("name"),
                "kind": node.get("kind"),
                "needs_code": needs_code_for(node.get("kind"), node.get("body"), node.get("skill")),
                "external_refs": refs_for(node.get("kind"), node.get("body")),
            }
            for node in nodes_raw
        ],
        "settings": manifest.get("settings", []),
        "permissions_requested": [],
    }


def _checksum(declared_shape: Mapping[str, object]) -> str:
    canonical = json.dumps(declared_shape, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "repo_url": row["repo_url"],
        "tag": row["tag"],
        "revision": row["revision"],
        "declared_shape_json": row["declared_shape_json"],
        "checksum": row["checksum"],
        "state": row["state"],
        "error_json": row["error_json"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "version": row["version"],
    }


def _render(row: Mapping[str, object], *, harness_id: str) -> dict[str, object]:
    return {
        "id": row["id"],
        "created_at": grammar.render_ts(row["created_at"]),  # type: ignore[arg-type]
        "updated_at": grammar.render_ts(row["updated_at"]),  # type: ignore[arg-type]
        "state": row["state"],
        "tags": {},
        "harness_id": harness_id,
        "version": row["version"],
        "repo_url": row["repo_url"],
        "tag": row["tag"],
        "revision": row["revision"],
        "declared_shape": json.loads(str(row["declared_shape_json"])) if row["declared_shape_json"] else None,
        "checksum": row["checksum"],
        "error": json.loads(str(row["error_json"]))["detail"] if row["error_json"] else None,
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    ensure_schema(ctx.conns.reader())  # type: ignore[attr-defined]
    rows = [
        _render(_row_to_dict(r), harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]
        for r in ctx.conns.reader().execute("SELECT * FROM inspections ORDER BY created_at DESC")  # type: ignore[attr-defined]
    ]
    return grammar.page(rows, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    ensure_schema(ctx.conns.reader())  # type: ignore[attr-defined]
    row = ctx.conns.reader().execute("SELECT * FROM inspections WHERE id = ?", (id,)).fetchone()  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no inspection {id}")
    return _render(_row_to_dict(row), harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    if "plugins.inspect" not in ctx.capabilities:  # type: ignore[attr-defined]
        return problems.make("HARNESS_CAPABILITY_MISSING", "inspections.create requires capability plugins.inspect")

    validated = validate_repo_url_and_tag(body)
    if isinstance(validated, problems.Problem):
        return validated
    repo_url, tag = validated

    outcome = marketplace.inspect_tag(repo_url, tag)
    now = ctx.clock()  # type: ignore[attr-defined]

    declared_shape: dict[str, object] | None
    revision: str | None
    error: str | None
    if isinstance(outcome, marketplace.Inspected):
        declared_shape = _declared_shape(outcome.manifest)
        state, revision, error = "ok", outcome.revision, None
    elif isinstance(outcome, marketplace.InvalidManifest):
        declared_shape = _declared_shape(outcome.manifest) if outcome.manifest is not None else None
        state, revision, error = "failed", outcome.revision, outcome.detail
    else:  # plugin_install.TagMismatch | plugin_install.FetchFailed
        declared_shape, revision = None, None
        state, error = "failed", plugin_install.describe_fetch_failure(outcome)

    ensure_schema(ctx.conns.writer)  # type: ignore[attr-defined]
    insp_id = ids.make_id("insp")
    with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
        _sweep_if_new_day(c, now=now)
        c.execute(
            "INSERT INTO inspections "
            "(id, repo_url, tag, revision, declared_shape_json, checksum, state, error_json, "
            "created_at, updated_at, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                insp_id,
                repo_url,
                tag,
                revision,
                json.dumps(declared_shape) if declared_shape is not None else None,
                _checksum(declared_shape) if declared_shape is not None else None,
                state,
                json.dumps({"detail": error}) if error is not None else None,
                now,
                now,
            ),
        )
        ledger.record_change(c, noun="inspections", id=insp_id, kind="created", state=state, version=1, at=now)

    row = ctx.conns.reader().execute("SELECT * FROM inspections WHERE id = ?", (insp_id,)).fetchone()  # type: ignore[attr-defined]
    assert row is not None, f"inspection {insp_id} vanished immediately after its own create"
    return _render(_row_to_dict(row), harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


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
        title=f"{row.get('repo_url', '')}@{row.get('tag', '')}",
        subtitle=str(row.get("state", "")),
        facets={"state": row.get("state"), "repo_url": row.get("repo_url")},
    )
