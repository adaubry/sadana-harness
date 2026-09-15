"""The write half of ``state_dir/.env``.

``config.load_dotenv()`` is the read half of this format; these are the
functions that produce it. They lived inside ``subcommands/setup.py`` while
``sadana setup`` was the only thing that wrote a value
(`docs/tasks/CLI-SHELL-06-setup-first-run/spec.md`). ``sadana plugin set``
is the second writer, so they move here rather than being written twice
(`docs/tasks/PLUGIN-CONFIG-01-settings-and-secrets-a-plugin-owns/spec.md`).

Its own file, not part of ``config.py``: this module touches the disk on
every call, and CLAUDE.md keeps an I/O module separate from a block's
pure-function module. Nothing here prints, prompts, or exits — a caller
turns an outcome into a message, the same division ``plugin_install.py``
already keeps with ``subcommands/plugin.py``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from sadana import config


def env_path() -> Path:
    """``state_dir/.env`` — resolved fresh on every call, never cached, the
    same posture as ``config.get_paths()`` itself."""
    return config.get_paths().state_dir / ".env"


def path_put(path: Path, text: str) -> None:
    """Write ``text`` opening the file with mode 0600 — the file holds
    secrets, so it must never be world-readable even for the instant
    between creation and any later chmod.

    The ``mode`` argument to ``os.open`` is only honored by the OS when
    ``O_CREAT`` actually creates the file — a pre-existing file (created
    with a wider mode by something else before this module ever wrote it)
    keeps its old permissions on open. ``os.chmod`` after the write closes
    that gap unconditionally, whether or not this call created the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def read_key(name: str) -> str | None:
    """The value assigned to ``name`` in ``state_dir/.env``, unquoted, or
    ``None`` if the file or the key is absent.

    Read fresh on every call — this module's own posture, unchanged — so a
    value rewritten out-of-band (a person editing the file, a door ``PUT``)
    is seen on the very next call, no restart, which is what makes
    ``config.secret`` able to promise the same thing. Unquotes exactly the
    way ``config.load_dotenv`` used to and ``quote_env_value`` still
    writes: a value wrapped in a matching pair of double quotes has them
    stripped."""
    path = env_path()
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if env_line_key(line) != name:
            continue
        _, _, raw_value = line.strip().partition("=")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        return value
    return None


def fingerprint(value: str) -> str:
    """A stable, value-free stand-in for ``value``: its last four
    characters, a middle dot, and the first eight hex digits of its
    SHA-256 — enough for a person to recognize *which* credential this is
    without the value ever crossing back over the door (``docs/console/
    wire.md`` §6). Deterministic in both halves, so the same value always
    fingerprints the same way and neither half can be walked backwards to
    recover it."""
    return f"{value[-4:]}·{hashlib.sha256(value.encode()).hexdigest()[:8]}"


def env_line_key(line: str) -> str:
    """Return the var name a line assigns, or ``""`` if it assigns none.
    Matches the reader's normalization: ``load_dotenv`` strips the key's
    whitespace (``src/sadana/config.py``), so ``KEY=`` and ``KEY =`` are the
    same assignment — both are replaced/reused here, never duplicated."""
    key, sep, _ = line.strip().partition("=")
    return key.strip() if sep else ""


def quote_env_value(value: str) -> str:
    """Always double-quote (single quotes are ambiguous to systemd-style
    parsers), and strip newlines so a value can never span physical lines —
    a multi-line value would otherwise inject a second assignment into the
    .env, which the reader splits on physical lines (hermes's
    save_env_value strips \\n/\\r for the same reason)."""
    value = value.replace("\n", "").replace("\r", "")
    return f'"{value}"'


def upsert_key(path: Path, key: str, value: str) -> None:
    """Replace the line assigning ``key`` or append one, leaving other
    lines' content unchanged. Only keys the caller owns are touched."""
    reinsert = f"{key}={quote_env_value(value)}"
    if not path.exists():
        path_put(path, f"{reinsert}\n")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if env_line_key(line) == key:
            out.append(reinsert)
            found = True
        else:
            out.append(line)
    if not found:
        out.append(reinsert)
    path_put(path, "\n".join(out) + "\n")


def drop_key(path: Path, key: str) -> None:
    """Remove every line assigning ``key``. Shares the writer's key
    normalization, so a spaced ``KEY =`` line is removed too."""
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    out = [line for line in lines if env_line_key(line) != key]
    if len(out) == len(lines):
        return
    path_put(path, "\n".join(out) + "\n")
