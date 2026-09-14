"""Where characters are kept, and which one an account chose.

`docs/tasks/PERSONA-01-characters-you-write/spec.md`. The I/O half of PERSONA:
a directory of character files the person owns, and one row per account
naming the character they selected. `persona.py` stays pure; this module is
the only one that reads a disk or a database on its behalf.

Two stores would be one too many. A character's text lives in exactly one
place — the file its author wrote — and this module never copies it anywhere.
What is stored is the *name*, which is what `sadana persona list` shows, what
`persona use` takes, and what a conversation's voice is resolved from at the
moment it is created. hermes learned that one the expensive way: their
surfaces disagreed about whether persona state was the name or the rendered
text, and years of stale per-surface writes resurrected personalities users
had turned off (`hermes_cli/personality.py:15-25`).

Disk and SQLite share a file here because CLAUDE.md separates I/O from a
block's *pure* module, not I/O from other I/O — `memory_store.py` already
holds SQLite alongside a filesystem seed.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from sadana import config, conversation_store, ids, ledger, memory, memory_store, persona
from sadana.conversation_store import fill_legacy_identity, migrate_columns, write_txn
from sadana.persona import Character

_SCHEMA = """
CREATE TABLE IF NOT EXISTS persona_selections (
    account_key TEXT NOT NULL PRIMARY KEY,
    name        TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    -- H16.
    id          TEXT,
    created_at  REAL,
    version     INTEGER NOT NULL DEFAULT 1
);

-- H16 requirement 8: an index over the characters directory, so the whole
-- inventory is cheap to enumerate without walking a disk (P3).
--
-- Reconciled, never authoritative. The file its author wrote is the character;
-- this is a row that keeps pace with it. Every disagreement is resolved in the
-- file's favour, which is also how the reference treats its own file manifests
-- (`tools/skill_ledger.py:157-182` hashes bytes and never consults an mtime —
-- a restored file with an old timestamp is exactly what a timestamp gets
-- wrong).
--
-- `id` is the PRIMARY KEY and `name` is UNIQUE, which is the two-halves rule
-- from `console_fit_plan.md` §5(a) with a database constraint behind each:
-- the id is what a ledger row points at, the name is what a person types.
CREATE TABLE IF NOT EXISTS agents (
    id             TEXT PRIMARY KEY,
    name           TEXT UNIQUE NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    state          TEXT NOT NULL DEFAULT 'active',
    template       TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1,
    content_sha256 TEXT NOT NULL
);
"""

#: See `conversation_store._MIGRATED_COLUMNS`.
_MIGRATED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "persona_selections": (
        ("id", "TEXT"),
        ("created_at", "REAL"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
    ),
}

#: Every word `agents.state` may hold. One today: nothing retires a character,
#: and deleting its file removes the row rather than changing its state.
AGENT_STATES = frozenset({"active"})

#: What `persona new` writes. It parses as a valid character on the spot, so
#: `persona new` followed by `persona list` shows something rather than a
#: file that has to be edited before the tool will admit it exists.
_TEMPLATE = """---
description: say in one line what this voice is for
tone:
style:
---
Write the voice here: how it should answer, what it should never do.
"""


def characters_dir_from_config() -> Path:
    """`SADANA_PERSONA_DIR`, else `<config_dir>/characters`."""
    return config.env_path("SADANA_PERSONA_DIR", default=config.get_paths().config_dir / "characters")


def character_path(characters_dir: Path, name: str) -> Path:
    """`name`'s file, having checked twice that it is one.

    `persona.valid_name()` first, so a caller-supplied string is rejected
    before it is ever joined to a path; then `is_relative_to` on the
    *resolved* path, which is what catches a symlink inside the directory
    pointing somewhere else. Both, never either (CLAUDE.md)."""
    if not persona.valid_name(name):
        raise persona.CharacterError(f"{name!r} is not a valid character name")
    root = characters_dir.resolve()
    path = (characters_dir / f"{name}.md").resolve()
    if not path.is_relative_to(root):
        raise persona.CharacterError(f"{name!r} resolves outside {root}")
    return path


def load_character(characters_dir: Path, name: str) -> Character:
    """Read and parse one character. Raises `CharacterError` for a bad name,
    a missing file, bytes that are not UTF-8, or anything `persona.parse`
    refuses — every failure to produce a character is the one type."""
    path = character_path(characters_dir, name)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise persona.CharacterError(f"no character named {name!r} at {path}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise persona.CharacterError(f"{path} could not be read: {e}") from e
    try:
        return persona.parse(name, text)
    except persona.CharacterError as e:
        raise persona.CharacterError(f"{path} {e}") from e


def list_characters(characters_dir: Path) -> tuple[Character, ...]:
    """Every character in the directory, by name. A file that does not parse
    is skipped rather than failing the listing: one malformed character must
    not hide the others from the person trying to find out what they have."""
    if not characters_dir.is_dir():
        return ()
    found = []
    for path in sorted(characters_dir.glob("*.md")):
        try:
            found.append(load_character(characters_dir, path.stem))
        except persona.CharacterError:
            continue
    return tuple(found)


def write_template(characters_dir: Path, name: str) -> Path:
    """Create `name`'s file from the template and return its path. Refuses to
    overwrite: this command's whole job is to make the directory findable,
    and a `new` that could silently replace a character someone wrote would
    be the worst possible way to learn where it lives."""
    path = character_path(characters_dir, name)
    if path.exists():
        raise persona.CharacterError(f"{path} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_TEMPLATE, encoding="utf-8")
    return path


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent, the same "create tables on open" posture
    `memory_store.ensure_schema` takes. A new table, not a new column on an
    existing one, so no `migrate_columns`-style guard was needed for the
    original shape; H16 added three columns to it, which is what the map is
    for."""
    conn.executescript(_SCHEMA)
    migrate_columns(conn, _MIGRATED_COLUMNS)
    fill_legacy_identity(conn, "persona_selections", "agt", time_columns=("created_at",))


def get_selection(conn: sqlite3.Connection, account_key: memory.AccountKey) -> str | None:
    """The character name this account chose, or `None` — which means the
    neutral voice, not a missing value to fill in."""
    row = conn.execute(
        "SELECT name FROM persona_selections WHERE account_key = ?",
        (account_key,),
    ).fetchone()
    return row["name"] if row is not None else None


def set_selection(conn: sqlite3.Connection, account_key: memory.AccountKey, name: str, *, now: float) -> None:
    """Record the name. Never the rendered text — see this module's docstring."""
    at = time.time()
    with write_txn(conn) as c:
        existed = c.execute("SELECT id FROM persona_selections WHERE account_key = ?", (account_key,)).fetchone()
        c.execute(
            "INSERT INTO persona_selections (account_key, name, updated_at, id, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (account_key) "
            "DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at, "
            "version = persona_selections.version + 1",
            (account_key, name, now, ids.make_id("agt"), at),
        )
        row = c.execute("SELECT id, version FROM persona_selections WHERE account_key = ?", (account_key,)).fetchone()
        ledger.record_change(
            c,
            noun="agents",
            id=row["id"],
            kind="changed" if existed else "created",
            state=None,
            version=row["version"],
            at=at,
        )


def clear_selection(conn: sqlite3.Connection, account_key: memory.AccountKey) -> None:
    """Back to the neutral voice. Tombstoned, like every other delete: the
    ledger row goes in before the row does, because afterwards there is no id
    left to name it by."""
    at = time.time()
    with write_txn(conn) as c:
        row = c.execute("SELECT id FROM persona_selections WHERE account_key = ?", (account_key,)).fetchone()
        if row is not None and row["id"] is not None:
            ledger.record_change(c, noun="agents", id=row["id"], kind="deleted", state=None, version=None, at=at)
        c.execute("DELETE FROM persona_selections WHERE account_key = ?", (account_key,))


def reconcile_agents(conn: sqlite3.Connection, characters_dir: Path) -> None:
    """Make the `agents` index match the characters directory.

    Three outcomes per character, and one for a row whose file has gone:
    a file with no row is inserted; a file whose bytes hash differently bumps
    `version` and `updated_at`; a file that is unchanged costs one hash and no
    write; a row whose file has vanished is announced as `deleted` and then
    removed.

    **The hash, not the modification time.** A character restored from a backup
    carries an old mtime and new content, and a file rewritten with identical
    content carries a new mtime and nothing to announce — a timestamp is wrong
    in both directions. The reference's own file manifests reached the same
    place (`tools/skill_ledger.py:157-182`, `{path, sha256}` per file, no
    timestamp anywhere).

    Only characters that **parse** get a row, matching `list_characters`'
    existing posture: a half-written file is not yet a character, and giving it
    a row would put it in front of the console as one. It simply has no row
    until it parses, and gets `created` then.

    Never raises for a directory that does not exist — nobody has written a
    character yet, which is a normal state and not an error.
    """
    at = time.time()
    found = {
        character.name: hashlib.sha256(character_path(characters_dir, character.name).read_bytes()).hexdigest()
        for character in list_characters(characters_dir)
    }
    known = {
        row["name"]: (row["id"], row["content_sha256"], row["version"])
        for row in conn.execute("SELECT id, name, content_sha256, version FROM agents")
    }
    if found == {name: digest for name, (_id, digest, _v) in known.items()}:
        return
    with write_txn(conn) as c:
        for name, digest in found.items():
            if name not in known:
                agent_id = ids.make_id("agt")
                c.execute(
                    "INSERT INTO agents (id, name, created_at, updated_at, content_sha256) VALUES (?, ?, ?, ?, ?)",
                    (agent_id, name, at, at, digest),
                )
                ledger.record_change(c, noun="agents", id=agent_id, kind="created", state="active", version=1, at=at)
                continue
            agent_id, known_digest, version = known[name]
            if known_digest == digest:
                continue
            c.execute(
                "UPDATE agents SET content_sha256 = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                (digest, at, agent_id),
            )
            ledger.record_change(
                c, noun="agents", id=agent_id, kind="changed", state="active", version=version + 1, at=at
            )
        for name, (agent_id, _digest, _version) in known.items():
            if name in found:
                continue
            ledger.record_change(c, noun="agents", id=agent_id, kind="deleted", state=None, version=None, at=at)
            c.execute("DELETE FROM agents WHERE id = ?", (agent_id,))


def known_accounts(conn: sqlite3.Connection) -> tuple[memory.AccountKey, ...]:
    """Every account this assistant has spoken with, sorted.

    The union of three sources, because no one of them sees everybody: an
    account that chatted and never had anything remembered about it exists
    only in `conversation_accounts`; one that only ever had a character
    chosen for it exists only in `persona_selections`; and a store written
    before PERSONA-02 has conversations with no account recorded at all,
    whose people show up here only through what was remembered about them.

    Derived on every call. No index, no cached list, nothing to go stale.
    """
    selections = conn.execute("SELECT DISTINCT account_key FROM persona_selections").fetchall()
    return tuple(
        sorted(
            conversation_store.accounts_with_conversations(conn)
            | memory_store.accounts_with_memories(conn)
            | {row["account_key"] for row in selections}
        )
    )


def account_exists(conn: sqlite3.Connection, account_key: memory.AccountKey) -> bool:
    """Whether anything in this store has ever used `account_key`. What
    `persona use` asks before it agrees to write a selection nothing would
    ever read (PERSONA-02 requirement 8) — the owner's own account is
    exempt, and that exemption belongs to the command, not here."""
    return account_key in known_accounts(conn)


def resolve_voice(conn: sqlite3.Connection, account_key: memory.AccountKey, characters_dir: Path) -> str:
    """The voice `account_key` speaks in, for a conversation being created.

    What it survives: no selection, and a character whose file has been
    deleted, renamed, made unreadable or broken since it was selected — all
    of them come back as `persona.NEUTRAL_VOICE`. What it does not survive,
    and is not meant to: a connection whose `ensure_schema` was never run,
    which raises `sqlite3.OperationalError` like any other unprepared store
    access. `client_surface.open_runtime()` ensures it; a caller hand-building
    a `Runtime` owns that setup, exactly as `take_turn`'s docstring already
    says for `memory_store`. A scheduled trigger firing at
    3am with nobody watching must still answer, and `sadana persona use` is
    where a person finds out their name was wrong, loudly, while they are
    there to read it. That split is deliberate and its cost is written down
    in spec.md § Concerns: a broken character file produces a reply that is
    fluent, neutral, and not the voice the person asked for."""
    name = get_selection(conn, account_key)
    if name is None:
        return persona.NEUTRAL_VOICE
    try:
        return persona.render(load_character(characters_dir, name))
    except persona.CharacterError:
        return persona.NEUTRAL_VOICE
