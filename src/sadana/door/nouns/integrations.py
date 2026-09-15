"""The `integration` noun: the box's own inbound webhook, tunable through
the door (H14, `docs/tasks/H14-tuned-config-settings-secrets/spec.md`).

Box-wide, one row — the same synthetic-until-first-write shape `budgets.py`
and `memory_policies` (H26) already use for a value with no natural row of
its own.

`webhook_url`/`gateway_state` are computed at read time, never stored, so
neither can go stale against what is actually true: `webhook_url` is
`gateway.webhook_bind`/`.webhook_port` plus `channel_webhook.WEBHOOK_PATH`,
the box's own inbound address; `gateway_state` probes the same
non-blocking flock `gateway_daemon.run()` itself takes (`state_dir/
gateway.lock`) — held means running, acquirable means stopped — rather than
shelling out to `systemctl`, which would be slow, require the service to
be installed at all, and answer a different question (whether *that*
supervision layer is up, not whether a message can currently reach this
box).

`webhook_secret_ref` is validated against `secrets.exists` exactly like
`providers.py`'s own `credential_ref` — a name, never an id.
"""

from __future__ import annotations

import contextlib
import fcntl
import sqlite3
from collections.abc import Mapping

from sadana import channel_webhook, config, ids, ledger
from sadana.conversation_store import write_txn
from sadana.door import config_writer, grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, check_if_match, unavailable
from sadana.door.nouns import secrets as secrets_noun

spec = NounSpec(
    plural="integrations",
    prefix="intg",
    filterable=frozenset(),
    orderable=frozenset({"created_at"}),
    states=frozenset(),
    actions={},
    parent=None,
)

_DEFAULT_ID = "intg_default"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS integrations (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _gateway_state() -> str:
    lock_path = config.get_paths().state_dir / "gateway.lock"
    try:
        with open(lock_path, "a+") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return "running"
            with contextlib.suppress(OSError):
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return "stopped"
    except OSError:
        return "stopped"


def _current(ctx: object) -> dict[str, object]:
    ensure_schema(ctx.conns.reader())  # type: ignore[attr-defined]
    row = ctx.conns.reader().execute("SELECT * FROM integrations").fetchone()  # type: ignore[attr-defined]
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    if row is None:
        now = grammar.render_ts(ctx.clock())  # type: ignore[attr-defined]
        id_, created_at, updated_at, version = _DEFAULT_ID, now, now, 0
    else:
        id_ = row["id"]
        created_at = grammar.render_ts(row["created_at"])
        updated_at = grammar.render_ts(row["updated_at"])
        version = row["version"]
    host = config.get("gateway.webhook_bind", "127.0.0.1", env_name="SADANA_GATEWAY_HOST")
    port = config.get("gateway.webhook_port", 8765, env_name="SADANA_GATEWAY_PORT")
    return {
        "id": id_,
        "created_at": created_at,
        "updated_at": updated_at,
        "tags": {},
        "harness_id": harness_id,
        "version": version,
        "webhook_url": f"http://{host}:{port}{channel_webhook.WEBHOOK_PATH}",
        "webhook_secret_ref": config.get("gateway.webhook_secret_ref", "SADANA_GATEWAY_WEBHOOK_SECRET"),
        "gateway_state": _gateway_state(),
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    return grammar.page([_current(ctx)], params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    current = _current(ctx)
    if current["id"] != id:
        return problems.make("NOT_FOUND", f"no integration {id}")
    return current


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
        return problems.make("HARNESS_CAPABILITY_MISSING", "integrations.update requires capability settings.write")
    current = _current(ctx)
    if current["id"] != id:
        return problems.make("NOT_FOUND", f"no integration {id}")
    precondition = check_if_match(current, if_match)
    if precondition is not None:
        return precondition
    for read_only in ("webhook_url", "gateway_state"):
        if read_only in body:
            return problems.make("VALIDATION", f"{read_only} is read-only")

    webhook_secret_ref = body.get("webhook_secret_ref")
    if webhook_secret_ref is not None:
        if not isinstance(webhook_secret_ref, str) or not webhook_secret_ref:
            return problems.make("VALIDATION", "webhook_secret_ref must be a non-empty string")
        if not secrets_noun.exists(webhook_secret_ref):
            return problems.make(
                "VALIDATION", f"no secret named {webhook_secret_ref!r}; POST /v1/secrets to create it first"
            )
    webhook_bind = body.get("webhook_bind")
    if webhook_bind is not None and (not isinstance(webhook_bind, str) or not webhook_bind):
        return problems.make("VALIDATION", "webhook_bind must be a non-empty string")
    webhook_port = body.get("webhook_port")
    if webhook_port is not None and (isinstance(webhook_port, bool) or not isinstance(webhook_port, int)):
        return problems.make("VALIDATION", "webhook_port must be an integer")
    if webhook_secret_ref is None and webhook_bind is None and webhook_port is None:
        return current

    path = config.get_paths().config_dir / "config.toml"
    data = config.raw_toml()
    if webhook_secret_ref is not None:
        data = config_writer.apply(data, "gateway.webhook_secret_ref", webhook_secret_ref)
    if webhook_bind is not None:
        data = config_writer.apply(data, "gateway.webhook_bind", webhook_bind)
    if webhook_port is not None:
        data = config_writer.apply(data, "gateway.webhook_port", webhook_port)
    config_writer.write(path, data)

    now = ctx.clock()  # type: ignore[attr-defined]
    with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
        row = c.execute("SELECT * FROM integrations").fetchone()
        if row is None:
            new_id = ids.make_id("intg")
            c.execute(
                "INSERT INTO integrations (id, created_at, updated_at, version) VALUES (?, ?, ?, 1)",
                (new_id, now, now),
            )
            ledger.record_change(c, noun="integrations", id=new_id, kind="created", state=None, version=1, at=now)
        else:
            new_version = row["version"] + 1
            c.execute("UPDATE integrations SET updated_at = ?, version = ? WHERE id = ?", (now, new_version, row["id"]))
            ledger.record_change(
                c, noun="integrations", id=row["id"], kind="changed", state=None, version=new_version, at=now
            )
    return _current(ctx)


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable(spec.plural, "remove")


def act(
    ctx: object, principal: Principal, id: str, name: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable(spec.plural, name)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title="Integration", facets={"gateway_state": row.get("gateway_state")})
