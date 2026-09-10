"""A plugin nobody can find is a plugin nobody uses.

`docs/tasks/PLUGIN-MARKET-01-submit-vet-and-browse-safely/spec.md`. A
creator submits a repository and a tag; this module fetches and verifies
it exactly as `plugin_install.py` already does for a real install
(`plugin_install.fetch_verified_tag()`), captures the plugin's declared
shape with `plugin_manifest.validate(check_bodies=False)` — never
executing a line of the fetched repository's own code — and discards the
clone. One or more reviewers decide, from that captured shape alone,
whether ordinary browsers get to see it.

Touches disk (a temp clone), the network (through `plugin_install`), and
SQLite, so per CLAUDE.md this is its own file. Reuses
`conversation_store`'s own connection and file for its one table — no
second store, no second writer, the same posture PLUGIN-INSTALL-01 and
OBSERVABILITY-01 both already took.

`SubmitOutcome`/`DecideOutcome` are closed sets of dataclasses, never
raised exceptions, matching `plugin_manifest.py`'s `ManifestOutcome` and
`plugin_install.py`'s own outcome types.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sadana import plugin_install, plugin_manifest, plugins
from sadana.conversation_store import write_txn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketplace_releases (
    plugin_name    TEXT NOT NULL,
    tag            TEXT NOT NULL,
    repo_url       TEXT NOT NULL,
    revision       TEXT NOT NULL,
    manifest_json  TEXT NOT NULL,
    status         TEXT NOT NULL,
    reason         TEXT,
    submitted_at   REAL NOT NULL,
    decided_at     REAL,
    PRIMARY KEY (plugin_name, tag)
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent, the same posture `plugin_install._ensure_schema()` and
    `observability.make_recorder()` already take on this same
    connection."""
    conn.executescript(_SCHEMA)


@dataclass(frozen=True)
class Pending:
    plugin_name: str
    tag: str


@dataclass(frozen=True)
class NameOwnedByAnotherRepo:
    """`plugin_name` is already claimed by a different repository — the
    first repository to ever submit a name owns it (Design: guideline 4,
    derived from the earliest row, never a second table)."""

    plugin_name: str
    owning_repo_url: str


@dataclass(frozen=True)
class AlreadySubmitted:
    """This exact `(plugin_name, tag)` already has a row, whatever its
    status."""

    plugin_name: str
    tag: str


@dataclass(frozen=True)
class InvalidManifest:
    """The fetched `plugin.toml` doesn't structurally validate.
    `plugin_name` is `None` only when the manifest fails to parse at all
    (nothing to key a row on); otherwise a `rejected` row is recorded with
    this same `detail` as its reason, and this is never shown to a
    reviewer as something needing a decision — there is nothing for a
    human to judge in input that doesn't parse or has a dangling node."""

    plugin_name: str | None
    detail: str


SubmitOutcome = (
    Pending
    | NameOwnedByAnotherRepo
    | AlreadySubmitted
    | plugin_install.TagMismatch
    | plugin_install.FetchFailed
    | InvalidManifest
)


@dataclass(frozen=True)
class Decided:
    plugin_name: str
    tag: str
    status: Literal["approved", "rejected"]


@dataclass(frozen=True)
class UnknownRelease:
    plugin_name: str
    tag: str


@dataclass(frozen=True)
class ReasonRequired:
    """A rejection with no reason — Requirement 3 promises the creator
    always sees one."""


@dataclass(frozen=True)
class AlreadyDecided:
    """`(plugin_name, tag)` already has a decision. Intent.md's own Open
    Questions left "can an approval be taken back" unresolved; refusing
    any re-decision — including overriding an automatic rejection — is
    the reading that adds the least new behavior on top of that gap."""

    plugin_name: str
    tag: str
    status: str


DecideOutcome = Decided | UnknownRelease | ReasonRequired | AlreadyDecided


@dataclass(frozen=True)
class ReleaseListing:
    """What a reviewer or a browser sees for one release — the safe shape
    plus its workflow state."""

    plugin_name: str
    tag: str
    repo_url: str
    revision: str
    manifest: dict[str, object]
    status: str
    reason: str | None
    submitted_at: float
    decided_at: float | None


def _owning_repo_url(conn: sqlite3.Connection, plugin_name: str) -> str | None:
    row = conn.execute(
        "SELECT repo_url FROM marketplace_releases WHERE plugin_name = ? ORDER BY submitted_at ASC LIMIT 1",
        (plugin_name,),
    ).fetchone()
    return row["repo_url"] if row is not None else None


def _insert_release(
    conn: sqlite3.Connection,
    *,
    plugin_name: str,
    tag: str,
    repo_url: str,
    revision: str,
    manifest_json: str,
    status: str,
    reason: str | None,
    now: float,
) -> bool:
    """`False` (nothing written) if `(plugin_name, tag)` already has a row."""
    try:
        with write_txn(conn) as c:
            c.execute(
                "INSERT INTO marketplace_releases "
                "(plugin_name, tag, repo_url, revision, manifest_json, status, reason, submitted_at, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (plugin_name, tag, repo_url, revision, manifest_json, status, reason, now),
            )
    except sqlite3.IntegrityError:
        return False
    return True


class _NameConflict(Exception):
    """Internal-only signal from inside `_claim_release()`'s own
    transaction — never crosses that function's boundary, which returns
    `NameOwnedByAnotherRepo` instead."""

    def __init__(self, owning_repo_url: str) -> None:
        super().__init__(owning_repo_url)
        self.owning_repo_url = owning_repo_url


def _claim_release(
    conn: sqlite3.Connection,
    *,
    plugin_name: str,
    tag: str,
    repo_url: str,
    revision: str,
    manifest_json: str,
    now: float,
) -> Pending | NameOwnedByAnotherRepo | AlreadySubmitted:
    """Checks name ownership and inserts the new `pending` row inside one
    `BEGIN IMMEDIATE` transaction (`write_txn`), so two concurrent
    `submit()` calls on two different connections — exactly what happens
    for two concurrent requests to the marketplace webhook's own
    `ThreadingHTTPServer`, which opens a fresh connection per request —
    can never both observe "nobody owns this name yet" for a brand-new
    name. `BEGIN IMMEDIATE` takes SQLite's write lock at the database
    level, not just within this connection, so a second connection's own
    `BEGIN IMMEDIATE` blocks until this one commits or rolls back."""
    try:
        with write_txn(conn) as c:
            owning = _owning_repo_url(c, plugin_name)
            if owning is not None and owning != repo_url:
                raise _NameConflict(owning)
            c.execute(
                "INSERT INTO marketplace_releases "
                "(plugin_name, tag, repo_url, revision, manifest_json, status, reason, submitted_at, decided_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)",
                (plugin_name, tag, repo_url, revision, manifest_json, now),
            )
    except _NameConflict as exc:
        return NameOwnedByAnotherRepo(plugin_name=plugin_name, owning_repo_url=exc.owning_repo_url)
    except sqlite3.IntegrityError:
        return AlreadySubmitted(plugin_name=plugin_name, tag=tag)
    return Pending(plugin_name=plugin_name, tag=tag)


def submit(conn: sqlite3.Connection, repo_url: str, tag: str, *, now: float) -> SubmitOutcome:
    """Fetch `tag` from `repo_url`, verify it, capture its shape, and
    queue it for review — or auto-reject it, or refuse it, per the
    outcomes above. Never imports or executes anything from `repo_url`."""
    _ensure_schema(conn)
    with tempfile.TemporaryDirectory(prefix="sadana-marketplace-") as tmp:
        dest = Path(tmp) / "release"
        fetched = plugin_install.fetch_verified_tag(repo_url, tag, dest)
        if isinstance(fetched, plugin_install.TagMismatch | plugin_install.FetchFailed):
            return fetched

        outcome = plugin_manifest.validate(fetched.directory, check_bodies=False)

        if isinstance(outcome, plugins.Valid):
            manifest = outcome.manifest
            manifest_json = json.dumps(plugins.manifest_to_dict(manifest))
            return _claim_release(
                conn,
                plugin_name=manifest.name,
                tag=tag,
                repo_url=repo_url,
                revision=fetched.revision,
                manifest_json=manifest_json,
                now=now,
            )

        detail = plugins.describe_manifest_outcome(outcome)
        try:
            manifest = plugins._parse_manifest((fetched.directory / "plugin.toml").read_text(encoding="utf-8"))
        except Exception:
            return InvalidManifest(plugin_name=None, detail=detail)

        manifest_json = json.dumps(plugins.manifest_to_dict(manifest))
        _insert_release(
            conn,
            plugin_name=manifest.name,
            tag=tag,
            repo_url=repo_url,
            revision=fetched.revision,
            manifest_json=manifest_json,
            status="rejected",
            reason=f"automatically rejected: {detail}",
            now=now,
        )
        return InvalidManifest(plugin_name=manifest.name, detail=detail)


def decide(
    conn: sqlite3.Connection,
    plugin_name: str,
    tag: str,
    decision: Literal["approved", "rejected"],
    *,
    reason: str | None = None,
    now: float,
) -> DecideOutcome:
    """A reviewer's decision on a pending release. Rejecting without a
    reason is refused; deciding something already decided is refused."""
    _ensure_schema(conn)
    if decision == "rejected" and not reason:
        return ReasonRequired()

    row = conn.execute(
        "SELECT status FROM marketplace_releases WHERE plugin_name = ? AND tag = ?", (plugin_name, tag)
    ).fetchone()
    if row is None:
        return UnknownRelease(plugin_name=plugin_name, tag=tag)
    if row["status"] != "pending":
        return AlreadyDecided(plugin_name=plugin_name, tag=tag, status=row["status"])

    with write_txn(conn) as c:
        c.execute(
            "UPDATE marketplace_releases SET status = ?, reason = ?, decided_at = ? "
            "WHERE plugin_name = ? AND tag = ?",
            (decision, reason, now, plugin_name, tag),
        )
    return Decided(plugin_name=plugin_name, tag=tag, status=decision)


def _row_to_listing(row: sqlite3.Row) -> ReleaseListing:
    return ReleaseListing(
        plugin_name=row["plugin_name"],
        tag=row["tag"],
        repo_url=row["repo_url"],
        revision=row["revision"],
        manifest=json.loads(row["manifest_json"]),
        status=row["status"],
        reason=row["reason"],
        submitted_at=row["submitted_at"],
        decided_at=row["decided_at"],
    )


def latest_approved(conn: sqlite3.Connection, plugin_name: str) -> ReleaseListing | None:
    """What an ordinary browser sees for `plugin_name` — its most
    recently approved release, or `None` if it has none yet."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM marketplace_releases WHERE plugin_name = ? AND status = 'approved' "
        "ORDER BY decided_at DESC LIMIT 1",
        (plugin_name,),
    ).fetchone()
    return _row_to_listing(row) if row is not None else None


def latest_pending(conn: sqlite3.Connection, plugin_name: str) -> ReleaseListing | None:
    """What a reviewer sees for `plugin_name` when there's something
    awaiting their decision."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM marketplace_releases WHERE plugin_name = ? AND status = 'pending' "
        "ORDER BY submitted_at DESC LIMIT 1",
        (plugin_name,),
    ).fetchone()
    return _row_to_listing(row) if row is not None else None


def pending_releases(conn: sqlite3.Connection) -> tuple[ReleaseListing, ...]:
    """The reviewer queue, oldest first."""
    _ensure_schema(conn)
    rows = conn.execute("SELECT * FROM marketplace_releases WHERE status = 'pending' ORDER BY submitted_at ASC")
    return tuple(_row_to_listing(row) for row in rows.fetchall())


def approved_plugin_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Every plugin name an ordinary browser can find something for."""
    _ensure_schema(conn)
    rows = conn.execute(
        "SELECT DISTINCT plugin_name FROM marketplace_releases WHERE status = 'approved' ORDER BY plugin_name"
    )
    return tuple(row["plugin_name"] for row in rows.fetchall())
