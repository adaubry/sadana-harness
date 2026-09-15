"""The `budget` noun: the box's own conversation budget, tunable through
the door (H14, `docs/tasks/H14-tuned-config-settings-secrets/spec.md`).

Box-wide, not one per account or per conversation — there is exactly one
active `conversation.iteration_max`/`.wall_clock_s` pair, matching
`conversation.py`'s own budget-from-config functions, which read the same
two dotted keys. `list`/`get` render a *synthetic* default (`id =
"bdg_default"`, `version = 0`) until the first `update`, then a *real* row
— the same one-row-until-first-write shape `memory_policies` (H26) already
uses for an identical problem: no natural row exists for a value that
lives in a flat file, so `id`/`version`/`ETag` need a small identity table
of their own (the CLAUDE.md rule this document's own review added).

`runs_per_day` is never stored — it is the console's own plan quota, and
this box has no opinion about it; always `null`, and a `PATCH` naming it is
`400 VALIDATION`, not silently ignored.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from sadana import config, ids, ledger
from sadana.conversation_store import write_txn
from sadana.door import config_writer, grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, check_if_match, unavailable

spec = NounSpec(
    plural="budgets",
    prefix="bdg",
    filterable=frozenset(),
    orderable=frozenset({"created_at"}),
    states=frozenset(),
    actions={},
    parent=None,
)

_DEFAULT_ID = "bdg_default"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS budgets (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _current(ctx: object) -> dict[str, object]:
    ensure_schema(ctx.conns.reader())  # type: ignore[attr-defined]
    row = ctx.conns.reader().execute("SELECT * FROM budgets").fetchone()  # type: ignore[attr-defined]
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    if row is None:
        now = grammar.render_ts(ctx.clock())  # type: ignore[attr-defined]
        id_, created_at, updated_at, version = _DEFAULT_ID, now, now, 0
    else:
        id_ = row["id"]
        created_at = grammar.render_ts(row["created_at"])
        updated_at = grammar.render_ts(row["updated_at"])
        version = row["version"]
    return {
        "id": id_,
        "created_at": created_at,
        "updated_at": updated_at,
        "tags": {},
        "harness_id": harness_id,
        "version": version,
        "iterations_max": config.get("conversation.iteration_max", 60, env_name="SADANA_CONVERSATION_MAX_ITERATIONS"),
        "wall_clock_seconds": config.get(
            "conversation.wall_clock_s", 0, env_name="SADANA_CONVERSATION_RUN_BUDGET_SECONDS"
        ),
        "runs_per_day": None,
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    return grammar.page([_current(ctx)], params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    current = _current(ctx)
    if current["id"] != id:
        return problems.make("NOT_FOUND", f"no budget {id}")
    return current


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable(spec.plural, "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> dict[str, object] | problems.Problem:
    # router.py only auto-gates *actions* by capability, never the generic
    # verbs (schedules.py's own `_require_write` comment) — this noun has
    # no actions, so `update` must check `settings.write` itself.
    if "settings.write" not in ctx.capabilities:  # type: ignore[attr-defined]
        return problems.make("HARNESS_CAPABILITY_MISSING", "budgets.update requires capability settings.write")
    current = _current(ctx)
    if current["id"] != id:
        return problems.make("NOT_FOUND", f"no budget {id}")
    precondition = check_if_match(current, if_match)
    if precondition is not None:
        return precondition
    if "runs_per_day" in body:
        return problems.make("VALIDATION", "runs_per_day is read-only")

    iterations_max = body.get("iterations_max")
    if iterations_max is not None and (isinstance(iterations_max, bool) or not isinstance(iterations_max, int)):
        return problems.make("VALIDATION", "iterations_max must be an integer")
    wall_clock_seconds = body.get("wall_clock_seconds")
    if wall_clock_seconds is not None and (
        isinstance(wall_clock_seconds, bool) or not isinstance(wall_clock_seconds, int)
    ):
        return problems.make("VALIDATION", "wall_clock_seconds must be an integer")
    if iterations_max is None and wall_clock_seconds is None:
        return current

    path = config.get_paths().config_dir / "config.toml"
    data = config.raw_toml()
    if iterations_max is not None:
        data = config_writer.apply(data, "conversation.iteration_max", iterations_max)
    if wall_clock_seconds is not None:
        data = config_writer.apply(data, "conversation.wall_clock_s", wall_clock_seconds)
    config_writer.write(path, data)

    now = ctx.clock()  # type: ignore[attr-defined]
    with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
        row = c.execute("SELECT * FROM budgets").fetchone()
        if row is None:
            new_id = ids.make_id("bdg")
            c.execute(
                "INSERT INTO budgets (id, created_at, updated_at, version) VALUES (?, ?, ?, 1)", (new_id, now, now)
            )
            ledger.record_change(c, noun="budgets", id=new_id, kind="created", state=None, version=1, at=now)
        else:
            new_version = row["version"] + 1
            c.execute("UPDATE budgets SET updated_at = ?, version = ? WHERE id = ?", (now, new_version, row["id"]))
            ledger.record_change(
                c, noun="budgets", id=row["id"], kind="changed", state=None, version=new_version, at=now
            )
    return _current(ctx)


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable(spec.plural, "remove")


def act(
    ctx: object, principal: Principal, id: str, name: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable(spec.plural, name)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title="Budget", facets={"iterations_max": row.get("iterations_max")})
