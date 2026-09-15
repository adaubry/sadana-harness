"""Tests for sadana.plugin_install: register(), resolve(), install(),
fetch_verified_tag()."""

from __future__ import annotations

import shutil
import time
from contextlib import closing
from pathlib import Path

import pytest

from conftest import make_upstream_repo as _make_upstream_repo
from conftest import open_conn as _conn
from conftest import run_git as _run_git
from sadana import ledger, plugin_install, plugin_manifest


def _retag(repo: Path, *, plugin_name: str, tag: str, content: str) -> None:
    """Adds a second commit and moves `tag` onto it — used to prove
    `--replace` actually lands new content, not a stale clone."""
    (repo / "plugin.toml").write_text(
        f'[plugin]\nname = "{plugin_name}"\nversion = "{tag}"\ndescription = "{content}"\n'
    )
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "update"], cwd=repo)
    _run_git(["tag", "-f", tag], cwd=repo)


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
def test_fetch_verified_tag_clones_into_dest_and_reports_the_revision(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    dest = tmp_path / "clone"

    outcome = plugin_install.fetch_verified_tag(str(repo), "v1.0.0", dest)

    assert isinstance(outcome, plugin_install.FetchedTag)
    assert outcome.directory == dest
    assert (dest / "plugin.toml").is_file()


@pytest.mark.unit
def test_fetch_verified_tag_unknown_tag_is_fetch_failed(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    outcome = plugin_install.fetch_verified_tag(str(repo), "no-such-tag", tmp_path / "clone")
    assert isinstance(outcome, plugin_install.FetchFailed)


@pytest.mark.unit
def test_fetch_verified_tag_a_repo_url_starting_with_dash_is_never_read_as_a_git_option(tmp_path: Path) -> None:
    """A caller-supplied `repo_url` reaching `git` as a bare positional
    let a value like `--upload-pack=<command>` be parsed as an option
    instead of a repository, running `<command>` for real — reproduced
    live during PLUGIN-MARKET-01's cold review. The `--` separator in
    `fetch_verified_tag()`/`_resolve_tag_commit()` is the fix; this test
    proves the sentinel command genuinely never runs, not just that the
    outcome type looks like a normal failure."""
    sentinel = tmp_path / "should-not-exist"
    malicious_repo_url = f"--upload-pack=touch {sentinel}; #"

    outcome = plugin_install.fetch_verified_tag(malicious_repo_url, "v1.0.0", tmp_path / "clone")

    assert isinstance(outcome, plugin_install.FetchFailed)
    assert not sentinel.exists()


@pytest.mark.unit
def test_fetch_verified_tag_mismatch_when_remote_disagrees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    monkeypatch.setattr(plugin_install, "_resolve_tag_commit", lambda repo_url, tag: "0" * 40)

    outcome = plugin_install.fetch_verified_tag(str(repo), "v1.0.0", tmp_path / "clone")

    assert isinstance(outcome, plugin_install.TagMismatch)
    assert outcome.expected_revision == "0" * 40


@pytest.mark.unit
def test_describe_fetch_failure_names_both_revisions_for_a_mismatch() -> None:
    outcome = plugin_install.TagMismatch(tag="v1.0.0", expected_revision="a" * 40, actual_revision="b" * 40)
    description = plugin_install.describe_fetch_failure(outcome)
    assert "a" * 40 in description
    assert "b" * 40 in description


@pytest.mark.unit
def test_describe_fetch_failure_passes_through_a_fetch_failed_detail() -> None:
    outcome = plugin_install.FetchFailed(detail="git is not installed or not in PATH")
    assert plugin_install.describe_fetch_failure(outcome) == "git is not installed or not in PATH"


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
def test_install_from_git_discovers_the_name_from_the_manifest(tmp_path: Path) -> None:
    """No `register()` call anywhere — the console's own create/
    install-from-git reaches this with only a repository and a tag."""
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        outcome = plugin_install.install_from_git(conn, str(repo), "v1.0.0", plugins_root=plugins_root)
    finally:
        conn.close()

    assert isinstance(outcome, plugin_install.Installed)
    assert outcome.name == "greeter"
    assert outcome.directory == plugins_root / "greeter"
    assert (plugins_root / "greeter" / "plugin.toml").is_file()


@pytest.mark.unit
def test_install_from_git_expect_name_mismatch_is_refused_without_touching_disk(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="actually-called-this", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        outcome = plugin_install.install_from_git(
            conn, str(repo), "v1.0.0", plugins_root=plugins_root, expect_name="expected-this-instead"
        )
    finally:
        conn.close()

    assert outcome == plugin_install.NameMismatch(expected="expected-this-instead", found="actually-called-this")
    assert not (plugins_root / "actually-called-this").exists()
    assert not (plugins_root / "expected-this-instead").exists()


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["a" * 40, "not a valid tag"], ids=["bare-commit-sha", "malformed-ref-name"])
def test_install_from_git_rejects_a_non_tag_before_fetching_anything(tmp_path: Path, tag: str) -> None:
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        outcome = plugin_install.install_from_git(
            conn, "https://example.invalid/greeter.git", tag, plugins_root=plugins_root
        )
    finally:
        conn.close()

    assert isinstance(outcome, plugin_install.FetchFailed)
    assert not plugins_root.exists()


@pytest.mark.unit
def test_install_from_git_already_installed_without_replace_is_refused(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        first = plugin_install.install_from_git(conn, str(repo), "v1.0.0", plugins_root=plugins_root)
        second = plugin_install.install_from_git(conn, str(repo), "v1.0.0", plugins_root=plugins_root)
    finally:
        conn.close()

    assert isinstance(first, plugin_install.Installed)
    assert second == plugin_install.AlreadyInstalled(name="greeter")


@pytest.mark.unit
def test_install_from_git_with_replace_lands_the_new_tags_content(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    conn = _conn()
    try:
        plugin_install.install_from_git(conn, str(repo), "v1.0.0", plugins_root=plugins_root)
        _retag(repo, plugin_name="greeter", tag="v1.0.0", content="second version")
        outcome = plugin_install.install_from_git(conn, str(repo), "v1.0.0", plugins_root=plugins_root, replace=True)
    finally:
        conn.close()

    assert isinstance(outcome, plugin_install.Installed)
    assert "second version" in (plugins_root / "greeter" / "plugin.toml").read_text()


@pytest.mark.unit
def test_set_state_writes_the_row_and_records_a_change(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    with closing(_conn()) as conn:
        plugin_install.install_from_git(conn, str(repo), "v1.0.0", plugins_root=plugins_root)
        before = conn.execute("SELECT id, version FROM plugin_state WHERE name = 'greeter'").fetchone()

        plugin_install.set_state(conn, before["id"], "disabled", now=time.time())

        after = conn.execute("SELECT state, version FROM plugin_state WHERE id = ?", (before["id"],)).fetchone()
        changes = ledger.changes_since(conn, 0, 10)

    assert (after["state"], after["version"]) == ("disabled", before["version"] + 1)
    assert (before["id"], "changed", "disabled") in {(c.id, c.kind, c.state) for c in changes}


@pytest.mark.unit
def test_set_state_unknown_id_is_a_silent_no_op(tmp_path: Path) -> None:
    with closing(_conn()) as conn:
        plugin_install._ensure_schema(conn)
        plugin_install.set_state(conn, "plg_does_not_exist", "disabled", now=time.time())
        assert conn.execute("SELECT * FROM plugin_state").fetchone() is None


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


@pytest.mark.unit
def test_register_refuses_a_name_that_is_not_a_safe_name() -> None:
    """A registered name is a long-lived key that later becomes a directory
    under the plugins root. Checked at the door it enters by, so no unsafe
    name is ever in the registry to be resolved later."""
    with closing(_conn()) as conn:
        outcome = plugin_install.register(conn, "../evil", "https://example.invalid/x.git", now=1.0)
    assert isinstance(outcome, plugin_install.InvalidName)


@pytest.mark.unit
def test_install_never_writes_outside_the_plugins_root(tmp_path: Path) -> None:
    """`plugins_root / name` with an unchecked name escapes the root, and
    the write is an `os.replace` over whatever is there. Both halves of
    CLAUDE.md's rule are needed: the pattern at the door, and the resolved
    path re-checked before anything is moved."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("do not clobber")
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    escaping = "../outside"

    with closing(_conn()) as conn:
        plugin_install._ensure_schema(conn)
        # Straight into the table, past register(): proves install() defends
        # itself rather than trusting that the registry was policed.
        conn.execute(
            "INSERT INTO plugin_registry (name, repo_url, registered_at) VALUES (?, ?, ?)",
            (escaping, "https://example.invalid/x.git", 1.0),
        )
        conn.commit()
        outcome = plugin_install.install(conn, escaping, "v1.0.0", plugins_root=plugins_root)

    assert isinstance(outcome, plugin_install.InvalidName)
    assert (outside / "precious.txt").read_text() == "do not clobber"


# ── H16: the registry ledger and the plugin_state index ────────────────────


@pytest.mark.unit
def test_registering_then_installing_records_two_changes(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    with closing(_conn()) as conn:
        plugin_install.register(conn, "greeter", str(repo), now=time.time())
        plugin_install.install(conn, "greeter", "v1.0.0", plugins_root=tmp_path / "plugins")

        state = conn.execute("SELECT id, state, source_tag FROM plugin_state WHERE name = 'greeter'").fetchone()
        registry = conn.execute("SELECT id FROM plugin_registry WHERE name = 'greeter'").fetchone()
        changes = ledger.changes_since(conn, 0, 10)

    assert (state["state"], state["source_tag"]) == ("installed", "v1.0.0")
    assert [(c.noun, c.kind) for c in changes] == [("plugins", "created"), ("plugins", "created")]
    assert {c.id for c in changes} == {registry["id"], state["id"]}


@pytest.mark.unit
def test_a_directory_whose_manifest_is_invalid_reconciles_to_error(tmp_path: Path) -> None:
    """A broken plugin is the one the console most needs to show. Hiding it is
    what `editor_server.list_plugins` already declined to do."""
    plugins_root = tmp_path / "plugins"
    (plugins_root / "broken").mkdir(parents=True)
    (plugins_root / "broken" / "plugin.toml").write_text("this is not toml [[[")

    with closing(_conn()) as conn:
        plugin_install._ensure_schema(conn)
        plugin_install.reconcile_plugin_state(conn, plugins_root)

        row = conn.execute("SELECT name, state FROM plugin_state").fetchone()

    assert (row["name"], row["state"]) == ("broken", "error")


@pytest.mark.unit
def test_reconciling_an_unchanged_root_announces_nothing(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    (plugins_root / "broken").mkdir(parents=True)
    (plugins_root / "broken" / "plugin.toml").write_text("this is not toml [[[")

    with closing(_conn()) as conn:
        plugin_install._ensure_schema(conn)
        plugin_install.reconcile_plugin_state(conn, plugins_root)
        head = ledger.ledger_head(conn)

        plugin_install.reconcile_plugin_state(conn, plugins_root)

        assert ledger.ledger_head(conn) == head


@pytest.mark.unit
def test_a_removed_plugin_directory_is_tombstoned(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    (plugins_root / "gone").mkdir(parents=True)
    (plugins_root / "gone" / "plugin.toml").write_text("this is not toml [[[")

    with closing(_conn()) as conn:
        plugin_install._ensure_schema(conn)
        plugin_install.reconcile_plugin_state(conn, plugins_root)
        plugin_id = conn.execute("SELECT id FROM plugin_state").fetchone()["id"]

        shutil.rmtree(plugins_root / "gone")
        plugin_install.reconcile_plugin_state(conn, plugins_root)

        assert conn.execute("SELECT COUNT(*) FROM plugin_state").fetchone()[0] == 0
        last = ledger.changes_since(conn, 0, 10)[-1]

    assert (last.kind, last.id) == ("deleted", plugin_id)


@pytest.mark.unit
def test_a_disabled_plugin_stays_disabled_across_a_reconciliation(tmp_path: Path) -> None:
    """Whether a plugin works is a fact about the files; whether it is
    switched off is a decision somebody made. A reconciliation must not
    overrule the second with the first. H24 adds the verb that writes it."""
    plugins_root = tmp_path / "plugins"
    (plugins_root / "off").mkdir(parents=True)
    (plugins_root / "off" / "plugin.toml").write_text("this is not toml [[[")

    with closing(_conn()) as conn:
        plugin_install._ensure_schema(conn)
        plugin_install.reconcile_plugin_state(conn, plugins_root)
        conn.execute("UPDATE plugin_state SET state = 'disabled'")

        plugin_install.reconcile_plugin_state(conn, plugins_root)

        assert conn.execute("SELECT state FROM plugin_state").fetchone()["state"] == "disabled"
