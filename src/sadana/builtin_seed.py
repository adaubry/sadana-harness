"""Materialising a first-party plugin into the plugins root.

`docs/tasks/WEB-SEARCH-01-the-first-plugin-that-reaches-the-outside-world
/spec.md`. Lifted unchanged out of `memory_store.ensure_plugin_seeded`, which
now calls it: `memory` and `web-search` both ship inside this package and both
need placing where `discover_plugins()` already looks, and nine lines of
copy-into-staging-then-rename is not worth writing twice.

A second real member is the whole justification (CLAUDE.md: a seam "earns its
cost only once a second real member exists"). This is not a plugin-distribution
mechanism — `PLUGIN-INSTALL-01` owns that — only the shipped-with-the-package
case.

Its own file because it touches the disk on every call.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent / "builtin_plugins"


def seed(plugins_root: Path, name: str) -> None:
    """If `plugins_root / name` doesn't exist yet, copy the shipped plugin
    source there — the same "if missing, write the default" idiom
    `persona.py:load_or_seed_persona` already uses for one file, extended to a
    directory tree. A no-op on every call after the first.

    Copies into a sibling temp directory first and renames it into place, so a
    process killed mid-copy never leaves a half-written directory for
    `discover_plugins()` to trip over — the rename is one atomic filesystem
    op. Not guarding against a second *process* racing this one: this
    project's posture is single-writer until a real concurrent-caller incident
    shows up (CLAUDE.md), not before.
    """
    destination = plugins_root / name
    if destination.exists():
        return
    plugins_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=plugins_root))
    try:
        # `__pycache__` is a build artifact of this package, not part of the
        # plugin; copying it ships bytecode into a person's state directory.
        shutil.copytree(SOURCE_ROOT / name, staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        staging.rename(destination)
    except OSError:
        # Inside the `try` with the copy, not only the rename: a copy that
        # fails half-way would otherwise leave its staging directory behind in
        # the plugins root forever.
        shutil.rmtree(staging, ignore_errors=True)
        raise


def seed_all(plugins_root: Path) -> None:
    """Every plugin shipped inside this package.

    The set is the directory listing rather than a list anyone maintains, and
    that is the point: a shipped plugin nobody remembered to seed is silently
    absent — `discover_plugins()` finds nothing and says nothing — which is
    the same quiet failure mode `web-search` nearly shipped with when its name
    would not have validated.
    """
    for source in sorted(p for p in SOURCE_ROOT.iterdir() if p.is_dir()):
        seed(plugins_root, source.name)
