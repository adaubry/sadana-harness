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
    between creation and any later chmod."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


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
