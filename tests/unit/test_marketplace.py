"""Tests for sadana.marketplace: submit(), decide(), and the query
functions."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from conftest import make_upstream_repo as _make_upstream_repo
from conftest import open_conn as _conn
from conftest import run_git as _run_git
from sadana import marketplace


def _make_broken_upstream_repo(tmp_path: Path, *, plugin_name: str = "broken", tag: str = "v1.0.0") -> Path:
    """A real, local git repository whose `plugin.toml` parses but fails a
    structural check (a `next` pointing at an undeclared node) — for
    proving `InvalidManifest` never reaches the reviewer queue."""
    repo = tmp_path / "upstream-broken"
    repo.mkdir()
    _run_git(["init", "-q", "-b", "main"], cwd=repo)
    _run_git(["config", "user.email", "test@example.com"], cwd=repo)
    _run_git(["config", "user.name", "Test"], cwd=repo)
    (repo / "plugin.toml").write_text(
        f'[plugin]\nname = "{plugin_name}"\nversion = "{tag}"\ndescription = "d"\n\n'
        '[[node]]\nname = "a"\nkind = "compute"\nnext = "no-such-node"\n'
    )
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "initial"], cwd=repo)
    _run_git(["tag", tag], cwd=repo)
    return repo


@pytest.mark.unit
def test_submit_a_valid_release_is_pending_under_its_own_declared_name(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        outcome = marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
    finally:
        conn.close()

    assert outcome == marketplace.Pending(plugin_name="greeter", tag="v1.0.0")


@pytest.mark.unit
def test_submit_never_imports_or_executes_the_fetched_repository(tmp_path: Path) -> None:
    """The one thing this whole work item exists to prove."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    _run_git(["init", "-q", "-b", "main"], cwd=repo)
    _run_git(["config", "user.email", "test@example.com"], cwd=repo)
    _run_git(["config", "user.name", "Test"], cwd=repo)
    (repo / "plugin.toml").write_text('[plugin]\nname = "greeter"\nversion = "v1.0.0"\ndescription = "d"\n')
    sentinel = tmp_path / "executed.marker"
    (repo / "init.py").write_text(f"open({str(sentinel)!r}, 'w').close()\n")
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "initial"], cwd=repo)
    _run_git(["tag", "v1.0.0"], cwd=repo)

    conn = _conn()
    try:
        outcome = marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
    finally:
        conn.close()

    assert isinstance(outcome, marketplace.Pending)
    assert not sentinel.exists()


@pytest.mark.unit
def test_submit_a_second_repo_claiming_an_already_owned_name_is_refused(tmp_path: Path) -> None:
    first_repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    (tmp_path / "second").mkdir()
    second_repo = _make_upstream_repo(tmp_path / "second", plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(first_repo), "v1.0.0", now=time.time())
        outcome = marketplace.submit(conn, str(second_repo), "v1.0.0", now=time.time())
    finally:
        conn.close()

    assert outcome == marketplace.NameOwnedByAnotherRepo(plugin_name="greeter", owning_repo_url=str(first_repo))


@pytest.mark.unit
def test_claim_release_is_atomic_under_two_concurrent_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two different repos race to claim the same brand-new name at the
    same instant — exactly what two concurrent requests to the
    marketplace webhook's ThreadingHTTPServer look like, each on its own
    connection. Without `_claim_release()`'s single `write_txn`, both
    could observe "nobody owns this yet" and both succeed. Proven by
    forcing the real interleaving a race needs (SQLite's own `BEGIN
    IMMEDIATE` lock), not by hoping a timing window lines up."""
    setup_conn = _conn()
    try:
        marketplace._ensure_schema(setup_conn)
    finally:
        setup_conn.close()

    entered_critical_section = threading.Event()
    let_first_thread_proceed = threading.Event()
    real_owning_repo_url = marketplace._owning_repo_url

    def slow_owning_repo_url(conn: object, plugin_name: str) -> str | None:
        result = real_owning_repo_url(conn, plugin_name)  # type: ignore[arg-type]
        entered_critical_section.set()
        let_first_thread_proceed.wait(timeout=5)
        return result

    monkeypatch.setattr(marketplace, "_owning_repo_url", slow_owning_repo_url)

    results: dict[str, marketplace.Pending | marketplace.NameOwnedByAnotherRepo | marketplace.AlreadySubmitted] = {}

    def claim(label: str, repo_url: str) -> None:
        conn = _conn()
        try:
            results[label] = marketplace._claim_release(
                conn,
                plugin_name="greeter",
                tag="v1.0.0",
                repo_url=repo_url,
                revision="a" * 40,
                manifest_json="{}",
                now=time.time(),
            )
        finally:
            conn.close()

    first = threading.Thread(target=claim, args=("first", "https://example.invalid/first.git"))
    first.start()
    assert entered_critical_section.wait(timeout=5)

    second = threading.Thread(target=claim, args=("second", "https://example.invalid/second.git"))
    second.start()
    second.join(timeout=0.2)
    assert second.is_alive(), "second connection should be blocked on SQLite's own write lock"

    let_first_thread_proceed.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert results["first"] == marketplace.Pending(plugin_name="greeter", tag="v1.0.0")
    assert results["second"] == marketplace.NameOwnedByAnotherRepo(
        plugin_name="greeter", owning_repo_url="https://example.invalid/first.git"
    )


@pytest.mark.unit
def test_submit_the_exact_same_release_twice_is_already_submitted(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
        outcome = marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
    finally:
        conn.close()

    assert outcome == marketplace.AlreadySubmitted(plugin_name="greeter", tag="v1.0.0")


@pytest.mark.unit
def test_submit_an_unknown_tag_is_tag_mismatch_shaped_fetch_failed(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        outcome = marketplace.submit(conn, str(repo), "no-such-tag", now=time.time())
    finally:
        conn.close()

    assert isinstance(outcome, marketplace.plugin_install.FetchFailed)


@pytest.mark.unit
def test_submit_a_structurally_invalid_manifest_is_auto_rejected_not_queued(tmp_path: Path) -> None:
    repo = _make_broken_upstream_repo(tmp_path, plugin_name="broken", tag="v1.0.0")
    conn = _conn()
    try:
        outcome = marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
        queue = marketplace.pending_releases(conn)
    finally:
        conn.close()

    assert isinstance(outcome, marketplace.InvalidManifest)
    assert outcome.plugin_name == "broken"
    assert queue == ()


@pytest.mark.unit
def test_decide_approve_makes_it_the_latest_approved_release(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
        outcome = marketplace.decide(conn, "greeter", "v1.0.0", "approved", now=time.time())
        listing = marketplace.latest_approved(conn, "greeter")
    finally:
        conn.close()

    assert outcome == marketplace.Decided(plugin_name="greeter", tag="v1.0.0", status="approved")
    assert listing is not None
    assert listing.tag == "v1.0.0"
    assert listing.manifest["name"] == "greeter"


@pytest.mark.unit
def test_decide_reject_without_a_reason_is_refused(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
        outcome = marketplace.decide(conn, "greeter", "v1.0.0", "rejected", now=time.time())
    finally:
        conn.close()

    assert outcome == marketplace.ReasonRequired()


@pytest.mark.unit
def test_decide_reject_with_a_reason_is_visible_to_a_reviewer(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
        marketplace.decide(conn, "greeter", "v1.0.0", "rejected", reason="looks unsafe", now=time.time())
        row = conn.execute(
            "SELECT reason FROM marketplace_releases WHERE plugin_name = 'greeter' AND tag = 'v1.0.0'"
        ).fetchone()
    finally:
        conn.close()

    assert row["reason"] == "looks unsafe"


@pytest.mark.unit
def test_decide_unknown_release_is_refused(tmp_path: Path) -> None:
    conn = _conn()
    try:
        outcome = marketplace.decide(conn, "nobody-submitted-this", "v1.0.0", "approved", now=time.time())
    finally:
        conn.close()
    assert outcome == marketplace.UnknownRelease(plugin_name="nobody-submitted-this", tag="v1.0.0")


@pytest.mark.unit
def test_decide_an_already_decided_release_is_refused(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
        marketplace.decide(conn, "greeter", "v1.0.0", "approved", now=time.time())
        outcome = marketplace.decide(conn, "greeter", "v1.0.0", "rejected", reason="changed my mind", now=time.time())
    finally:
        conn.close()

    assert outcome == marketplace.AlreadyDecided(plugin_name="greeter", tag="v1.0.0", status="approved")


@pytest.mark.unit
def test_a_newer_pending_release_never_replaces_the_still_visible_approved_one(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
        marketplace.decide(conn, "greeter", "v1.0.0", "approved", now=time.time())

        _run_git(["tag", "v1.1.0"], cwd=repo)
        marketplace.submit(conn, str(repo), "v1.1.0", now=time.time())

        listing = marketplace.latest_approved(conn, "greeter")
        pending = marketplace.latest_pending(conn, "greeter")
    finally:
        conn.close()

    assert listing is not None
    assert listing.tag == "v1.0.0"
    assert pending is not None
    assert pending.tag == "v1.1.0"


@pytest.mark.unit
def test_latest_approved_is_none_for_a_plugin_with_only_a_pending_release(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(repo), "v1.0.0", now=time.time())
        listing = marketplace.latest_approved(conn, "greeter")
    finally:
        conn.close()
    assert listing is None


@pytest.mark.unit
def test_pending_releases_lists_the_reviewer_queue_oldest_first(tmp_path: Path) -> None:
    first = _make_upstream_repo(tmp_path, plugin_name="alpha", tag="v1.0.0")
    (tmp_path / "second").mkdir()
    second = _make_upstream_repo(tmp_path / "second", plugin_name="beta", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(first), "v1.0.0", now=1.0)
        marketplace.submit(conn, str(second), "v1.0.0", now=2.0)
        queue = marketplace.pending_releases(conn)
    finally:
        conn.close()

    assert [r.plugin_name for r in queue] == ["alpha", "beta"]


@pytest.mark.unit
def test_approved_plugin_names_only_lists_approved_plugins(tmp_path: Path) -> None:
    approved_repo = _make_upstream_repo(tmp_path, plugin_name="approved-one", tag="v1.0.0")
    (tmp_path / "second").mkdir()
    pending_repo = _make_upstream_repo(tmp_path / "second", plugin_name="pending-one", tag="v1.0.0")
    conn = _conn()
    try:
        marketplace.submit(conn, str(approved_repo), "v1.0.0", now=time.time())
        marketplace.decide(conn, "approved-one", "v1.0.0", "approved", now=time.time())
        marketplace.submit(conn, str(pending_repo), "v1.0.0", now=time.time())
        names = marketplace.approved_plugin_names(conn)
    finally:
        conn.close()

    assert names == ("approved-one",)
