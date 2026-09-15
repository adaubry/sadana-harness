"""The write half of ``config_dir/config.toml`` (H14,
`docs/tasks/H14-tuned-config-settings-secrets/spec.md`).

Its own file, not part of ``config.py``: this module touches the disk on
every ``write`` call, and CLAUDE.md keeps an I/O module separate from a
block's pure-function module — the same split ``env_file.py`` already
draws against ``config.py`` itself.

Two functions. ``apply`` is pure — a dotted key, a value, and the config
dict that results, never touching disk. ``write`` is the only thing here
that does: emit, read the emission back with ``tomllib``, and only then
replace the file — ``editor_server._write_manifest``'s own round-trip
guard, the same idiom, not a new one. A self-check there once found two
strings a hand-rolled emitter got wrong, and in both cases the damage was
not the bad text, it was that the file had already been overwritten before
the failure surfaced; checking first turns a bad emission into "the write
was refused and the file is untouched" instead.

Supports exactly what this project's own config needs: strings, integers,
floats, booleans, and up to one level of subtables beyond a table's own
top level (``[model_access]``, and ``[providers.openrouter]`` for the one
level deeper a per-provider or per-plugin table needs) — there is no
stdlib TOML writer, and hand-rolling one general enough for arbitrary
nesting nobody asks for would be exactly the speculative infrastructure
CLAUDE.md's own methodology warns against. A value or a nesting depth
outside that set raises ``ValueError``, which every door noun caller turns
into a ``400 VALIDATION``.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sadana.plugins import toml_string

#: The scalar types this emitter can express. `bool` must be checked before
#: `int` at every call site that branches on type, the same trap
#: `config.get`'s own coercion already has to mind — `bool` is a subclass
#: of `int` in Python, so an `isinstance` membership test here (rather than
#: a chain of `isinstance(x, int)` first) is what keeps `True` from being
#: silently emitted as `1`.
_SCALAR_TYPES = (str, int, float, bool)

#: `apply`'s own depth cap, matching `_emit`'s: a table and one level of
#: subtable, never more.
_MAX_TABLE_DEPTH = 2


def apply(current: Mapping[str, Any], key: str, value: object) -> dict[str, Any]:
    """``current`` with ``value`` set at ``key`` (a dotted ``table.field``
    or ``table.subtable.field`` path), as a new dict — ``current`` itself
    is never mutated, so a caller holding onto the old one keeps seeing the
    old one.

    Raises ``ValueError`` for a value of a type this emitter cannot
    express, or a ``key`` with fewer than two segments — every real key in
    this project's config lives under a table, never at the bare root."""
    if not isinstance(value, _SCALAR_TYPES):
        raise ValueError(f"config_writer cannot express a value of type {type(value).__name__} for {key!r}")
    parts = key.split(".")
    if len(parts) < 2:
        raise ValueError(f"{key!r} is not a dotted table.field key")
    updated: dict[str, Any] = _deep_copy(dict(current))
    node = updated
    for part in parts[:-1]:
        existing = node.get(part)
        child = dict(existing) if isinstance(existing, Mapping) else {}
        node[part] = child
        node = child
    node[parts[-1]] = value
    return updated


def _deep_copy(data: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (_deep_copy(v) if isinstance(v, Mapping) else v) for k, v in data.items()}


def _emit_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, int | float):
        return repr(value)
    raise ValueError(f"config_writer cannot express a value of type {type(value).__name__}")


def _emit(data: Mapping[str, Any], *, prefix: tuple[str, ...] = (), depth: int = 0) -> str:
    scalars = [(k, v) for k, v in data.items() if not isinstance(v, Mapping)]
    tables = [(k, v) for k, v in data.items() if isinstance(v, Mapping)]
    lines: list[str] = []
    if prefix:
        lines.append(f"[{'.'.join(prefix)}]")
    lines.extend(f"{k} = {_emit_scalar(v)}" for k, v in scalars)
    sections = ["\n".join(lines)] if lines else []
    for name, table in tables:
        if depth >= _MAX_TABLE_DEPTH:
            raise ValueError(
                f"config_writer supports at most one level of subtables; "
                f"{'.'.join((*prefix, name))} nests deeper than that"
            )
        sections.append(_emit(table, prefix=(*prefix, name), depth=depth + 1))
    return "\n\n".join(section for section in sections if section)


def write(path: Path, data: Mapping[str, Any]) -> None:
    """Emit ``data`` as TOML, read the emission back, and only then replace
    ``path`` — never a partial write, and never a write that ``config.get``
    could not read back exactly the way it was handed here.

    Raises ``ValueError`` — for an unsupported type or nesting depth from
    ``_emit`` itself, or because the round trip disagreed — with nothing on
    disk touched either way."""
    text = _emit(data)
    if text:
        text += "\n"
    reread = tomllib.loads(text) if text else {}
    if reread != _deep_copy(data):
        raise ValueError("config_writer's own emission could not be read back faithfully; nothing was changed")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
