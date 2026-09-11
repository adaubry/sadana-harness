"""Tests for sadana.builtin_seed — placing the plugins shipped in this package.

These moved out of `test_memory_store.py` with the code they cover, when
`web-search` became the second first-party plugin needing exactly this and the
copy-and-rename stopped belonging to the memory store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sadana.builtin_seed import SOURCE_ROOT, seed, seed_all


@pytest.mark.unit
def test_seed_materializes_a_whole_plugin_directory(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    seed(plugins_root, "memory")
    assert (plugins_root / "memory" / "plugin.toml").is_file()
    assert (plugins_root / "memory" / "init.py").is_file()
    assert (plugins_root / "memory" / "schema" / "remember.json").is_file()


@pytest.mark.unit
def test_seed_never_overwrites_what_is_already_there(tmp_path: Path) -> None:
    """Idempotent, and specifically: a person's local edit survives."""
    plugins_root = tmp_path / "plugins"
    seed(plugins_root, "memory")
    marker = plugins_root / "memory" / "plugin.toml"
    original = marker.read_text(encoding="utf-8")
    marker.write_text(original + "\n# local edit\n", encoding="utf-8")

    seed(plugins_root, "memory")

    assert marker.read_text(encoding="utf-8") == original + "\n# local edit\n"


@pytest.mark.unit
def test_a_failed_seed_leaves_nothing_behind_at_all(tmp_path: Path) -> None:
    """Not just the named destination: a copy that fails half-way used to
    strand its staging directory in the plugins root forever, because only the
    rename was guarded."""
    plugins_root = tmp_path / "plugins"
    with pytest.raises(OSError):
        seed(plugins_root, "not-a-shipped-plugin")
    assert not (plugins_root / "not-a-shipped-plugin").exists()
    assert list(plugins_root.iterdir()) == []


@pytest.mark.unit
def test_seed_does_not_ship_this_packages_build_artifacts(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    seed(plugins_root, "memory")
    assert not list((plugins_root / "memory").rglob("__pycache__"))


@pytest.mark.unit
def test_seed_all_places_every_plugin_shipped_in_this_package(tmp_path: Path) -> None:
    """Asserted as a relationship, not a snapshot: whatever ships is what gets
    seeded, so adding a plugin cannot silently fail to reach the plugins root."""
    plugins_root = tmp_path / "plugins"
    seed_all(plugins_root)

    shipped = {d.name for d in SOURCE_ROOT.iterdir() if d.is_dir()}
    assert shipped  # the package really does ship some
    for name in shipped:
        assert (plugins_root / name / "plugin.toml").is_file()


@pytest.mark.unit
def test_everything_seed_all_places_is_a_valid_plugin(tmp_path: Path) -> None:
    """The quiet failure this guards: `discover_plugins()` silently excludes
    an invalid plugin, so a shipped one that does not validate simply never
    appears and nothing says why."""
    from sadana.plugin_manifest import discover_plugins

    plugins_root = tmp_path / "plugins"
    seed_all(plugins_root)

    shipped = {d.name for d in SOURCE_ROOT.iterdir() if d.is_dir()}
    assert {p.name for p in discover_plugins(plugins_root)} == shipped
