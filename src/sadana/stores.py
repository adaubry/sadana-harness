"""Every add-on table this store carries, and who may hold a connection to it.

`docs/tasks/PERSONA-02-who-the-agent-is-talking-to/plan.md`, step 6's
amendment. `conversation_store.open_store()` creates its own tables; the
blocks that added tables to the same database afterwards each have their own
`ensure_schema`, and every caller that opens the store has had to remember all
of them.

This module exists because that checklist reached three members and then bit:
`persona_store.known_accounts()` reads `memory_entries`, and the `sadana
persona` subcommand had ensured only PERSONA's own schema, so a CLI verb died
with `no such table` on a store that had never chatted. A fourth table would
have been a fourth chance to forget, on a failure that no type checker and no
green suite can see — and H16 found it had become the seventh, through
`ledger.inventory()`, which queries every table at once.

Deliberately not a registry: a fixed list of calls in a fixed order, and a new
block adds its line here rather than registering itself (CLAUDE.md's rule about
a dispatch seam earning its cost only once a second real member exists — this
is a function, not a seam).

H16 made it the owner of connections as well. That is the same job seen from
one step back: this is already the module that knows about every store at once,
and "which connection may a caller write through" is a question with one answer
for the whole database rather than one per table. It cannot live in
`conversation_store`, which `memory_store` and `persona_store` both import;
that direction is what keeps them acyclic.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from sadana import ledger, marketplace, memory_store, observability, persona_store, plugin_install, plugins
from sadana.conversation_store import open_store, store_path_from_config
from sadana.door import idempotency as door_idempotency
from sadana.door import operations as door_operations
from sadana.door.nouns import inspections as door_nouns_inspections


def ensure_schemas(conn: sqlite3.Connection) -> None:
    """Every table, created if missing. Idempotent, and safe on a store that
    predates any of them.

    DDL only. Keeping the name honest is not cosmetic: the first cut of H16
    also reconciled the two file-backed indexes from here, which put a
    directory walk, a SHA-256 of every character and a full validation of every
    plugin behind a name that promises schema creation — so nobody costing a
    call site would read it. A self-check measured it at 25 ms with 20 plugins
    installed, paid by every `sadana` subcommand including pure reads, against
    0.09 ms before. See `reconcile_indexes` for where that work went.
    """
    ledger.ensure_schema(conn)
    memory_store.ensure_schema(conn)
    persona_store.ensure_schema(conn)
    plugin_install._ensure_schema(conn)
    marketplace._ensure_schema(conn)
    observability.ensure_schema(conn)
    door_idempotency.ensure_schema(conn)
    door_operations.ensure_schema(conn)
    door_nouns_inspections.ensure_schema(conn)


def reconcile_indexes(conn: sqlite3.Connection) -> None:
    """Make the two indexes over things that live as files match the files.

    Separate from `ensure_schemas` because it is a different kind of work with
    a different cost, and only some callers need it. A process that takes turns
    needs it: `client_surface.enabled_plugin_set` reads `plugin_state` to know
    what is switched off, so a stale index means a disabled plugin is still
    offered to the model. A one-shot `sadana` subcommand does not — nothing it
    reads touches either index, and `sadana memory list` has no business
    hashing the characters directory before it prints.

    Reconciliation lives here rather than inside `persona_store.list_characters`
    because a read that writes is a trap for the next caller, and
    `list_characters` is reached from paths that hold no connection.

    The cost of doing it at open rather than continuously is named rather than
    hidden: a character or plugin file added while a long-running process is up
    is not indexed until that process reopens the store. H24 and H26 own those
    surfaces and are where a live refresh belongs.
    """
    persona_store.reconcile_agents(conn, persona_store.characters_dir_from_config())
    plugin_install.reconcile_plugin_state(conn, plugins._plugins_root())


class Connections:
    """The connections one process holds to one store, and the locks over them.

    H16, serving P8. Three things, and the division between them is the whole
    design:

    **One writer.** Every write in the process goes through it, serialized by
    the lock `conversation_store.write_txn` takes — held inside a transaction,
    never around a model round trip. That is what lets two people's turns
    overlap: the expensive part of a turn holds no database lock at all. The
    reference arrived at one-connection-one-lock the expensive way
    (`plugins/memory/holographic/store.py:100-160`: several providers in one
    process raced as independent WAL writers until one left a transaction open
    and pinned the write lock for the full busy timeout). Their refcounted
    per-path registry is declined — it exists so many independently-constructed
    store objects can share a connection, and we construct exactly one.

    **Thread-local readers.** A caller that only reads takes its own
    connection, so listing conversations never queues behind a turn.

    **A lock per conversation**, from `conversation_lock()` below — the one a
    turn holds for its whole length.

    What this survives: two turns on two conversations in two threads, and a
    reader listing conversations while a turn runs. What it does not: two turns
    on one conversation on one event loop; a call node at its approval gate
    (H18 removes this).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.writer = open_store(path)
        ensure_schemas(self.writer)
        reconcile_indexes(self.writer)
        self._readers = threading.local()

    def reader(self) -> sqlite3.Connection:
        """This thread's read-only connection, opened on first use.

        **Held nowhere else, and never closed explicitly.** That is not an
        oversight, it is the entire lesson of `hermes_state.py:4818-4845`: they
        kept thread-local readers *plus a strong set* so the connections could
        be closed on shutdown, and since a store that is never closed keeps the
        set alive, every worker thread that ever read left behind a connection
        and two descriptors — the database and its `-wal` — until the process
        hit the 256-descriptor soft limit a service manager hands it. Every
        request then failed with `EMFILE` while the process stayed up, so the
        supervisor's restart-on-exit never fired. Without that second
        reference, CPython's refcounting closes a reader when its thread's
        local storage dies, which is exactly when it should.

        `query_only` so a reader cannot take a write lock even by accident, and
        no `ensure_schemas`: this connection creates nothing.

        ponytail: one connection per reading thread, opened on demand. The
        ceiling is `ThreadingHTTPServer`'s thread-per-request, which pays one
        open and close per request. If that ever shows up in a profile the
        upgrade is the reference's own bounded LIFO pool
        (`hermes_state.py:387`, `_READ_POOL_MAX = 8`) — never a set of strong
        references to these.
        """
        conn: sqlite3.Connection | None = getattr(self._readers, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA query_only=1")
            self._readers.conn = conn
        return conn


# ponytail: a plain dict, never evicted. One `threading.Lock` is about 64 bytes,
# so a box holding conversations in the thousands costs well under a megabyte.
#
# The ceiling is real and is **not** bounded by how many conversations a person
# starts. A conversation key is `platform:chat_id[:thread_id]`
# (`gateway.session_key_for`), and `chat_id` is whatever string arrives in a
# webhook body — `channel_webhook.parse_webhook_request` requires only that it
# be a non-empty `str`, with no length or charset bound. So a caller that has
# the shared secret can mint distinct keys without limit, and every one of them
# retains a key string and a lock here for the life of the process. That is
# post-authentication, and the same unbounded key already grows the
# `conversations` table and the artifact directory names, so the lock dict is
# not the weakest part of it — but the honest statement is "bounded by the
# webhook's own input validation", not "nothing mints keys without limit".
# Bounding `chat_id` at the parse boundary is the fix and belongs with that
# block; recorded in H16's review.md rather than widened into this work item.
#
# Evicting is the harder and less useful half: a refcount here would be the
# registry-of-one CLAUDE.md warns about, in a place where the wrong answer is
# a lock two turns do not share.
_conversation_locks: dict[str, threading.Lock] = {}
_conversation_locks_guard = threading.Lock()


def conversation_lock(key: str) -> threading.Lock:
    """The lock a turn on `key` holds for its whole length.

    Per conversation rather than per process, which is the entire point of P8:
    two people talking to the same box in two different conversations wait for
    nothing, and somebody merely listing waits for nothing at all. Two turns in
    the *same* conversation still take it in turns, which is correct — a
    conversation is a sequence, and running two turns into one at once would
    interleave a history rather than share it.

    Addressed by name, never by holding a lock object: the caller passes the
    conversation's key and gets the one lock for it, which is CLAUDE.md's
    names-not-pointers rule applied to a thing whose identity must outlive any
    one caller's reference to it.

    Non-reentrant, like the module-level lock it replaced: a caller holding it
    must not call something that takes it again.
    """
    with _conversation_locks_guard:
        return _conversation_locks.setdefault(key, threading.Lock())


@contextmanager
def open_cli_store() -> Iterator[sqlite3.Connection]:
    """Open the configured store with every schema ensured, and close it.

    What a `sadana` subcommand that reads or writes the database directly —
    without taking a turn — needs, and what `subcommands/memory.py` and
    `subcommands/persona.py` had a copy of each. The copies became identical
    the moment both had to ensure more than their own block's tables, which
    is the point at which one of them should stop existing.

    Still a bare connection rather than a `Connections`: a subcommand is a
    process with one thread that does one thing and exits, so the reader/writer
    split would be two connections where one is used. The write lock still
    applies — it lives in `write_txn`, not in `Connections`.

    Schemas but **not** `reconcile_indexes`: nothing a subcommand reads touches
    either file-backed index, and reconciling would put a plugin validation and
    a directory hash in front of every `sadana memory list`.

    Not for a client taking a turn: that is `client_surface.open_runtime()`,
    which holds its connections for the life of the process and carries the
    plugin scan and the recorder besides.
    """
    with closing(open_store(store_path_from_config())) as conn:
        ensure_schemas(conn)
        yield conn
