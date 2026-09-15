"""The `provider` noun: the box's own model providers, tunable through the
door (H14, `docs/tasks/H14-tuned-config-settings-secrets/spec.md`).

One row per name in `model_access.list_providers()` with `request_fn is
not None` — "wired providers only" is exactly that check, already
computable from the existing registry; no second registry is built here.
A row materializes (`INSERT OR IGNORE`, inside `write_txn`) the first time
`list`/`get` is asked for it, because a provider's existence is a fact
about the box's own installed code, not something a person creates —
`create`/`remove` are both `unavailable` for the same reason. The minted
row's `created_at` is a floor, not a fact, the same posture
`console_fit_plan.md` §5(b) already documents for a legacy row filled in
at first open — this box simply had not been asked about this provider
before.

`model`/`base_url`/`credential_ref` live under `[providers.<name>]` in
`config.toml`, keyed by the provider's own name — never folded into the
existing flat `[model_access]` table, which stays the box's *active
selection* (`provider`, `model`, `timeout_s`, `max_retries`) and may name a
provider whose own `[providers.<name>]` table also exists. A second
registered provider gets its own table for free; nothing about this
noun's own code has to change for one to be added.

`update`'s `credential_ref` is validated against `secrets.exists` — a name,
never an id, matching `console_fit_plan.md` §5(a)'s names-not-pointers
posture and `docs/console/wire.md` §6's own statement of where a
credential's value lives.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from sadana import config, ids, ledger, model_access
from sadana.conversation_store import write_txn
from sadana.door import config_writer, grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, check_if_match, unavailable
from sadana.door.nouns import secrets as secrets_noun

spec = NounSpec(
    plural="providers",
    prefix="prv",
    filterable=frozenset({"name"}),
    orderable=frozenset({"created_at"}),
    states=frozenset(),
    actions={},
    parent=None,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _wired_manifests() -> dict[str, model_access.ProviderManifest]:
    return {m.name: m for m in model_access.list_providers() if m.request_fn is not None}


def _materialize(conn: sqlite3.Connection, *, now: float) -> None:
    with write_txn(conn) as c:
        for name in _wired_manifests():
            if c.execute("SELECT 1 FROM providers WHERE name = ?", (name,)).fetchone() is not None:
                continue
            new_id = ids.make_id("prv")
            c.execute(
                "INSERT INTO providers (id, name, created_at, updated_at, version) VALUES (?, ?, ?, ?, 1)",
                (new_id, name, now, now),
            )
            ledger.record_change(c, noun="providers", id=new_id, kind="created", state=None, version=1, at=now)


def _render(row: sqlite3.Row, manifest: model_access.ProviderManifest, *, harness_id: str) -> dict[str, object]:
    name = str(row["name"])
    default_credential_ref = manifest.env_vars[0] if manifest.env_vars else ""
    return {
        "id": row["id"],
        "created_at": grammar.render_ts(row["created_at"]),
        "updated_at": grammar.render_ts(row["updated_at"]),
        "tags": {},
        "harness_id": harness_id,
        "version": row["version"],
        "name": name,
        "model": config.get(f"providers.{name}.model", model_access.DEFAULT_MODEL),
        "base_url": config.get(f"providers.{name}.base_url", manifest.base_url),
        "credential_ref": config.get(f"providers.{name}.credential_ref", default_credential_ref),
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    ensure_schema(ctx.conns.reader())  # type: ignore[attr-defined]
    now = ctx.clock()  # type: ignore[attr-defined]
    _materialize(ctx.conns.writer, now=now)  # type: ignore[attr-defined]
    manifests = _wired_manifests()
    rows = [
        _render(r, manifests[str(r["name"])], harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]
        for r in ctx.conns.reader().execute("SELECT * FROM providers")  # type: ignore[attr-defined]
        if str(r["name"]) in manifests
    ]
    return grammar.page(rows, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    ensure_schema(ctx.conns.reader())  # type: ignore[attr-defined]
    now = ctx.clock()  # type: ignore[attr-defined]
    _materialize(ctx.conns.writer, now=now)  # type: ignore[attr-defined]
    row = ctx.conns.reader().execute("SELECT * FROM providers WHERE id = ?", (id,)).fetchone()  # type: ignore[attr-defined]
    manifests = _wired_manifests()
    if row is None or str(row["name"]) not in manifests:
        return problems.make("NOT_FOUND", f"no provider {id}")
    return _render(row, manifests[str(row["name"])], harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable(spec.plural, "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> dict[str, object] | problems.Problem:
    # router.py only auto-gates *actions* by capability, never the generic
    # verbs (schedules.py's own `_require_write` comment) — this noun has
    # no actions, so `update` must check `settings.write` itself.
    if "settings.write" not in ctx.capabilities:  # type: ignore[attr-defined]
        return problems.make("HARNESS_CAPABILITY_MISSING", "providers.update requires capability settings.write")
    now = ctx.clock()  # type: ignore[attr-defined]
    _materialize(ctx.conns.writer, now=now)  # type: ignore[attr-defined]
    manifests = _wired_manifests()
    with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
        row = c.execute("SELECT * FROM providers WHERE id = ?", (id,)).fetchone()
        if row is None or str(row["name"]) not in manifests:
            return problems.make("NOT_FOUND", f"no provider {id}")
        precondition = check_if_match(row, if_match)
        if precondition is not None:
            return precondition
        name = str(row["name"])
        if "name" in body and body["name"] != name:
            return problems.make("VALIDATION", "a provider's name cannot be changed")
        credential_ref = body.get("credential_ref")
        if credential_ref is not None:
            if not isinstance(credential_ref, str) or not credential_ref:
                return problems.make("VALIDATION", "credential_ref must be a non-empty string")
            if not secrets_noun.exists(credential_ref):
                return problems.make(
                    "VALIDATION", f"no secret named {credential_ref!r}; POST /v1/secrets to create it first"
                )
        model = body.get("model")
        if model is not None and (not isinstance(model, str) or not model):
            return problems.make("VALIDATION", "model must be a non-empty string")
        base_url = body.get("base_url")
        if base_url is not None and (not isinstance(base_url, str) or not base_url):
            return problems.make("VALIDATION", "base_url must be a non-empty string")

        path = config.get_paths().config_dir / "config.toml"
        data = config.raw_toml()
        if model is not None:
            data = config_writer.apply(data, f"providers.{name}.model", model)
        if base_url is not None:
            data = config_writer.apply(data, f"providers.{name}.base_url", base_url)
        if credential_ref is not None:
            data = config_writer.apply(data, f"providers.{name}.credential_ref", credential_ref)
        config_writer.write(path, data)

        new_version = row["version"] + 1
        c.execute("UPDATE providers SET updated_at = ?, version = ? WHERE id = ?", (now, new_version, id))
        ledger.record_change(c, noun="providers", id=id, kind="changed", state=None, version=new_version, at=now)
    return get(ctx, principal, id)


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable(spec.plural, "remove")


def act(
    ctx: object, principal: Principal, id: str, name: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable(spec.plural, name)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title=str(row.get("name", "")), facets={"model": row.get("model")})
