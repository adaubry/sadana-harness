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

import sqlite3
from pathlib import Path

from sadana import config, memory, persona
from sadana.conversation_store import write_txn
from sadana.persona import Character

_SCHEMA = """
CREATE TABLE IF NOT EXISTS persona_selections (
    account_key TEXT NOT NULL PRIMARY KEY,
    name        TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
"""

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
    existing one, so no `_migrate_columns`-style guard is needed and a store
    written before this work item loads unchanged."""
    conn.executescript(_SCHEMA)


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
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO persona_selections (account_key, name, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (account_key) "
            "DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
            (account_key, name, now),
        )


def clear_selection(conn: sqlite3.Connection, account_key: memory.AccountKey) -> None:
    with write_txn(conn) as c:
        c.execute("DELETE FROM persona_selections WHERE account_key = ?", (account_key,))


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
