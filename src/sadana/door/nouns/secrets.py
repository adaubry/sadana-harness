"""The `secret` noun: write-only, referenced by name (H14,
`docs/tasks/H14-tuned-config-settings-secrets/spec.md`).

Rows are minted through the door's own standard grammar — `POST /v1/secrets`
creates (a real ``sec_<uuid7>`` id), `PATCH /v1/secrets/{id}` rotates the
value and/or ``kind`` — not the original brief's own `PUT /v1/secrets/{name}`
sketch, which has no counterpart in `wire.md` §1's actual six-verb,
id-addressed grammar (closed by H19) and would have reopened that shared
framework for one noun. `name` is what a `credential_ref`/`secret_ref`
anywhere else in this API actually names
(`docs/reference/console_fit_plan.md` §5(a): names, not pointers) — set
once at creation and immutable after, because unlike most of this
project's other names, something else is allowed to hold this one by
reference.

The value itself never enters this table and never leaves the door on any
verb, on any state: `PATCH` accepts it, `GET`/`LIST` never render it, only
a `fingerprint` (`env_file.fingerprint`) computed live from
`state_dir/.env` on every read — a value changed by hand outside the door
is reflected immediately, with no cached fingerprint that could go stale
against it.

`remove` hard-deletes the row and drops the `.env` line
(`env_file.drop_key`) — the tombstone this project's own removal pattern
leaves is the ledger's own permanent `deleted` row
(`memory_store.delete_entry`'s identical shape), not a lingering `state`
kept on the noun's own table.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping

from sadana import config, env_file, ids, ledger
from sadana.conversation_store import write_txn
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, check_if_match, unavailable

spec = NounSpec(
    plural="secrets",
    prefix="sec",
    filterable=frozenset({"name"}),
    orderable=frozenset({"created_at"}),
    states=frozenset(),
    actions={},
    parent=None,
)

#: A `name` becomes a bare, unquoted key on its own physical line of
#: `state_dir/.env` (`env_file.upsert_key`) — allowlisted before it ever
#: touches that file, the same posture CLAUDE.md requires of any
#: caller-supplied name that becomes a filesystem/config-adjacent value.
#: Without this, a `name` containing "\\n" splits into two physical lines
#: and injects an arbitrary second `.env` assignment the caller never
#: named as a secret.
_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def exists(name: str) -> bool:
    """Whether ``name`` currently resolves to a value through
    ``config.secret`` — a real environment variable counts exactly as much
    as one `PUT` through this noun, since "the environment always wins"
    applies to this check too: a `credential_ref` naming an already
    shell-exported variable is never rejected for not having been
    registered here first."""
    return config.secret(name) is not None


def _render(row: sqlite3.Row, *, harness_id: str) -> dict[str, object]:
    value = env_file.read_key(str(row["name"]))
    return {
        "id": row["id"],
        "created_at": grammar.render_ts(row["created_at"]),
        "updated_at": grammar.render_ts(row["updated_at"]),
        "tags": {},
        "harness_id": harness_id,
        "version": row["version"],
        "name": row["name"],
        "kind": row["kind"],
        "fingerprint": env_file.fingerprint(value) if value is not None else None,
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    ensure_schema(ctx.conns.reader())  # type: ignore[attr-defined]
    rows = [
        _render(r, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]
        for r in ctx.conns.reader().execute("SELECT * FROM secrets")  # type: ignore[attr-defined]
    ]
    return grammar.page(rows, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    ensure_schema(ctx.conns.reader())  # type: ignore[attr-defined]
    row = ctx.conns.reader().execute("SELECT * FROM secrets WHERE id = ?", (id,)).fetchone()  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no secret {id}")
    return _render(row, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    if "secrets.write" not in ctx.capabilities:  # type: ignore[attr-defined]
        return problems.make("HARNESS_CAPABILITY_MISSING", "secrets.create requires capability secrets.write")
    name = body.get("name")
    if not isinstance(name, str) or not _VALID_NAME.match(name):
        return problems.make("VALIDATION", "name must match ^[A-Za-z_][A-Za-z0-9_]*$")
    value = body.get("value")
    if not isinstance(value, str) or not value:
        return problems.make("VALIDATION", "value is required")
    kind = body.get("kind", "generic")
    if not isinstance(kind, str) or not kind:
        return problems.make("VALIDATION", "kind must be a non-empty string")

    now = ctx.clock()  # type: ignore[attr-defined]
    secret_id = ids.make_id("sec")
    with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
        if c.execute("SELECT 1 FROM secrets WHERE name = ?", (name,)).fetchone() is not None:
            return problems.make("VALIDATION", f"a secret named {name!r} already exists")
        c.execute(
            "INSERT INTO secrets (id, name, kind, created_at, updated_at, version) VALUES (?, ?, ?, ?, ?, 1)",
            (secret_id, name, kind, now, now),
        )
        env_file.upsert_key(env_file.env_path(), name, value)
        ledger.record_change(c, noun="secrets", id=secret_id, kind="created", state=None, version=1, at=now)
    return get(ctx, principal, secret_id)


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> dict[str, object] | problems.Problem:
    if "secrets.write" not in ctx.capabilities:  # type: ignore[attr-defined]
        return problems.make("HARNESS_CAPABILITY_MISSING", "secrets.update requires capability secrets.write")
    with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
        row = c.execute("SELECT * FROM secrets WHERE id = ?", (id,)).fetchone()
        if row is None:
            return problems.make("NOT_FOUND", f"no secret {id}")
        precondition = check_if_match(row, if_match)
        if precondition is not None:
            return precondition
        if "name" in body and body["name"] != row["name"]:
            return problems.make("VALIDATION", "a secret's name cannot be changed after creation")
        kind = body.get("kind", row["kind"])
        if not isinstance(kind, str) or not kind:
            return problems.make("VALIDATION", "kind must be a non-empty string")
        value = body.get("value")
        if value is not None:
            if not isinstance(value, str) or not value:
                return problems.make("VALIDATION", "value must be a non-empty string")
            env_file.upsert_key(env_file.env_path(), str(row["name"]), value)
        now = ctx.clock()  # type: ignore[attr-defined]
        new_version = row["version"] + 1
        c.execute("UPDATE secrets SET kind = ?, updated_at = ?, version = ? WHERE id = ?", (kind, now, new_version, id))
        ledger.record_change(c, noun="secrets", id=id, kind="changed", state=None, version=new_version, at=now)
    return get(ctx, principal, id)


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> None | problems.Problem:
    if "secrets.write" not in ctx.capabilities:  # type: ignore[attr-defined]
        return problems.make("HARNESS_CAPABILITY_MISSING", "secrets.remove requires capability secrets.write")
    with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
        row = c.execute("SELECT * FROM secrets WHERE id = ?", (id,)).fetchone()
        if row is None:
            return problems.make("NOT_FOUND", f"no secret {id}")
        precondition = check_if_match(row, if_match)
        if precondition is not None:
            return precondition
        now = ctx.clock()  # type: ignore[attr-defined]
        c.execute("DELETE FROM secrets WHERE id = ?", (id,))
        env_file.drop_key(env_file.env_path(), str(row["name"]))
        ledger.record_change(c, noun="secrets", id=id, kind="deleted", state=None, version=None, at=now)
    return None


def act(
    ctx: object, principal: Principal, id: str, name: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable(spec.plural, name)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title=str(row.get("name", "")), facets={"kind": row.get("kind")})
