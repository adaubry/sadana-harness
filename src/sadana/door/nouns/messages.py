"""The `messages` noun: a conversation's transcript, and the turn.

`docs/tasks/H20-door-nouns-turn-side/spec.md` requirements 8-20. `create()`
is the turn: it calls `turn_client.send()` (which calls `client_surface.
take_turn` and nothing deeper) and does the surrounding bookkeeping itself —
locating the newly-appended rows, backfilling their `run_id`, and marking a
failed turn's assistant row.

Like `conversations.py`, this is a small class constructed once with a
`client_surface.Runtime` — `DoorContext` carries no such field, and
`create()` is the one verb that needs one (spec.md § Design; the same
reasoning `conversations.py`'s own module docstring gives in full).
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping

from sadana import client_surface
from sadana.conversation_store import load, write_txn
from sadana.door import grammar, problems, turn_client
from sadana.door.auth import Principal, account_key_for
from sadana.door.nouns import NounSpec, SearchDoc, unavailable

CAPABILITIES: tuple[str, ...] = ()

spec = NounSpec(
    plural="messages",
    prefix="msg",
    parent="conversations",
    filterable=frozenset({"role", "created_at", "run_id"}),
    orderable=frozenset({"created_at"}),
    states=frozenset({"streaming", "sent", "failed"}),
    actions={},
)


def _reader(ctx: object) -> sqlite3.Connection:
    return ctx.conns.reader()  # type: ignore[attr-defined]


def _conversation_row(conn: sqlite3.Connection, account_key: str, id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT c.* FROM conversations c JOIN conversation_accounts ca ON ca.conversation_key = c.key "
        "WHERE ca.account_key = ? AND c.id = ?",
        (account_key, id),
    ).fetchone()


def _turn_seq_for(conn: sqlite3.Connection, run_id: str | None) -> int | None:
    if run_id is None:
        return None
    found = conn.execute("SELECT turn_seq FROM turn_runs WHERE id = ?", (run_id,)).fetchone()
    return found["turn_seq"] if found is not None else None


def _render(ctx: object, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "created_at": grammar.render_ts(row["created_at"]),
        "updated_at": grammar.render_ts(row["created_at"]),  # never edited once written
        "state": row["state"],
        "tags": {},
        "harness_id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
        "version": 1,
        "role": row["role"],
        "content": row["content"],
        "run_id": row["run_id"],
        "turn_seq": _turn_seq_for(conn, row["run_id"]),
    }


class MessagesNoun:
    spec = spec

    def __init__(self, runtime: client_surface.Runtime) -> None:
        self._runtime = runtime

    def list(
        self, ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
    ) -> grammar.ListResponse:
        conn = _reader(ctx)
        conversation = _conversation_row(conn, account_key_for(principal), parent_id) if parent_id is not None else None
        if conversation is None:
            return grammar.ListResponse(data=[], next_page_token=None, count=0 if params.count else None)
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_key = ? AND id IS NOT NULL", (conversation["key"],)
        ).fetchall()
        rendered = [_render(ctx, conn, r) for r in rows]
        return grammar.page(rendered, params)

    def get(
        self, ctx: object, principal: Principal, id: str, parent_id: str | None = None
    ) -> Mapping[str, object] | problems.Problem:
        conn = _reader(ctx)
        conversation = _conversation_row(conn, account_key_for(principal), parent_id) if parent_id is not None else None
        if conversation is None:
            return problems.make("NOT_FOUND", f"no message {id}")
        row = conn.execute(
            "SELECT * FROM messages WHERE conversation_key = ? AND id = ?", (conversation["key"], id)
        ).fetchone()
        if row is None:
            return problems.make("NOT_FOUND", f"no message {id}")
        return _render(ctx, conn, row)

    def create(
        self, ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
    ) -> Mapping[str, object] | problems.Problem:
        if parent_id is None:
            return problems.make("VALIDATION", "messages.create requires a parent conversation")
        conn = _reader(ctx)
        account_key = account_key_for(principal)
        conversation = _conversation_row(conn, account_key, parent_id)
        if conversation is None:
            return problems.make("NOT_FOUND", f"no conversation {parent_id}")
        if conversation["state"] != "active":
            return problems.make("CONFLICT", f"create requires state active; current state is {conversation['state']}")
        if "content" not in body:
            return problems.make("VALIDATION", "messages.create requires content")
        text = str(body["content"])
        conversation_key = conversation["key"]

        # Requirement 15's own before/after reads — see spec.md § Concerns
        # for the narrow, accepted race this carries.
        before = load(conn, conversation_key, now=ctx.clock())  # type: ignore[attr-defined]
        turn_seq_before = before.next_turn_seq
        msg_count_before = len(before.messages)

        outcome = asyncio.run(
            turn_client.send(self._runtime, account=account_key, conversation=conversation_key, text=text)
        )

        run_row = conn.execute(
            "SELECT id FROM turn_runs WHERE conversation_key = ? AND turn_seq = ?",
            (conversation_key, turn_seq_before),
        ).fetchone()
        run_id = run_row["id"] if run_row is not None else None
        # No second `load()`/count needed to gate this: the `UPDATE`'s own
        # `WHERE` already touches zero rows when the turn appended nothing
        # new, which is exactly what a count-then-branch would have decided.
        if run_id is not None:
            with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
                c.execute(
                    "UPDATE messages SET run_id = ? WHERE conversation_key = ? AND msg_seq >= ?",
                    (run_id, conversation_key, msg_count_before),
                )

        user_row = conn.execute(
            "SELECT * FROM messages WHERE conversation_key = ? AND msg_seq = ?", (conversation_key, msg_count_before)
        ).fetchone()

        if not outcome.ok:
            assistant_row = conn.execute(
                "SELECT * FROM messages WHERE conversation_key = ? AND msg_seq >= ? AND role = 'assistant' "
                "ORDER BY msg_seq DESC LIMIT 1",
                (conversation_key, msg_count_before),
            ).fetchone()
            if assistant_row is not None:
                with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
                    c.execute(
                        "UPDATE messages SET state = 'failed' WHERE conversation_key = ? AND msg_seq = ?",
                        (conversation_key, assistant_row["msg_seq"]),
                    )
            return problems.make("INTERNAL", outcome.diagnostic)

        assert user_row is not None  # the turn succeeded, so its own user row exists
        return _render(ctx, conn, user_row)

    def update(
        self, ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
    ) -> problems.Problem:
        return unavailable("messages", "update")

    def remove(self, ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
        return unavailable("messages", "remove")

    def act(
        self,
        ctx: object,
        principal: Principal,
        id: str,
        name: str,
        body: Mapping[str, object],
        if_match: str | None,
    ) -> problems.Problem:
        return unavailable("messages", name)

    def search_doc(self, row: Mapping[str, object]) -> SearchDoc:
        content = str(row.get("content") or "")
        return SearchDoc(
            title=content[:80], facets={"role": row.get("role"), "conversation": row.get("conversation_id")}
        )
