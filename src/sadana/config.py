"""The one way to ask sadana-harness for a configuration value.

This module ships four things:

``Paths`` / ``get_paths()`` — the two values that belong to no single block
(``state_dir``, ``config_dir``), resolved fresh from the environment on
every call. No caching: nothing here is expensive enough to justify stored
state that could go stale between two calls.

``env`` / ``env_bool`` / ``env_int`` / ``env_path`` — the resolution
primitive every existing block-owned env var still uses. A value is the
given default if its environment variable is unset, the parsed value if it
is set and valid, and a raised ``ValueError`` if it is set but not
parseable as its declared type — a bad value should fail loudly at the one
call site that reads it, not silently fall back.

``get(key, default)`` — the resolution primitive for a *behavior* value
with a user-facing meaning (H14,
`docs/tasks/H14-tuned-config-settings-secrets/spec.md`): read from
``config_dir/config.toml``, a real ``SADANA_<UPPER>_<UPPER>`` environment
variable still winning over the file, exactly as it always has. Reading a
value moved to ``get`` no longer requires a restart to see a change — the
next call re-reads the file the moment its ``(mtime, size)`` changes.

``secret(name)`` / ``bind_secret_reader(reader)`` — the resolution
primitive for a value nothing may ever echo back. A real environment
variable still wins; otherwise the bound reader (``env_file.read_key`` in
production) is asked, fresh, every call. Bound once at each real entrypoint
(``cli.main()``, ``client_surface.open_runtime()``) — calling ``secret()``
before that binding exists is a startup-ordering bug, not a value to guess
at, and raises.

Env vars for a block-owned value should be named ``SADANA_<BLOCK>_<FIELD>``
(e.g. ``SADANA_MODEL_ACCESS_TIMEOUT_S``) so names stay collision-free as
more blocks are added, and so the same name doubles as the override for the
matching dotted ``get``/``secret`` key. Nothing enforces that convention in
code.

This module imports nothing from any other part of sadana-harness.
"""

from __future__ import annotations

import copy
import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def load_dotenv() -> None:
    """Confirm ``state_dir/.env`` is readable, and nothing more.

    Used to copy every ``KEY=value`` line into ``os.environ`` — that made a
    secret's value indistinguishable from a real environment variable to
    everything downstream (a log dump, a subprocess's inherited
    environment) the moment ``.env`` was read once. ``config.secret`` is the
    read path now; nothing in this codebase reads ``os.environ`` expecting
    ``.env``'s contents to already be there. Still opens and reads the file
    at the same startup call site as before — a permissions or encoding
    problem with ``.env`` still surfaces immediately at startup, not on the
    first turn that happens to need a credential.
    """
    dotenv = get_paths().state_dir / ".env"
    if not dotenv.is_file():
        return
    dotenv.read_text(encoding="utf-8")


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


#: ``str(path) -> (mtime_ns, size, parsed)``. Keyed on the path's string
#: rather than assuming one fixed location, the same posture ``get_paths()``
#: itself takes (nothing here is expensive enough to justify assuming the
#: path never changes across calls in one process, e.g. across tests).
_CONFIG_CACHE: dict[str, tuple[int, int, dict[str, object]]] = {}


def raw_toml() -> dict[str, object]:
    """``config_dir/config.toml``, parsed, cached on ``(mtime_ns, size)`` —
    hermes's own proven cache key for exactly this problem
    (`hermes_cli/config.py`'s ``read_raw_config``). A cache hit returns a
    deep copy, never the cached dict itself, so a caller mutating what it
    got back can never corrupt what the next caller sees.

    An absent file is an empty config, not an error — every dotted key
    simply falls through to its own default. A file that exists but does
    not parse raises: `config.env_int`'s own docstring already commits this
    module to failing loudly on a bad value rather than guessing, and a
    malformed file is exactly that, just for every key in it at once.

    Public: every door noun's own ``update`` that writes a config value
    reads the current file through this same function before handing it to
    ``door.config_writer.apply`` — one read primitive, not one per noun."""
    path = get_paths().config_dir / "config.toml"
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    cached = _CONFIG_CACHE.get(str(path))
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return copy.deepcopy(cached[2])
    with open(path, "rb") as f:
        data = tomllib.load(f)
    _CONFIG_CACHE[str(path)] = (st.st_mtime_ns, st.st_size, copy.deepcopy(data))
    return data


def get(key: str, default: T, *, env_name: str | None = None) -> T:
    """A behavior value with a user-facing meaning: ``key`` is a dotted
    path (``"model_access.timeout_s"``) read from ``config_dir/config.toml``
    as nested tables (``[model_access]\\ntimeout_s = ...``); a missing file
    or a missing key both fall through to ``default``.

    ``SADANA_<UPPER>_<UPPER>`` — the same env var a block-owned value has
    always used, unchanged — wins over the file when set. The type of
    ``default`` decides how that environment string is coerced, the same
    contract ``env_bool``/``env_int`` already keep; a config-file value is
    used exactly as TOML parsed it, with no coercion of its own.

    ``env_name`` overrides the derived name for a value whose environment
    variable predates and does not fit that convention — a per-plugin
    setting's own ``SADANA_PLUGIN__<PLUGIN>__<SETTING>`` (double
    underscore, a fixed literal name, not derivable from a dotted
    ``plugins.<plugin>.<setting>`` key) is the one caller today
    (`plugins.read_setting`); nothing else needs it.

    Nothing here is cached beyond the file's own ``(mtime, size)``: a value
    changed through the door and written to disk is seen on the very next
    call, in this or any other process, with no restart."""
    if env_name is None:
        env_name = "SADANA_" + key.upper().replace(".", "_")
    raw_env = os.environ.get(env_name)
    if raw_env is not None:
        if isinstance(default, bool):
            return env_bool(env_name, default)  # type: ignore[return-value]
        if isinstance(default, int):
            return env_int(env_name, default)  # type: ignore[return-value]
        if isinstance(default, float):
            try:
                return float(raw_env)  # type: ignore[return-value]
            except ValueError as exc:
                raise ValueError(f"{env_name}={raw_env!r} is not a valid float") from exc
        return raw_env  # type: ignore[return-value]
    node: object = raw_toml()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node  # type: ignore[return-value]


_secret_reader: Callable[[str], str | None] | None = None


def bind_secret_reader(reader: Callable[[str], str | None]) -> None:
    """Bind the fallback ``secret()`` calls when no real environment
    variable answers. Called exactly once per process, at each real
    entrypoint (``cli.main()``, ``client_surface.open_runtime()``) — this
    module stays import-free by never reaching for ``env_file.read_key``
    itself; the caller hands it in instead."""
    global _secret_reader
    _secret_reader = reader


def secret(name: str) -> str | None:
    """``name``'s value: a real environment variable if one is set — which
    is what "the environment always wins" means for a secret, now that
    ``load_dotenv`` no longer copies anything into ``os.environ`` — else
    whatever the bound reader answers, asked fresh on every call so a
    rewritten credential file is seen on the very next call.

    Raises if nothing has called ``bind_secret_reader`` yet: reaching this
    before either real entrypoint has run is a startup-ordering bug worth
    hearing about, never a value worth guessing at."""
    value = os.environ.get(name)
    if value is not None:
        return value
    if _secret_reader is None:
        raise RuntimeError(f"config.secret({name!r}) called before config.bind_secret_reader()")
    return _secret_reader(name)
