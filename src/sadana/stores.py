"""Every add-on table this store carries, ensured in one call.

`docs/tasks/PERSONA-02-who-the-agent-is-talking-to/plan.md`, step 6's
amendment. `conversation_store.open_store()` creates its own tables; the two
blocks that added tables to the same database afterwards — MEMORY-01 and
PERSONA-01 — each have their own `ensure_schema`, and every caller that opens
the store has had to remember both.

This module exists because that checklist reached three members and then bit:
`persona_store.known_accounts()` reads `memory_entries`, and the `sadana
persona` subcommand had ensured only PERSONA's own schema, so a CLI verb died
with `no such table` on a store that had never chatted. A fourth table would
have been a fourth chance to forget, on a failure that no type checker and no
green suite can see.

Deliberately not a registry: two calls in a fixed order, and a third block
adds its line here rather than registering itself (CLAUDE.md's rule about a
dispatch seam earning its cost only once a second real member exists — this
is a function, not a seam).

It cannot live in `conversation_store`, which `memory_store` and
`persona_store` both import; that direction is what keeps them acyclic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager

from sadana import memory_store, persona_store
from sadana.conversation_store import open_store, store_path_from_config


def ensure_schemas(conn: sqlite3.Connection) -> None:
    """Idempotent, and safe on a store that predates any of these tables."""
    memory_store.ensure_schema(conn)
    persona_store.ensure_schema(conn)


@contextmanager
def open_cli_store() -> Iterator[sqlite3.Connection]:
    """Open the configured store with every schema ensured, and close it.

    What a `sadana` subcommand that reads or writes the database directly —
    without taking a turn — needs, and what `subcommands/memory.py` and
    `subcommands/persona.py` had a copy of each. The copies became identical
    the moment both had to ensure more than their own block's tables, which
    is the point at which one of them should stop existing.

    Not for a client taking a turn: that is `client_surface.open_runtime()`,
    which holds its connection for the life of the process and carries the
    plugin scan and the recorder besides.
    """
    with closing(open_store(store_path_from_config())) as conn:
        ensure_schemas(conn)
        yield conn
