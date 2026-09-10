"""Tests for sadana.plugin_install: register(), resolve(), install()."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from conftest import make_upstream_repo as _make_upstream_repo
from conftest import run_git as _run_git
from sadana import plugin_install, plugin_manifest
from sadana.conversation_store import open_store, store_path_from_config


def _retag(repo: Path, *, plugin_name: str, tag: str, content: str) -> None:
    """Adds a second commit and moves `tag` onto it — used to prove
    `--replace` actually lands new content, not a stale clone."""
    (repo / "plugin.toml").write_text(
        f'[plugin]\nname = "{plugin_name}"\nversion = "{tag}"\ndescription = "{content}"\n'
    )
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "update"], cwd=repo)
    _run_git(["tag", "-f", tag], cwd=repo)


def _conn():
    return open_store(store_path_from_config())


@pytest.mark.unit
def test_register_then_resolve_finds_the_url() -> None:
    conn = _conn()
    try:
        outcome = plugin_install.register(conn, "greeter", "https://example.invalid/greeter.git", now=time.time())
        assert outcome == plugin_install.Registered(name="greeter")
        assert plugin_install.resolve(conn, "greeter") == "https://example.invalid/greeter.git"
    finally:
        conn.close()


@pytest.mark.unit
def test_register_twice_returns_name_taken_and_keeps_the_first_url() -> None:
    conn = _conn()
    try:
        plugin_install.register(conn, "greeter", "https://example.invalid/first.git", now=time.time())
        outcome = plugin_install.register(conn, "greeter", "https://example.invalid/second.git", now=time.time())
        assert outcome == plugin_install.NameTaken(name="greeter")
        assert plugin_install.resolve(conn, "greeter") == "https://example.invalid/first.git"
    finally:
        conn.close()


@pytest.mark.unit
def test_resolve_unknown_name_returns_none() -> None:
    conn = _conn()
    try:
        assert plugin_install.resolve(conn, "does-not-exist") is None
    finally:
        conn.close()


@pytest.mark.unit
def test_install_places_a_verified_plugin_under_plugins_root(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        plugin_install.register(conn, "greeter", str(repo), now=time.time())
        outcome = plugin_install.install(conn, "greeter", "v1.0.0", plugins_root=plugins_root)
    finally:
        conn.close()

    assert isinstance(outcome, plugin_install.Installed)
    assert outcome.name == "greeter"
    assert outcome.directory == plugins_root / "greeter"
    assert (plugins_root / "greeter" / "plugin.toml").is_file()


@pytest.mark.unit
def test_install_unknown_name_is_refused_without_touching_disk(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        outcome = plugin_install.install(conn, "nobody-registered-this", "v1.0.0", plugins_root=plugins_root)
    finally:
        conn.close()

    assert outcome == plugin_install.UnknownPluginName(name="nobody-registered-this")
    assert not plugins_root.exists()


@pytest.mark.unit
def test_install_twice_without_replace_is_refused(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        plugin_install.register(conn, "greeter", str(repo), now=time.time())
        first = plugin_install.install(conn, "greeter", "v1.0.0", plugins_root=plugins_root)
        second = plugin_install.install(conn, "greeter", "v1.0.0", plugins_root=plugins_root)
    finally:
        conn.close()

    assert isinstance(first, plugin_install.Installed)
    assert second == plugin_install.AlreadyInstalled(name="greeter")


@pytest.mark.unit
def test_install_with_replace_lands_the_new_tags_content(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        plugin_install.register(conn, "greeter", str(repo), now=time.time())
        plugin_install.install(conn, "greeter", "v1.0.0", plugins_root=plugins_root)
        _retag(repo, plugin_name="greeter", tag="v1.0.0", content="second version")
        outcome = plugin_install.install(conn, "greeter", "v1.0.0", plugins_root=plugins_root, replace=True)
    finally:
        conn.close()

    assert isinstance(outcome, plugin_install.Installed)
    assert "second version" in (plugins_root / "greeter" / "plugin.toml").read_text()


@pytest.mark.unit
def test_install_name_mismatch_when_manifest_declares_a_different_name(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="actually-called-this", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        plugin_install.register(conn, "registered-as-this", str(repo), now=time.time())
        outcome = plugin_install.install(conn, "registered-as-this", "v1.0.0", plugins_root=plugins_root)
    finally:
        conn.close()

    assert outcome == plugin_install.NameMismatch(expected="registered-as-this", found="actually-called-this")
    assert not (plugins_root / "registered-as-this").exists()


@pytest.mark.unit
def test_install_tag_mismatch_when_the_remote_disagrees_with_what_was_cloned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mismatch branch only fires under a real-world race (the tag
    moves between resolving it and cloning it). Reproduced here by
    substituting only the "what the remote claims right now" input —
    the clone, the tag, and the comparison itself are all real."""
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    monkeypatch.setattr(plugin_install, "_resolve_tag_commit", lambda repo_url, tag: "0" * 40)
    conn = _conn()
    try:
        plugin_install.register(conn, "greeter", str(repo), now=time.time())
        outcome = plugin_install.install(conn, "greeter", "v1.0.0", plugins_root=plugins_root)
    finally:
        conn.close()

    assert isinstance(outcome, plugin_install.TagMismatch)
    assert outcome.tag == "v1.0.0"
    assert outcome.expected_revision == "0" * 40
    assert not (plugins_root / "greeter").exists()


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["a" * 40, "not a valid tag"], ids=["bare-commit-sha", "malformed-ref-name"])
def test_install_rejects_a_non_tag_before_resolving_anything(tmp_path: Path, tag: str) -> None:
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        plugin_install.register(conn, "greeter", "https://example.invalid/greeter.git", now=time.time())
        outcome = plugin_install.install(conn, "greeter", tag, plugins_root=plugins_root)
    finally:
        conn.close()
    assert isinstance(outcome, plugin_install.FetchFailed)


@pytest.mark.unit
def test_discover_plugins_finds_a_freshly_installed_plugin_with_no_code_change(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        plugin_install.register(conn, "greeter", str(repo), now=time.time())
        plugin_install.install(conn, "greeter", "v1.0.0", plugins_root=plugins_root)
    finally:
        conn.close()

    installed = plugin_manifest.discover_plugins(plugins_root)
    assert any(p.name == "greeter" for p in installed)
