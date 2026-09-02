"""The one way to ask sadana-harness for a configuration value.

This module ships two things, and is meant to never need a third:

``Paths`` / ``get_paths()`` — the two values that belong to no single block
(``state_dir``, ``config_dir``), resolved fresh from the environment on
every call. No caching: nothing here is expensive enough to justify stored
state that could go stale between two calls.

``env`` / ``env_bool`` / ``env_int`` / ``env_path`` — the resolution
primitive every future block uses to build its *own* typed config in its
*own* module. A value is the given default if its environment variable is
unset, the parsed value if it is set and valid, and a raised ``ValueError``
if it is set but not parseable as its declared type — a bad value should
fail loudly at the one call site that reads it, not silently fall back.

Env vars for a block-owned value should be named ``SADANA_<BLOCK>_<FIELD>``
(e.g. ``SADANA_MODEL_ACCESS_TIMEOUT_S``) so names stay collision-free as
more blocks are added. Nothing enforces that convention in code.

This module imports nothing from any other part of sadana-harness.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env(name: str, default: str) -> str:
    """Return the string value of ``name``, or ``default`` if unset."""
    return os.environ.get(name, default)


def env_bool(name: str, default: bool) -> bool:
    """Return the boolean value of ``name``, or ``default`` if unset.

    Recognizes ``1/true/yes/on`` and ``0/false/no/off`` (case-insensitive).
    Raises ``ValueError`` if set to anything else.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name}={raw!r} is not a recognized boolean")


def env_int(name: str, default: int) -> int:
    """Return the integer value of ``name``, or ``default`` if unset.

    Raises ``ValueError`` if set to something that doesn't parse as ``int``.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a valid integer") from exc


def env_path(name: str, default: Path) -> Path:
    """Return the path value of ``name``, or ``default`` if unset."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return Path(raw)


@dataclass(frozen=True)
class Paths:
    """The filesystem locations that belong to sadana-harness as a whole."""

    state_dir: Path
    config_dir: Path


def get_paths() -> Paths:
    """Resolve ``Paths`` fresh from the current environment.

    ``state_dir``: ``SADANA_STATE_DIR`` if set, else
    ``$XDG_STATE_HOME/sadana``, else ``~/.local/state/sadana``.

    ``config_dir``: ``$XDG_CONFIG_HOME/sadana``, else ``~/.config/sadana``.
    """
    state_default = env_path("XDG_STATE_HOME", default=Path.home() / ".local" / "state") / "sadana"
    config_dir = env_path("XDG_CONFIG_HOME", default=Path.home() / ".config") / "sadana"
    return Paths(
        state_dir=env_path("SADANA_STATE_DIR", default=state_default),
        config_dir=config_dir,
    )
