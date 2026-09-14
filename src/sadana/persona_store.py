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
import os
import sqlite3
import time
from dataclasses import dataclass
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

#: Every word `agents.state` may hold. `draft` (H26) exists only for a row
#: the door itself created and hasn't activated yet — a character found by
#: the ordinary directory scan is `active` on sight, since nobody is
#: mid-edit on a file that was simply discovered. Nothing retires a
#: character; deleting its file removes the row rather than changing its
#: state.
AGENT_STATES = frozenset({"draft", "active"})

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

#: The built-in template(s) `create_character` can start a new voice from
#: (H26). Not a table — `door/nouns/agent_templates.py` renders these as a
#: read-only noun with a hash-derived id, never a stored row; a second
#: template, if one is ever wanted, is a second entry here, not a registry.
#: Lives here, not in either door noun module, because `door/nouns/agents.py`
#: and `door/nouns/agent_templates.py` both need it and a noun module never
#: imports another noun module (`door/nouns/__init__.py`'s own rule).
BUILTIN_TEMPLATES: dict[str, dict[str, str]] = {
    "default": {"description": "A blank character to customize.", "system_prompt": _TEMPLATE},
}


def template_id(name: str) -> str:
    """`"tmpl_" + sha256(name)[:32]` — a pure function of the name, never
    `ids.make_id` (which would change on every call) and never stored. Still
    matches `ids.PREFIXES`/`ids._HEX32`'s shape, so it validates anywhere the
    wire format is checked, even though it isn't uuid7-derived."""
    return "tmpl_" + hashlib.sha256(name.encode()).hexdigest()[:32]


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


def _write_character_file(characters_dir: Path, name: str, content: str) -> Path:
    """`name`'s file, written for the first time. Refuses to overwrite: a
    `new` that could silently replace a character someone wrote would be the
    worst possible way to learn where it lives. Shared by `write_template`
    (the CLI's canned starting point) and `create_character` (H26, the
    door's own — a caller-supplied `system_prompt`), which differ only in
    what `content` they pass; the path-allowlist-then-containment check
    (`character_path`) and the refuse-if-exists rule apply identically to
    both."""
    path = character_path(characters_dir, name)
    if path.exists():
        raise persona.CharacterError(f"{path} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_template(characters_dir: Path, name: str) -> Path:
    """Create `name`'s file from the template and return its path. Refuses to
    overwrite: this command's whole job is to make the directory findable,
    and a `new` that could silently replace a character someone wrote would
    be the worst possible way to learn where it lives."""
    return _write_character_file(characters_dir, name, _TEMPLATE)


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


def insert_agent_row(
    c: sqlite3.Connection, name: str, digest: str, at: float, *, state: str = "active", template: str | None = None
) -> tuple[str, int]:
    """Mint an id and insert one new `agents` row, on a connection already
    inside a `write_txn`. Returns `(id, version)` — always `1` for a brand
    new row.

    Extracted from `reconcile_agents`'s own insert branch (H26) because a
    second caller now needs it: `create_character` inserts a row for a
    character the door itself just wrote, with `state="draft"` — everywhere
    else (a file found by the ordinary directory scan) keeps `active`, the
    default this function shares with `reconcile_agents`'s old inline
    behaviour."""
    agent_id = ids.make_id("agt")
    c.execute(
        "INSERT INTO agents (id, name, state, template, created_at, updated_at, content_sha256) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent_id, name, state, template, at, at, digest),
    )
    ledger.record_change(c, noun="agents", id=agent_id, kind="created", state=state, version=1, at=at)
    return agent_id, 1


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

    **Reads `agents` only inside its own `write_txn`, never on a bare
    `conn.execute` first.** `reconcile_agents` used to compare against a
    read taken before the transaction — harmless when the only caller was a
    single-threaded process start (`stores.reconcile_indexes`), but H26 also
    calls this from every `agents.list`/`agents.get` request on the door's
    `ThreadingHTTPServer`, each thread sharing the one writer connection.
    An unlocked read on that connection while another thread is mid-`write_txn`
    is exactly the hazard CLAUDE.md's H16 rule names: it can run inside that
    other thread's open transaction and see rows it may still roll back.
    Folding the comparison read into the transaction below — a no-op
    `BEGIN IMMEDIATE`/`COMMIT` when nothing changed — costs the same
    microseconds `write_txn` already costs on any other empty-write call.
    """
    at = time.time()
    found = {
        character.name: hashlib.sha256(character_path(characters_dir, character.name).read_bytes()).hexdigest()
        for character in list_characters(characters_dir)
    }
    with write_txn(conn) as c:
        known = {
            row["name"]: (row["id"], row["content_sha256"], row["version"])
            for row in c.execute("SELECT id, name, content_sha256, version FROM agents")
        }
        if found == {name: digest for name, (_id, digest, _v) in known.items()}:
            return
        for name, digest in found.items():
            if name not in known:
                insert_agent_row(c, name, digest, at)
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


#: What a door-created character's frontmatter `description:` falls back to
#: when none was given — `persona.parse` requires a non-empty one, and a
#: console caller's `system_prompt` has no format contract of its own to
#: supply it from.
_DEFAULT_AGENT_DESCRIPTION = "A voice created through the console."


def _character_file_text(system_prompt: str, description: str) -> str:
    """Wrap a console-supplied `system_prompt` — plain text, no `---`
    frontmatter of its own — in the shape `persona.parse` requires (H26).

    Without this, `create_character`/`update_character` writing
    `system_prompt` verbatim would produce a file `list_characters` skips as
    unparseable, and the very next `reconcile_agents` pass — including the
    one `door/nouns/agents.py` runs on every read — would read "no file
    matches this row" and delete it. Found by `test_activate_requires_draft_state`
    failing with `404` instead of `409`: the second `activate` call's own
    state gate first re-`get`s the row, and reconciliation had already
    removed it.

    `description` becomes both the door's own `agents.description` column
    and this frontmatter line — one value, not two independently-tracked
    descriptions of the same character.
    """
    label = description.strip() or _DEFAULT_AGENT_DESCRIPTION
    return f"---\ndescription: {label}\n---\n{system_prompt}\n"


@dataclass(frozen=True)
class AgentRow:
    """One `agents` row, in full — the identity/state fields
    `list_characters`' parsed `Character` doesn't carry (H26)."""

    id: str
    name: str
    description: str
    state: str
    template: str | None
    created_at: float
    updated_at: float
    version: int
    content_sha256: str


_AGENT_ROW_COLUMNS = "id, name, description, state, template, created_at, updated_at, version, content_sha256"


def _agent_row(row: sqlite3.Row) -> AgentRow:
    return AgentRow(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        state=row["state"],
        template=row["template"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
        content_sha256=row["content_sha256"],
    )


def list_agent_rows(conn: sqlite3.Connection) -> tuple[AgentRow, ...]:
    """Every `agents` row, in index order. Read straight off the index —
    call `reconcile_agents` first if the caller needs it caught up with the
    directory (`door/nouns/agents.py` always does, per H16's own note on
    live reconciliation)."""
    rows = conn.execute(f"SELECT {_AGENT_ROW_COLUMNS} FROM agents WHERE id IS NOT NULL ORDER BY name").fetchall()
    return tuple(_agent_row(row) for row in rows)


def get_agent_row(conn: sqlite3.Connection, agent_id: str) -> AgentRow | None:
    row = conn.execute(f"SELECT {_AGENT_ROW_COLUMNS} FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return _agent_row(row) if row is not None else None


def read_character_text(characters_dir: Path, name: str) -> str:
    """The file's raw text — frontmatter and body, byte for byte — which is
    what the door renders as `system_prompt` (H26). `character_path` still
    does the allowlist-then-containment check; this is `load_character`
    without the `persona.parse` step, for a caller that wants the file as
    written rather than as interpreted."""
    path = character_path(characters_dir, name)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise persona.CharacterError(f"no character named {name!r} at {path}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise persona.CharacterError(f"{path} could not be read: {e}") from e


def create_character(
    conn: sqlite3.Connection,
    characters_dir: Path,
    name: str,
    content: str,
    *,
    template: str | None = None,
    description: str = "",
) -> AgentRow:
    """The door's own `agents.create` (H26): write the file through the same
    path rules `write_template` uses (`_write_character_file` — allowlist
    then containment, refuses to overwrite), then insert its `agents` row
    directly as `state="draft"` rather than waiting for `reconcile_agents`
    to find it, which would insert it as `active`.

    Inserting directly, not calling `reconcile_agents`, is what makes the
    `draft` state stick: the row's `content_sha256` already matches the file
    the instant this returns, so the next reconciliation pass — including
    the one `door/nouns/agents.py` runs on every read — sees nothing to
    reconcile and leaves the state alone. Only `set_agent_state` ever moves
    it to `active`.

    `content` is wrapped via `_character_file_text` before it touches disk —
    see that function's docstring for why a console-supplied `system_prompt`
    cannot be written verbatim.
    """
    effective_description = description.strip() or _DEFAULT_AGENT_DESCRIPTION
    path = _write_character_file(characters_dir, name, _character_file_text(content, effective_description))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    at = time.time()
    with write_txn(conn) as c:
        agent_id, version = insert_agent_row(c, name, digest, at, state="draft", template=template)
        c.execute("UPDATE agents SET description = ? WHERE id = ?", (effective_description, agent_id))
    row = get_agent_row(conn, agent_id)
    assert row is not None, f"agent {agent_id} vanished immediately after insert"
    return row


def update_character(
    conn: sqlite3.Connection,
    characters_dir: Path,
    name: str,
    *,
    content: str | None = None,
    description: str | None = None,
) -> AgentRow:
    """The door's own `agents.update` (H26): rewrite the file (if `content`
    is given) and/or the `description` column, then bump the row: new hash,
    `version + 1`, `updated_at`, a `changed` ledger row. A `PATCH` naming
    neither is a no-op — returned unchanged, nothing bumped.

    The file rewrite is a temp-file-then-`os.replace`, this codebase's own
    existing atomic-replace idiom (`plugin_install.py`'s own installs) — a
    crash mid-write must never leave a half-written character behind for
    the next read to trip over. Requires the file to already exist (the
    inverse of `write_template`/`create_character`'s refuse-to-overwrite);
    `character_path` still runs the allowlist-then-containment check first.

    `content`, like `create_character`'s, is wrapped via
    `_character_file_text` before it touches disk — using the *new*
    description if this same call also gave one, else the row's current one,
    so the file never regresses to `_DEFAULT_AGENT_DESCRIPTION` just because
    only its content changed. A `description`-only update never rewrites the
    file: the two frontmatter/column values are only forced to agree at the
    moment content changes, not kept in permanent lockstep.
    """
    path = character_path(characters_dir, name)
    if not path.exists():
        raise persona.CharacterError(f"no character named {name!r} at {path}")
    if content is None and description is None:
        # Same hazard `reconcile_agents` names above: an empty-body PATCH
        # reaches this branch on the door's shared writer connection, so
        # this read goes through `write_txn` too, not a bare `conn.execute`.
        with write_txn(conn) as c:
            row = c.execute("SELECT id FROM agents WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise persona.CharacterError(f"{name!r} has no agents row to update")
            existing = get_agent_row(c, row["id"])
            assert existing is not None
            return existing
    at = time.time()
    with write_txn(conn) as c:
        row = c.execute("SELECT id, version, description FROM agents WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise persona.CharacterError(f"{name!r} has no agents row to update")
        agent_id, version, current_description = row["id"], row["version"], row["description"]
        if content is not None:
            effective_description = description if description is not None else current_description
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(_character_file_text(content, effective_description), encoding="utf-8")
            os.replace(tmp, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if description is not None:
            c.execute(
                "UPDATE agents SET content_sha256 = ?, updated_at = ?, version = version + 1, description = ? "
                "WHERE id = ?",
                (digest, at, description, agent_id),
            )
        else:
            c.execute(
                "UPDATE agents SET content_sha256 = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                (digest, at, agent_id),
            )
        ledger.record_change(c, noun="agents", id=agent_id, kind="changed", state=None, version=version + 1, at=at)
    updated = get_agent_row(conn, agent_id)
    assert updated is not None, f"agent {agent_id} vanished mid-update"
    return updated


def set_agent_state(conn: sqlite3.Connection, agent_id: str, state: str) -> AgentRow:
    """Flip an agent's state (H26's `activate` action calls this with
    `"active"`). Never touches the file — this is a console-only concept
    `reconcile_agents` has no reason to know about (spec.md's own rejected
    alternative: teaching reconciliation about a person's decision would
    couple a pure file-sync pass to something it has no other reason to
    know)."""
    if state not in AGENT_STATES:
        raise ValueError(f"{state!r} is not a known agent state; the closed set is persona_store.AGENT_STATES")
    at = time.time()
    with write_txn(conn) as c:
        row = c.execute("SELECT version FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise persona.CharacterError(f"no agent {agent_id!r}")
        version = row["version"]
        c.execute(
            "UPDATE agents SET state = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (state, at, agent_id),
        )
        ledger.record_change(c, noun="agents", id=agent_id, kind="changed", state=state, version=version + 1, at=at)
    updated = get_agent_row(conn, agent_id)
    assert updated is not None, f"agent {agent_id} vanished mid-update"
    return updated


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
