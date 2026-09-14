"""The `messages` noun: a conversation's transcript, and the turn.

`docs/tasks/H20-door-nouns-turn-side/spec.md` requirements 8-20, extended by
`docs/tasks/H21-watched-streaming-live-runs-stop/spec.md`. `create()` is the
turn: it builds `_DoorTurnObserver` (this file's own `TurnObserver`) and
calls `turn_client.send()` (which calls `client_surface.take_turn` and
nothing deeper) with it, then does the surrounding bookkeeping — backfilling
`run_id` onto whatever the turn appended, and reading the created resource
back by that `run_id` rather than the pre-H21 before/after `msg_seq`-range
diff.

Like `conversations.py`, this is a small class constructed once with a
`client_surface.Runtime` — `DoorContext` carries no such field, and
`create()` is the one verb that needs one (spec.md § Design; the same
reasoning `conversations.py`'s own module docstring gives in full).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import Mapping

from sadana import client_surface, ids, ledger, observability
from sadana.conversation import TurnKey, TurnResult
from sadana.conversation_store import load, write_txn
from sadana.door import events, grammar, problems, run_control, turn_client
from sadana.door.auth import Principal, account_key_for
from sadana.door.nouns import NounSpec, SearchDoc, unavailable

logger = logging.getLogger(__name__)

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


class _DoorTurnObserver:
    """H21's real `TurnObserver`. Built fresh per `create()` call, closing
    over the one connection/conversation/harness identity it needs — kept
    out of `turn_client.py` itself, which stays exactly as narrow as its
    own docstring already requires ("calls take_turn and nothing deeper").

    `run_id`/`assistant_id` are public: `create()` reads them back once the
    turn is over, to find the rows this call produced without the pre-H21
    before/after `msg_seq`-range diff.

    Every method here is a plain, synchronous `TurnObserver` method —
    `run_turn` never awaits any of them (conversation.py's own contract) —
    so a DB write inside one is a direct, synchronous call, never routed
    through `observability.Recorder`'s own async wrappers (which exist
    only so an *async* caller can offload a write via
    `asyncio.to_thread`; nothing here has a running loop free to await).
    `observability.insert_turn_run_started`/`insert_turn_run` are called
    directly instead — the same plain functions those async wrappers
    themselves call — wrapped in this class's own `try/except
    sqlite3.Error`, matching the "caught and logged, never raised into the
    run being observed" posture every other recording write already
    takes (CLAUDE.md)."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        harness_id: str,
        conversation_key: str,
        conversation_id: str,
        user_text: str,
    ) -> None:
        self._conn = conn
        self._harness_id = harness_id
        self._conversation_key = conversation_key
        self._conversation_id = conversation_id
        self._user_text = user_text
        self.run_id: str = ""
        self.assistant_id: str = ""
        self._assistant_msg_seq = -1
        self._started_at = 0.0
        self._stop_event: threading.Event | None = None

    def turn_started(self, turn_key: TurnKey, user_message_seq: int) -> None:
        """Mints `run_id`, registers it for `should_stop()`/`runs.stop`,
        and writes both the user's own row (content and position already
        known synchronously — `create()`'s own `text`, and the position
        `run_turn` itself just reported, never a value guessed ahead of
        the turn) and the assistant's provisional row, in one transaction.
        `msg_seq = user_message_seq + 1` for the assistant row: correct for
        the common case (a plain text reply); `turn_finished` below is what
        keeps it correct for a turn that ends up using a tool call too."""
        self._started_at = time.time()
        self.run_id = ids.make_id("run")
        self._stop_event = run_control.register(self.run_id)
        user_id = ids.make_id("msg")
        self.assistant_id = ids.make_id("msg")
        self._assistant_msg_seq = user_message_seq + 1
        try:
            observability.insert_turn_run_started(self._conn, turn_key, user_message_seq, self.run_id, self._started_at)
            with write_txn(self._conn) as c:
                c.execute(
                    "INSERT INTO messages "
                    "(conversation_key, msg_seq, role, content, tool_calls_json, tool_call_id, id, created_at, "
                    "state, run_id) VALUES (?, ?, 'user', ?, '[]', NULL, ?, ?, 'sent', ?)",
                    (self._conversation_key, user_message_seq, self._user_text, user_id, self._started_at, self.run_id),
                )
                ledger.record_change(
                    c, noun="messages", id=user_id, kind="created", state="sent", version=1, at=self._started_at
                )
                c.execute(
                    "INSERT INTO messages "
                    "(conversation_key, msg_seq, role, content, tool_calls_json, tool_call_id, id, created_at, "
                    "state, run_id) VALUES (?, ?, 'assistant', '', '[]', NULL, ?, ?, 'streaming', ?)",
                    (self._conversation_key, self._assistant_msg_seq, self.assistant_id, self._started_at, self.run_id),
                )
                ledger.record_change(
                    c,
                    noun="messages",
                    id=self.assistant_id,
                    kind="created",
                    state="streaming",
                    version=1,
                    at=self._started_at,
                )
        except sqlite3.Error:
            logger.warning("failed to write the live turn/message rows for %r", turn_key, exc_info=True)

    def text_delta(self, seq: int, text: str) -> None:
        events.push(
            {
                "type": "ephemeral",
                "name": "message.delta",
                "harness_id": self._harness_id,
                "data": {
                    "conversation_id": self._conversation_id,
                    "message_id": self.assistant_id,
                    "delta": text,
                    "seq": seq,
                },
            }
        )

    def turn_finished(self, result: TurnResult) -> None:
        """Three outcomes, not two — the reason a fixed `msg_seq` at
        `turn_started` isn't enough on its own:

        1. The turn's own real final reply landed exactly where the
           placeholder was (`result.final_text` is set, and it is the last
           message the turn appended) — the common, no-tool-call case.
           Finalize the placeholder in place, keeping its own id.
        2. Nothing beyond the user's own row was ever appended
           (`len(result.appended) == 1`) — the turn failed before any
           completion at all. Nothing else could ever want this slot, so
           it is safe to mark it `failed` directly.
        3. Anything else: either the turn failed *after* producing real
           content (a tool call, say), or it succeeded but the real final
           reply ended up at a later `msg_seq` than the placeholder's own
           guess (a tool round shifted it). Either way the placeholder does
           not correspond to anything real — finalizing it would silently
           overwrite or hide genuine content. Drop it instead: `client_
           surface.take_turn`'s own trailing `save()` call (unchanged)
           inserts the turn's real messages normally into the now-empty
           slot(s), exactly as every pre-H21 turn already does, and
           `create()`'s own `run_id` backfill (below) tags them."""
        final_msg_seq = result.appended.stop - 1
        now_wall = time.time()
        try:
            with write_txn(self._conn) as c:
                if result.final_text is not None and final_msg_seq == self._assistant_msg_seq:
                    c.execute(
                        "UPDATE messages SET content = ?, state = 'sent' "
                        "WHERE conversation_key = ? AND msg_seq = ? AND state = 'streaming'",
                        (result.final_text, self._conversation_key, self._assistant_msg_seq),
                    )
                    ledger.record_change(
                        c, noun="messages", id=self.assistant_id, kind="changed", state="sent", version=1, at=now_wall
                    )
                elif len(result.appended) == 1:
                    c.execute(
                        "UPDATE messages SET state = 'failed' "
                        "WHERE conversation_key = ? AND msg_seq = ? AND state = 'streaming'",
                        (self._conversation_key, self._assistant_msg_seq),
                    )
                    ledger.record_change(
                        c, noun="messages", id=self.assistant_id, kind="changed", state="failed", version=1, at=now_wall
                    )
                else:
                    c.execute(
                        "DELETE FROM messages WHERE conversation_key = ? AND msg_seq = ? AND state = 'streaming'",
                        (self._conversation_key, self._assistant_msg_seq),
                    )
                    ledger.record_change(
                        c, noun="messages", id=self.assistant_id, kind="deleted", state=None, version=None, at=now_wall
                    )
            observability.insert_turn_run(self._conn, result, now_wall - self._started_at, recorded_at=now_wall)
        except sqlite3.Error:
            logger.warning("failed to finalize the live turn/message rows for %r", result.turn_key, exc_info=True)
        finally:
            if self.run_id:
                run_control.unregister(self.run_id)

    def should_stop(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()


class MessagesNoun:
    spec = spec

    def __init__(self, runtime: client_surface.Runtime) -> None:
        self._runtime = runtime

    def list(
        self, ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
    ) -> grammar.ListResponse | problems.Problem:
        conn = _reader(ctx)
        conversation = _conversation_row(conn, account_key_for(principal), parent_id) if parent_id is not None else None
        if conversation is None:
            # H21, re-verifying H20's own carried-over finding: an unknown
            # or foreign parent is a 404, matching what `get()`/`create()`
            # in this same file already answer for the identical
            # condition — not an empty `200`, which reads indistinguishably
            # from "a real, visible conversation with zero messages."
            return problems.make("NOT_FOUND", f"no conversation {parent_id}")
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

        # Requirement 15's own pre-turn read — see spec.md § Concerns for
        # the narrow, accepted race this carries. Used only as the bulk
        # `run_id` backfill's lower bound and as the fallback position for
        # a paused-resume call (no `_DoorTurnObserver` ever runs for one —
        # `client_surface.take_turn`'s resume branch never calls
        # `run_turn`), not for the observer's own addressing, which reads
        # the position `run_turn` itself reports, live.
        before = load(conn, conversation_key, now=ctx.clock())  # type: ignore[attr-defined]
        msg_count_before = len(before.messages)

        observer = _DoorTurnObserver(
            conn=ctx.conns.writer,  # type: ignore[attr-defined]
            harness_id=ctx.runtime.harness_id,  # type: ignore[attr-defined]
            conversation_key=conversation_key,
            conversation_id=conversation["id"],
            user_text=text,
        )
        outcome = asyncio.run(
            turn_client.send(
                self._runtime, account=account_key, conversation=conversation_key, text=text, observer=observer
            )
        )

        run_id = observer.run_id or None
        if run_id is not None:
            # A no-op for the user/assistant rows the observer already
            # tagged (`run_id IS NULL` excludes them) — this only reaches
            # rows the turn produced some other way: intermediate tool
            # rows, or the real final reply once `turn_finished` dropped
            # the placeholder that used to sit where it landed.
            with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
                c.execute(
                    "UPDATE messages SET run_id = ? WHERE conversation_key = ? AND msg_seq >= ? AND run_id IS NULL",
                    (run_id, conversation_key, msg_count_before),
                )
            user_row = conn.execute(
                "SELECT * FROM messages WHERE conversation_key = ? AND run_id = ? AND role = 'user'",
                (conversation_key, run_id),
            ).fetchone()
        else:
            # No observer ever ran (a paused-resume call, H18) — the same
            # "find the last assistant row, mark it failed" this file's
            # own pre-H21 code always did for exactly this path.
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
            user_row = conn.execute(
                "SELECT * FROM messages WHERE conversation_key = ? AND msg_seq = ?",
                (conversation_key, msg_count_before),
            ).fetchone()

        if not outcome.ok:
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
