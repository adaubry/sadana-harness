"""Naming a plugin is enough to get a verified copy of it.

`docs/tasks/PLUGIN-INSTALL-01-registry-fetch-verify-place/spec.md`. The
block that starts policing "the repo is the plugin"
(`docs/reference/plugin_blueprint.md` §3.2/§9): fetch a repository at a
released tag, confirm the fetched tree really is that tag, and place it
under `plugins._plugins_root()` — the same root
`plugin_manifest.discover_plugins()` already scans, so nothing on that side
needs to change.

Touches disk, the network and a subprocess (`git`), so per CLAUDE.md this
is its own file, never sharing one with `plugins.py` (explicitly pure-data)
or `plugin_manifest.py`. Reuses `conversation_store`'s own SQLite
connection and file for the registry table — no second store, no second
writer, the same posture `observability.py` already took for `turn_runs`/
`plugin_runs`.

`RegisterOutcome` and `InstallOutcome` are closed sets of dataclasses, never
raised exceptions, matching `plugin_manifest.py`'s own `ManifestOutcome`
shape.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sadana import plugins
from sadana.conversation_store import write_txn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plugin_registry (
    name           TEXT PRIMARY KEY,
    repo_url       TEXT NOT NULL,
    registered_at  REAL NOT NULL
);
"""

_GIT_TIMEOUT_S = 60
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class Registered:
    name: str


@dataclass(frozen=True)
class NameTaken:
    """`name` is already in the registry — a registration never silently
    repoints an existing name, the same refuse-by-default posture
    `AlreadyInstalled` applies to installing."""

    name: str


RegisterOutcome = Registered | NameTaken


@dataclass(frozen=True)
class Installed:
    name: str
    directory: Path
    revision: str


@dataclass(frozen=True)
class UnknownPluginName:
    """`name` has no entry in the registry."""

    name: str


@dataclass(frozen=True)
class AlreadyInstalled:
    """A plugin directory already exists under this name and `replace`
    wasn't asked for."""

    name: str


@dataclass(frozen=True)
class NameMismatch:
    """The fetched `plugin.toml`'s own declared name disagrees with the
    name it was installed under — `_skill_path()` addresses a plugin's
    skills by directory name, so this would otherwise silently break skill
    resolution the first time anything tries to use it."""

    expected: str
    found: str


@dataclass(frozen=True)
class TagMismatch:
    """What got checked out doesn't match what the tag names on the
    remote right now."""

    tag: str
    expected_revision: str
    actual_revision: str


@dataclass(frozen=True)
class FetchFailed:
    """Anything that stopped the fetch itself: git not installed, the
    network unreachable, an unknown tag, an unknown repository, or a
    fetched `plugin.toml` that doesn't even parse. Carries whatever
    detail is available; never a raw traceback."""

    detail: str


InstallOutcome = Installed | UnknownPluginName | AlreadyInstalled | NameMismatch | TagMismatch | FetchFailed


def describe_fetch_failure(outcome: TagMismatch | FetchFailed) -> str:
    """A one-line, human-readable account of either failure — lives
    beside the two types it describes so `install()`'s own CLI and
    `marketplace.submit()`'s own CLI (PLUGIN-MARKET-01) report the exact
    same wording for the exact same outcome, instead of each re-deriving
    it."""
    if isinstance(outcome, TagMismatch):
        return (
            f"tag {outcome.tag!r} resolved to {outcome.expected_revision}, "
            f"but the clone checked out {outcome.actual_revision}"
        )
    return outcome.detail


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent, the same `CREATE TABLE IF NOT EXISTS` posture
    `observability.make_recorder()` already takes on this same
    connection — no column to migrate yet, so no `ALTER TABLE` guard is
    needed."""
    conn.executescript(_SCHEMA)


def register(conn: sqlite3.Connection, name: str, repo_url: str, *, now: float) -> RegisterOutcome:
    """Record that `name` means `repo_url`. `name` is a real `PRIMARY KEY`
    — CLAUDE.md's rule that a long-lived name a user returns to needs a
    database constraint behind it, applied literally."""
    _ensure_schema(conn)
    try:
        with write_txn(conn) as c:
            c.execute(
                "INSERT INTO plugin_registry (name, repo_url, registered_at) VALUES (?, ?, ?)",
                (name, repo_url, now),
            )
    except sqlite3.IntegrityError:
        return NameTaken(name=name)
    return Registered(name=name)


def resolve(conn: sqlite3.Connection, name: str) -> str | None:
    """`name`'s registered repository URL, or `None` if nothing registered
    it."""
    _ensure_schema(conn)
    row = conn.execute("SELECT repo_url FROM plugin_registry WHERE name = ?", (name,)).fetchone()
    return row["repo_url"] if row is not None else None


class _GitError(Exception):
    """Raised only by the helpers below and caught once, inside `install()`
    — never crosses this module's public functions, which return
    `InstallOutcome` values, never exceptions."""


def _git(*args: str, cwd: Path | None = None) -> str:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # fail fast rather than hang on a credential prompt; public repos only
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError as exc:
        raise _GitError("git is not installed or not in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise _GitError(f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_S}s") from exc
    if result.returncode != 0:
        raise _GitError((result.stderr or result.stdout or "git failed").strip())
    return result.stdout


def _is_valid_tag_syntax(tag: str) -> bool:
    """A bare 40-char commit SHA is not a released version (Requirement
    3); everything else must be a syntactically valid ref name. Reuses
    `git check-ref-format` rather than hand-rolling the same rules."""
    if _SHA_RE.fullmatch(tag):
        return False
    try:
        _git("check-ref-format", "--allow-onelevel", f"refs/tags/{tag}")
    except _GitError:
        return False
    return True


def _resolve_tag_commit(repo_url: str, tag: str) -> str | None:
    """The commit `tag` names on `repo_url` right now, or `None` if it
    isn't a tag there (including: doesn't exist, is only a branch, or the
    remote couldn't be reached at all — all the same "can't install this"
    outcome from the caller's side). Prefers the peeled `^{}` entry, an
    annotated tag's real commit, over the tag object's own SHA.

    The `--` before `repo_url` is load-bearing, not stylistic: `repo_url`
    is caller-supplied (PLUGIN-MARKET-01 routes it straight from an
    anonymous submission), and without `--` a value starting with `-`
    (e.g. `--upload-pack=<command>`) is parsed by git as an option, not a
    repository — arbitrary command execution, independent of anything
    `check_bodies` guards. `--` forces every argument after it to be
    read literally."""
    try:
        output = _git("ls-remote", "--", repo_url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
    except _GitError:
        return None
    commit_by_ref: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        sha, _, ref = line.partition("\t")
        commit_by_ref[ref] = sha
    peeled = commit_by_ref.get(f"refs/tags/{tag}^{{}}")
    return peeled if peeled is not None else commit_by_ref.get(f"refs/tags/{tag}")


def _head_revision(repo: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=repo).strip()


@dataclass(frozen=True)
class FetchedTag:
    """A tag fetched into `dest` and confirmed to be what it claims."""

    directory: Path
    revision: str


def fetch_verified_tag(repo_url: str, tag: str, dest: Path) -> FetchedTag | TagMismatch | FetchFailed:
    """Resolve `tag`'s real commit on `repo_url`, clone it into `dest`,
    and confirm what got checked out really is that commit. `dest` must
    not already exist; its parent must. The one place this project
    fetches and verifies a tag — `install()` and
    `marketplace.submit()` (PLUGIN-MARKET-01) both call this rather than
    each re-deriving the same resolve→clone→verify sequence.

    Same `--` reasoning as `_resolve_tag_commit()`: `repo_url` is
    caller-supplied and must never be readable by git as an option."""
    try:
        expected_revision = _resolve_tag_commit(repo_url, tag)
        if expected_revision is None:
            return FetchFailed(detail=f"{tag!r} is not a tag on {repo_url!r}")

        _git("clone", "--depth", "1", "--branch", tag, "--", repo_url, str(dest))

        actual_revision = _head_revision(dest)
        if actual_revision != expected_revision:
            return TagMismatch(tag=tag, expected_revision=expected_revision, actual_revision=actual_revision)

        return FetchedTag(directory=dest, revision=actual_revision)
    except _GitError as exc:
        return FetchFailed(detail=str(exc))


def install(
    conn: sqlite3.Connection,
    name: str,
    tag: str,
    *,
    plugins_root: Path,
    replace: bool = False,
) -> InstallOutcome:
    """Resolve `name` through the registry, fetch `tag`, confirm it's
    really that tag, and place it at `plugins_root / name` — atomically,
    and only once every check above held. See spec.md's Design section
    for the full, numbered walk-through this function implements."""
    if not _is_valid_tag_syntax(tag):
        return FetchFailed(detail=f"{tag!r} is not a valid tag name")

    repo_url = resolve(conn, name)
    if repo_url is None:
        return UnknownPluginName(name=name)

    target = plugins_root / name
    if target.exists() and not replace:
        return AlreadyInstalled(name=name)

    plugins_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=plugins_root) as tmp:
        tmp_clone = Path(tmp) / "plugin"
        fetched = fetch_verified_tag(repo_url, tag, tmp_clone)
        if isinstance(fetched, TagMismatch | FetchFailed):
            return fetched

        manifest_path = fetched.directory / "plugin.toml"
        try:
            manifest = plugins._parse_manifest(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return FetchFailed(detail=f"plugin.toml is invalid: {exc}")
        if manifest.name != name:
            return NameMismatch(expected=name, found=manifest.name)

        backup = Path(tmp) / "previous-plugin"
        replaced_existing = target.exists()
        moved_existing_aside = False
        try:
            if replaced_existing:
                os.replace(target, backup)
                moved_existing_aside = True
            os.replace(fetched.directory, target)
        except OSError as exc:
            if moved_existing_aside:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                os.replace(backup, target)
            return FetchFailed(detail=f"could not place plugin: {exc}")

    return Installed(name=name, directory=target, revision=fetched.revision)
