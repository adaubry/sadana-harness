"""The box's own enrolled identity, persisted across restarts.

`docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md`. I/O — its own
file. `state_dir/tether/harness.toml`, one flat table, read with the
standard library's `tomllib` and written by hand (a small, hand-written
writer earns its keep for one flat table with five fields — a full TOML
library would be a second runtime dependency for what this is).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from sadana import config

_FILENAME = "harness.toml"


@dataclass(frozen=True)
class Identity:
    harness_id: str
    org: str | None
    relay_url: str
    console_url: str | None
    enrolled_at: float


def _path() -> Path:
    return config.get_paths().state_dir / "tether" / _FILENAME


def load() -> Identity | None:
    """`None` means "not enrolled yet" — never an exception. Every other
    failure to read a well-formed file (a missing required key, bad TOML)
    is a real bug in whatever wrote it and is allowed to raise."""
    path = _path()
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return Identity(
        harness_id=data["harness_id"],
        org=data.get("org"),
        relay_url=data["relay_url"],
        console_url=data.get("console_url"),
        enrolled_at=data["enrolled_at"],
    )


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(identity: Identity) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"harness_id = {_quoted(identity.harness_id)}"]
    if identity.org is not None:
        lines.append(f"org = {_quoted(identity.org)}")
    lines.append(f"relay_url = {_quoted(identity.relay_url)}")
    if identity.console_url is not None:
        lines.append(f"console_url = {_quoted(identity.console_url)}")
    lines.append(f"enrolled_at = {identity.enrolled_at!r}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove() -> None:
    """Deletes the identity file. Never raises for "already gone" — a
    deregister that runs twice, or against a box that never enrolled, is
    not an error."""
    _path().unlink(missing_ok=True)
