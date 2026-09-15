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
import time
from dataclasses import dataclass
from pathlib import Path

from sadana import ids, ledger, plugins
from sadana.conversation_store import fill_legacy_identity, migrate_columns, write_txn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plugin_registry (
    name           TEXT PRIMARY KEY,
    repo_url       TEXT NOT NULL,
    registered_at  REAL NOT NULL,
    -- H16.
    id             TEXT,
    updated_at     REAL,
    version        INTEGER NOT NULL DEFAULT 1
);

-- H16 requirement 9: one row per directory holding a `plugin.toml`, so the
-- inventory can be enumerated without walking the plugins root (P3).
--
-- Listed the way `editor_server.list_plugins` lists — including a plugin that
-- does not validate, which gets `state = 'error'`. `discover_plugins()` is
-- deliberately not the source: it returns only what fully validates, which is
-- right for deciding what an agent may call and exactly wrong for an index
-- whose job is to say what is *there*.
--
-- `name` is the PRIMARY KEY because a plugin directory's name is its natural
-- key and already has the filesystem behind it; `id` is UNIQUE beside it as
-- the thing a ledger row points at.
CREATE TABLE IF NOT EXISTS plugin_state (
    name          TEXT PRIMARY KEY,
    id            TEXT UNIQUE NOT NULL,
    state         TEXT NOT NULL DEFAULT 'installed',
    source_repo   TEXT,
    source_tag    TEXT,
    installed_at  REAL NOT NULL,
    updated_at    REAL NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1
);
"""

#: See `conversation_store._MIGRATED_COLUMNS`.
_MIGRATED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "plugin_registry": (
        ("id", "TEXT"),
        ("updated_at", "REAL"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
    ),
}

#: Every word `plugin_state.state` may hold. `installed` and `error` are
#: recomputed from disk by `reconcile_plugin_state`; `disabled` is a decision
#: somebody made and is never overruled by an observation. H24 adds the verb
#: that writes it.
PLUGIN_STATES = frozenset({"installed", "error", "disabled"})

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


@dataclass(frozen=True)
class InvalidName:
    """`name` is not one `plugins.PLUGIN_NAME_RE` allows, or it resolves
    outside the plugins root.

    Checked at both doors an outside name enters by. `register()` mints a
    long-lived key, so an unsafe name must never reach the table; `install()`
    re-checks anyway rather than trusting that the table was policed, because
    the row it reads is the thing that decides where `os.replace` writes."""

    name: str


RegisterOutcome = Registered | NameTaken | InvalidName


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


InstallOutcome = (
    Installed | UnknownPluginName | AlreadyInstalled | NameMismatch | TagMismatch | FetchFailed | InvalidName
)


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
    `observability.make_recorder()` already takes on this same connection.
    H16 added three columns to `plugin_registry`, which is what the migration
    map is for."""
    conn.executescript(_SCHEMA)
    migrate_columns(conn, _MIGRATED_COLUMNS)
    fill_legacy_identity(conn, "plugin_registry", "plg", time_columns=("updated_at",))


def register(conn: sqlite3.Connection, name: str, repo_url: str, *, now: float) -> RegisterOutcome:
    """Record that `name` means `repo_url`. `name` is a real `PRIMARY KEY`
    — CLAUDE.md's rule that a long-lived name a user returns to needs a
    database constraint behind it, applied literally."""
    if not plugins.PLUGIN_NAME_RE.fullmatch(name):
        return InvalidName(name=name)
    _ensure_schema(conn)
    at = time.time()
    registry_id = ids.make_id("plg")
    try:
        with write_txn(conn) as c:
            c.execute(
                "INSERT INTO plugin_registry (name, repo_url, registered_at, id, updated_at) VALUES (?, ?, ?, ?, ?)",
                (name, repo_url, now, registry_id, at),
            )
            ledger.record_change(c, noun="plugins", id=registry_id, kind="created", state=None, version=1, at=at)
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


def _place_atomically(target: Path, source: Path, tmp: Path) -> FetchFailed | None:
    """Atomically replace `target` with `source` (both already resolved,
    `source` sitting inside `tmp`'s own `TemporaryDirectory`), rolling back
    to whatever was at `target` before on any `OSError` mid-swap.

    Shared by `install()` and `install_from_git()` — one placement sequence,
    regardless of how the plugin's name was decided (H24:
    `docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`)."""
    backup = tmp / "previous-plugin"
    replaced_existing = target.exists()
    moved_existing_aside = False
    try:
        if replaced_existing:
            os.replace(target, backup)
            moved_existing_aside = True
        os.replace(source, target)
    except OSError as exc:
        if moved_existing_aside:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(backup, target)
        return FetchFailed(detail=f"could not place plugin: {exc}")
    return None


def _fetch_and_verify_name(
    repo_url: str, tag: str, dest: Path, *, expect_name: str | None
) -> tuple[FetchedTag, plugins.Manifest] | NameMismatch | TagMismatch | FetchFailed:
    """`fetch_verified_tag()`, then parse the fetched manifest and —
    when `expect_name` is given — confirm it declares that name. The one
    fetch-then-parse-then-check sequence `install()` and `install_from_git()`
    both run, byte for byte; they differ only in *when* the expected name is
    known (before the fetch for `install()`, only after it for
    `install_from_git()`), which is why this takes it as a parameter rather
    than assuming either caller's own ordering."""
    fetched = fetch_verified_tag(repo_url, tag, dest)
    if isinstance(fetched, TagMismatch | FetchFailed):
        return fetched
    manifest_path = fetched.directory / "plugin.toml"
    try:
        manifest = plugins._parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return FetchFailed(detail=f"plugin.toml is invalid: {exc}")
    if expect_name is not None and manifest.name != expect_name:
        return NameMismatch(expected=expect_name, found=manifest.name)
    return fetched, manifest


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

    # Before the name is allowed to become a path at all. `plugins_root / name`
    # with an unchecked name escapes the root, and what happens at the end of
    # this function is an `os.replace` over whatever is at `target`.
    target = plugins.plugin_dir(plugins_root, name)
    if target is None:
        return InvalidName(name=name)

    repo_url = resolve(conn, name)
    if repo_url is None:
        return UnknownPluginName(name=name)

    if target.exists() and not replace:
        return AlreadyInstalled(name=name)

    plugins_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=plugins_root) as tmp:
        tmp_clone = Path(tmp) / "plugin"
        outcome = _fetch_and_verify_name(repo_url, tag, tmp_clone, expect_name=name)
        if isinstance(outcome, NameMismatch | TagMismatch | FetchFailed):
            return outcome
        fetched, _manifest = outcome

        error = _place_atomically(target, fetched.directory, Path(tmp))
        if error is not None:
            return error

    _record_installed(conn, name, repo_url=repo_url, tag=tag)
    return Installed(name=name, directory=target, revision=fetched.revision)


def install_from_git(
    conn: sqlite3.Connection,
    repo_url: str,
    tag: str,
    *,
    plugins_root: Path,
    expect_name: str | None = None,
    replace: bool = False,
) -> InstallOutcome:
    """Like `install()`, but the plugin's name comes from the fetched
    manifest, not a pre-registered one — the console's own `create`/
    `install-from-git`, which reaches this module with only a repository and
    a tag, never a name
    (`docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`).

    `expect_name`, when given, refuses a `NameMismatch` the same way
    `install()`'s own `name` argument already does — the `act()`-path
    re-install case, where an already-registered plugin's identity must not
    silently change out from under its own id.

    Shares `fetch_verified_tag()`, `_is_valid_tag_syntax()`,
    `_fetch_and_verify_name()` and `_place_atomically()`/`_record_installed()`
    with `install()` — no second `git` invocation anywhere in this module.
    Unlike `install()`, the plugin's name — and therefore whether it is a
    safe path and whether it is already installed — can only be checked
    *after* the fetch, since nothing is known about it before then."""
    _ensure_schema(conn)  # unlike install(), never reaches resolve(), which does this implicitly
    if not _is_valid_tag_syntax(tag):
        return FetchFailed(detail=f"{tag!r} is not a valid tag name")

    plugins_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=plugins_root) as tmp:
        tmp_clone = Path(tmp) / "plugin"
        outcome = _fetch_and_verify_name(repo_url, tag, tmp_clone, expect_name=expect_name)
        if isinstance(outcome, NameMismatch | TagMismatch | FetchFailed):
            return outcome
        fetched, manifest = outcome

        target = plugins.plugin_dir(plugins_root, manifest.name)
        if target is None:
            return InvalidName(name=manifest.name)
        if target.exists() and not replace:
            return AlreadyInstalled(name=manifest.name)

        error = _place_atomically(target, fetched.directory, Path(tmp))
        if error is not None:
            return error

    _record_installed(conn, manifest.name, repo_url=repo_url, tag=tag)
    return Installed(name=manifest.name, directory=target, revision=fetched.revision)


def set_state(conn: sqlite3.Connection, id: str, to_state: str, *, now: float) -> None:
    """Writes `plugin_state.state` for one row, found by `id` — `disable`/
    `enable`'s one write (H24). Mirrors
    `conversation_store.set_schedule_state`'s shape: read the current
    version inside the write transaction, bump it, record the change.
    A silent no-op if `id` names no row — the door's own `act()` already
    checked the row exists before calling this."""
    with write_txn(conn) as c:
        row = c.execute("SELECT version FROM plugin_state WHERE id = ?", (id,)).fetchone()
        if row is None:
            return
        new_version = row["version"] + 1
        c.execute(
            "UPDATE plugin_state SET state = ?, updated_at = ?, version = ? WHERE id = ?",
            (to_state, now, new_version, id),
        )
        ledger.record_change(c, noun="plugins", id=id, kind="changed", state=to_state, version=new_version, at=now)


def _record_installed(conn: sqlite3.Connection, name: str, *, repo_url: str, tag: str) -> None:
    """Upsert the `plugin_state` row for a plugin that just landed on disk.

    Outside the placement transaction on purpose: the plugin is already at
    `target` by the time this runs, and an index row is a derived record of a
    filesystem fact, never the fact itself. If this failed, the next
    `reconcile_plugin_state` finds the directory and inserts the row — which is
    exactly what the reconciliation is for.

    `state` is left alone on an update: a plugin somebody disabled and then
    upgraded stays disabled, because upgrading is not the same as re-enabling
    and silently turning it back on is the surprise.
    """
    at = time.time()
    with write_txn(conn) as c:
        existing = c.execute("SELECT id FROM plugin_state WHERE name = ?", (name,)).fetchone()
        c.execute(
            "INSERT INTO plugin_state (name, id, source_repo, source_tag, installed_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (name) DO UPDATE SET source_repo = excluded.source_repo, "
            "source_tag = excluded.source_tag, updated_at = excluded.updated_at, "
            "version = plugin_state.version + 1",
            (name, ids.make_id("plg"), repo_url, tag, at, at),
        )
        row = c.execute("SELECT id, state, version FROM plugin_state WHERE name = ?", (name,)).fetchone()
        ledger.record_change(
            c,
            noun="plugins",
            id=row["id"],
            kind="changed" if existing else "created",
            state=row["state"],
            version=row["version"],
            at=at,
        )


def reconcile_plugin_state(conn: sqlite3.Connection, plugins_root: Path) -> None:
    """Make the `plugin_state` index match the plugins directory.

    Every directory holding a `plugin.toml` gets a row, whether or not it
    validates — a broken plugin is one the console most needs to show, and
    hiding it is what `editor_server.list_plugins` already declined to do.
    `state` is `'installed'` when it validates and `'error'` when it does not.

    A row already marked `'disabled'` keeps that word. Only `'installed'` and
    `'error'` are recomputed from disk: whether a plugin *works* is a fact
    about the files, but whether it is *switched off* is a decision somebody
    made, and a reconciliation must not overrule a decision with an
    observation. Nothing writes `'disabled'` yet — H24 adds that verb, and
    `client_surface.enabled_plugin_set` already honours it, over
    `disabled_names()` below.

    A row whose directory has gone is announced as `deleted` and removed.
    """
    at = time.time()
    found = {}
    if plugins_root.is_dir():
        for child in sorted(plugins_root.iterdir()):
            if child.is_dir() and (child / "plugin.toml").is_file():
                found[child.name] = "installed" if _validates(child) else "error"
    known = {
        row["name"]: (row["id"], row["state"], row["version"])
        for row in conn.execute("SELECT id, name, state, version FROM plugin_state")
    }
    # Read first, and take no transaction at all when nothing moved — which is
    # every open after the first. `write_txn` takes SQLite's database-level
    # write lock, so an unconditional one here would have every `sadana` command
    # contend with a running gateway for the length of its own commit.
    # `fill_legacy_identity` already takes this shape for the same reason.
    if not _plugin_state_differs(found, known):
        return
    with write_txn(conn) as c:
        for name, state in found.items():
            if name not in known:
                plugin_id = ids.make_id("plg")
                c.execute(
                    "INSERT INTO plugin_state (name, id, state, installed_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (name, plugin_id, state, at, at),
                )
                ledger.record_change(c, noun="plugins", id=plugin_id, kind="created", state=state, version=1, at=at)
                continue
            plugin_id, known_state, version = known[name]
            if known_state in ("disabled", state):
                continue
            c.execute(
                "UPDATE plugin_state SET state = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                (state, at, plugin_id),
            )
            ledger.record_change(
                c, noun="plugins", id=plugin_id, kind="changed", state=state, version=version + 1, at=at
            )
        for name, (plugin_id, _state, _version) in known.items():
            if name in found:
                continue
            ledger.record_change(c, noun="plugins", id=plugin_id, kind="deleted", state=None, version=None, at=at)
            c.execute("DELETE FROM plugin_state WHERE id = ?", (plugin_id,))


def disabled_names(conn: sqlite3.Connection) -> frozenset[str]:
    """Every plugin name somebody has switched off.

    Lives here because this module owns `plugin_state`. The first cut had
    `plugin_dispatch` querying the table directly, which put one module's
    column names inside another with nothing keeping them true — the same
    weakness `spec.md` § Concerns already names as the ledger's worst
    property, and there is no reason to repeat it where the import is legal.

    Empty when the table does not exist yet: a connection whose schemas were
    never ensured must not lose every tool, because failing closed here turns
    a setup omission into a silent, total loss of capability.

    Nothing writes `disabled` yet — H24 adds that verb. This and the filter in
    `client_surface.open_runtime` are here now so H24 adds only the verb and
    cannot forget the enforcement.
    """
    try:
        rows = conn.execute("SELECT name FROM plugin_state WHERE state = 'disabled'").fetchall()
    except sqlite3.OperationalError:
        return frozenset()
    return frozenset(row["name"] for row in rows)


def _plugin_state_differs(found: dict[str, str], known: dict[str, tuple[str, str, int]]) -> bool:
    """Whether `reconcile_plugin_state` has anything to write. Pure, so the
    steady-state answer costs no transaction and can be tested directly.

    A `disabled` row matches whatever is on disk: switching a plugin off is a
    decision somebody made, and an observation must not overrule it.
    """
    if set(found) != set(known):
        return True
    return any(state != "disabled" and state != found[name] for name, (_id, state, _v) in known.items())


def _validates(directory: Path) -> bool:
    """Whether `directory`'s manifest is well-formed — **without running any of
    its code**.

    `check_bodies=False` is the whole point, not an optimisation. The default
    imports and executes the plugin's own body module to confirm a named
    function exists, and this function is reached from
    `stores.reconcile_indexes`, which runs whenever a process opens a
    `Connections`. At the default, opening the store would import and execute
    every installed plugin's Python before doing anything else —
    CLAUDE.md: "A check that can execute code as a side effect of validating
    it must offer a mode that never executes anything, and any caller handling
    input from a source it doesn't already trust uses that mode." A plugin
    installed from a git tag is such a source, and an index has no business
    running one.

    `editor_server.assess` reached the same conclusion for the same reason:
    listing what is there is a different question from deciding what may run.

    Imported inside the function rather than at module scope: `plugin_manifest`
    imports this module's sibling `plugins`, and a top-level import would tie
    the install path to the validation path for a check only the index needs.
    """
    from sadana import plugin_manifest

    return isinstance(plugin_manifest.validate(directory, check_bodies=False), plugins.Valid)
