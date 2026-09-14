"""The `conversations` noun: a person's list of conversations, opened,
renamed, archived.

`docs/tasks/H20-door-nouns-turn-side/spec.md` requirements 1-7. `create()`
is the one verb that needs a `client_surface.Runtime` (to call
`client_surface.open_conversation`) — `DoorContext` (H19) carries no such
field, and adding one would be a `router.py` dataclass change reaching every
noun, this lane's and the other lane's, for a need exactly two nouns
(`conversations`, `messages`) have. Instead this module is a small class,
constructed once with a `Runtime` — the exact pattern H19's own
`tests/contract/fixture_noun.py:WidgetsNoun` already uses for per-instance
state, not a new mechanism. `subcommands/door.py` builds the one real
`Runtime` at process start and constructs `ConversationsNoun(runtime)` once;
every other verb here only ever touches `ctx`.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping

from sadana import client_surface, ids, ledger
from sadana.conversation_store import write_txn
from sadana.door import grammar, problems
from sadana.door.auth import Principal, account_key_for
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc, check_if_match, unavailable

CAPABILITIES: tuple[str, ...] = ()

spec = NounSpec(
    plural="conversations",
    prefix="conv",
    filterable=frozenset({"state", "agent_id", "created_at", "updated_at"}),
    orderable=frozenset({"created_at", "updated_at", "name"}),
    states=frozenset({"active", "archived"}),
    actions={
        "archive": ActionSpec(from_states=("active",), to_state="archived", capability=None, scope_verb="archive"),
        "unarchive": ActionSpec(from_states=("archived",), to_state="active", capability=None, scope_verb="unarchive"),
        # `to_state=""`: a rename never changes `state` — `ActionSpec.to_state`
        # is read by nothing in `router.py` (checked directly; only
        # `from_states` gates), so this is documentation, not enforcement,
        # matching `harness.upgrade`'s own use of the same sentinel for an
        # action with no real target state.
        "rename": ActionSpec(from_states=("active", "archived"), to_state="", capability=None, scope_verb="rename"),
    },
)


def _reader(ctx: object) -> sqlite3.Connection:
    return ctx.conns.reader()  # type: ignore[attr-defined]


def _row(conn: sqlite3.Connection, account_key: str, id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT c.* FROM conversations c JOIN conversation_accounts ca ON ca.conversation_key = c.key "
        "WHERE ca.account_key = ? AND c.id = ?",
        (account_key, id),
    ).fetchone()


def _agent_id_for_name(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT id FROM agents WHERE name = ?", (name,)).fetchone()
    return row["id"] if row is not None else None


def _agent_name_for_id(conn: sqlite3.Connection, agent_id: str) -> str | None:
    row = conn.execute("SELECT name FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return row["name"] if row is not None else None


def _message_stats(conn: sqlite3.Connection, conversation_key: str) -> tuple[int, str | None]:
    count_row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_key = ?", (conversation_key,)
    ).fetchone()
    last_row = conn.execute(
        "SELECT content FROM messages WHERE conversation_key = ? ORDER BY msg_seq DESC LIMIT 1", (conversation_key,)
    ).fetchone()
    return count_row["n"], (last_row["content"] if last_row is not None else None)


def _render(ctx: object, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    agent_id = _agent_id_for_name(conn, row["agent"]) if row["agent"] else None
    message_count, last_content = _message_stats(conn, row["key"])
    return {
        "id": row["id"],
        "created_at": grammar.render_ts(row["created_at"]),
        "updated_at": grammar.render_ts(row["updated_at"]),
        "state": row["state"],
        "tags": json.loads(row["tags_json"]),
        "harness_id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
        "version": row["version"],
        "name": row["name"] or row["key"],
        "agent_id": agent_id,
        "message_count": message_count,
        "last_message_preview": (last_content or "")[:200],
    }


#: The only two columns `_apply_update` ever writes — both single-word
#: literals from this file's own call sites, never body-derived, so
#: interpolating `column` into the `UPDATE` statement below carries no
#: injection risk despite not being a `?` placeholder (SQLite parameters
#: can't stand in for a column name).
_UPDATABLE_COLUMNS = frozenset({"state", "name"})


def _apply_update(conn: sqlite3.Connection, row: sqlite3.Row, *, column: str, value: str) -> None:
    """`_set_state`/`_rename`, collapsed: both were the same version-bump +
    single-column `UPDATE` + ledger row, differing only in which column and
    whether the ledger's own `state` is the new value (a state change) or
    unchanged (a rename)."""
    assert column in _UPDATABLE_COLUMNS
    now = time.time()
    new_version = row["version"] + 1
    ledger_state = value if column == "state" else row["state"]
    with write_txn(conn) as c:
        c.execute(
            f"UPDATE conversations SET {column} = ?, version = ?, updated_at = ? WHERE key = ?",
            (value, new_version, now, row["key"]),
        )
        ledger.record_change(
            c, noun="conversations", id=row["id"], kind="changed", state=ledger_state, version=new_version, at=now
        )


class ConversationsNoun:
    spec = spec

    def __init__(self, runtime: client_surface.Runtime) -> None:
        self._runtime = runtime

    def list(
        self, ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
    ) -> grammar.ListResponse:
        conn = _reader(ctx)
        account_key = account_key_for(principal)
        rows = conn.execute(
            "SELECT c.* FROM conversations c JOIN conversation_accounts ca ON ca.conversation_key = c.key "
            "WHERE ca.account_key = ? AND c.id IS NOT NULL",
            (account_key,),
        ).fetchall()
        rendered = [_render(ctx, conn, r) for r in rows]
        return grammar.page(rendered, params)

    def get(
        self, ctx: object, principal: Principal, id: str, parent_id: str | None = None
    ) -> Mapping[str, object] | problems.Problem:
        conn = _reader(ctx)
        row = _row(conn, account_key_for(principal), id)
        if row is None:
            return problems.make("NOT_FOUND", f"no conversation {id}")
        return _render(ctx, conn, row)

    def create(
        self, ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
    ) -> Mapping[str, object] | problems.Problem:
        conn = _reader(ctx)
        agent_id = body.get("agent_id")
        if agent_id is not None and _agent_name_for_id(conn, str(agent_id)) is None:
            return problems.make("NOT_FOUND", f"no agent {agent_id}")
        new_id = ids.make_id("conv")
        account_key = account_key_for(principal)
        client_surface.open_conversation(
            self._runtime, account=account_key, conversation=new_id, template_name="console", id=new_id
        )
        given_name = body.get("name")
        if given_name is not None:
            row = _row(conn, account_key, new_id)
            assert row is not None  # just created, under the same account, above
            _apply_update(ctx.conns.writer, row, column="name", value=str(given_name))  # type: ignore[attr-defined]
        row = _row(conn, account_key, new_id)
        assert row is not None
        return _render(ctx, conn, row)

    def update(
        self, ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
    ) -> Mapping[str, object] | problems.Problem:
        conn = _reader(ctx)
        row = _row(conn, account_key_for(principal), id)
        if row is None:
            return problems.make("NOT_FOUND", f"no conversation {id}")
        precondition = check_if_match(row, if_match)  # type: ignore[arg-type]
        if precondition is not None:
            return precondition
        if "name" not in body:
            return problems.make("VALIDATION", "PATCH body must include name")
        _apply_update(ctx.conns.writer, row, column="name", value=str(body["name"]))  # type: ignore[attr-defined]
        updated = _row(conn, account_key_for(principal), id)
        assert updated is not None
        return _render(ctx, conn, updated)

    def remove(self, ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
        return unavailable("conversations", "remove")

    def act(
        self,
        ctx: object,
        principal: Principal,
        id: str,
        name: str,
        body: Mapping[str, object],
        if_match: str | None,
    ) -> Mapping[str, object] | problems.Problem:
        conn = _reader(ctx)
        row = _row(conn, account_key_for(principal), id)
        if row is None:
            return problems.make("NOT_FOUND", f"no conversation {id}")
        precondition = check_if_match(row, if_match)  # type: ignore[arg-type]
        if precondition is not None:
            return precondition
        if name == "archive":
            _apply_update(ctx.conns.writer, row, column="state", value="archived")  # type: ignore[attr-defined]
        elif name == "unarchive":
            _apply_update(ctx.conns.writer, row, column="state", value="active")  # type: ignore[attr-defined]
        elif name == "rename":
            if "name" not in body:
                return problems.make("VALIDATION", "rename requires a name")
            _apply_update(ctx.conns.writer, row, column="name", value=str(body["name"]))  # type: ignore[attr-defined]
        else:
            return problems.make("HARNESS_CAPABILITY_MISSING", f"conversations.{name} is not available on this harness")
        updated = _row(conn, account_key_for(principal), id)
        assert updated is not None
        return _render(ctx, conn, updated)

    def search_doc(self, row: Mapping[str, object]) -> SearchDoc:
        return SearchDoc(
            title=str(row.get("name", "")),
            subtitle=(str(row["agent_id"]) if row.get("agent_id") else None),
            body=str(row.get("last_message_preview", "")),
            facets={"state": row.get("state"), "agent_id": row.get("agent_id"), "harness": row.get("harness_id")},
        )
