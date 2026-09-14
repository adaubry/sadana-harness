"""The `schedules` noun: cron-based (and, for now, migrated interval-based)
recurring triggers a console account can create, read, update, pause,
resume and remove through the door (H27,
docs/tasks/H27-door-nouns-schedules/spec.md).

Scoped to the acting account on every read and write —
`conversation_store`'s own ``WHERE id = ? AND account_key = ?`` is the whole
enforcement, so another account's schedule is `404`, never `403` (wire.md's
visibility rule), the same posture `memory_entries.py` already takes.

`create`/`update`/`remove` manually check `schedules.write`: `router.py`
only auto-gates *actions* by capability (`ActionSpec.capability`), never the
generic verbs. `pause`/`resume` get the gate for free that way instead.

`plugin_id`/`conversation_id` are names, not pointers (CLAUDE.md): no
`plugins` noun exists yet to mint a real plugin id, so `plugin_id` here is
the plugin's own registered `name`, validated against
`plugin_manifest.discover_plugins()`; `conversation_id` is the
conversation's own `key`, validated with `conversation_store.exists()` —
`memory_entries.py`'s own `conversation_id` field is the same convention
already in this codebase (spec.md § Design).
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sadana import conversation_store, cron, plugin_manifest, scheduling
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc

CAPABILITIES: tuple[str, ...] = ("schedules.write",)

spec = NounSpec(
    plural="schedules",
    prefix="sch",
    filterable=frozenset({"state", "plugin_id", "created_at"}),
    orderable=frozenset({"created_at"}),
    states=frozenset({"active", "paused"}),
    actions={
        "pause": ActionSpec(
            from_states=("active",), to_state="paused", capability="schedules.write", scope_verb="pause"
        ),
        "resume": ActionSpec(
            from_states=("paused",), to_state="active", capability="schedules.write", scope_verb="resume"
        ),
    },
    parent=None,
)


def _account(principal: Principal) -> str:
    return f"console:{principal.sub}"


def _require_write(ctx: object, verb: str) -> problems.Problem | None:
    """`create`/`update`/`remove` all need this: `router.py` only
    auto-gates *actions* by capability, never the generic verbs."""
    if "schedules.write" not in ctx.capabilities:  # type: ignore[attr-defined]
        return problems.make("HARNESS_CAPABILITY_MISSING", f"schedules.{verb} requires capability schedules.write")
    return None


def _render(row: conversation_store.ScheduleRow, *, harness_id: str) -> dict[str, object]:
    return {
        "id": row.id,
        "created_at": grammar.render_ts(row.created_at),
        "updated_at": grammar.render_ts(row.updated_at),
        "state": row.state,
        "tags": {},
        "harness_id": harness_id,
        "version": row.version,
        "name": row.name,
        "cron": row.cron,
        "timezone": row.timezone,
        "interval_seconds": row.interval_seconds,
        "trigger_text": row.trigger_text,
        "plugin_id": row.plugin,
        "conversation_id": row.conversation_key,
        "next_run_at": grammar.render_ts(row.next_run_at),
        "last_run_at": grammar.render_ts(row.last_run_at) if row.last_run_at is not None else None,
        "last_state": row.last_state,
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    rows = [
        _render(r, harness_id=harness_id)
        for r in conversation_store.list_schedules(ctx.conns.reader(), account_key=_account(principal))  # type: ignore[attr-defined]
    ]
    return grammar.page(rows, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    row = conversation_store.get_schedule(ctx.conns.reader(), account_key=_account(principal), id=id)  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no schedule {id}")
    return _render(row, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def _validate_cron(cron_text: object) -> tuple[cron.Cron | None, dict[str, str] | None]:
    if not isinstance(cron_text, str) or not cron_text:
        return None, {"field": "cron", "code": "invalid_cron"}
    result = cron.parse(cron_text)
    if isinstance(result, cron.CronError):
        return None, {"field": "cron", "code": "invalid_cron"}
    return result, None


def _validate_timezone(timezone: object) -> tuple[str | None, dict[str, str] | None]:
    if not isinstance(timezone, str) or not timezone:
        return None, {"field": "timezone", "code": "invalid_timezone"}
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return None, {"field": "timezone", "code": "invalid_timezone"}
    return timezone, None


def _resolve_plugin(plugin_id: object) -> tuple[str | None, dict[str, str] | None]:
    if not isinstance(plugin_id, str):
        return None, {"field": "plugin_id", "code": "unknown_plugin"}
    names = {p.manifest.name for p in plugin_manifest.discover_plugins()}
    if plugin_id not in names:
        return None, {"field": "plugin_id", "code": "unknown_plugin"}
    return plugin_id, None


def _resolve_conversation(ctx: object, conversation_id: object) -> tuple[str | None, dict[str, str] | None]:
    if not isinstance(conversation_id, str) or not conversation_store.exists(ctx.conns.reader(), conversation_id):  # type: ignore[attr-defined]
        return None, {"field": "conversation_id", "code": "unknown_conversation"}
    return conversation_id, None


def _resolve_plugin_and_conversation(
    ctx: object, body: Mapping[str, object]
) -> tuple[str | None, str | None, tuple[dict[str, str], ...]]:
    """The two optional wire-facing `_id` fields `create`/`update` both
    accept, resolved only when the caller actually sent them — `None`
    otherwise means "not given," never "clear the existing value" (spec.md
    § Design's names-not-pointers section)."""
    errors = []
    plugin: str | None = None
    if "plugin_id" in body:
        plugin, plugin_error = _resolve_plugin(body.get("plugin_id"))
        if plugin_error is not None:
            errors.append(plugin_error)
    conversation_key: str | None = None
    if "conversation_id" in body:
        conversation_key, conversation_error = _resolve_conversation(ctx, body.get("conversation_id"))
        if conversation_error is not None:
            errors.append(conversation_error)
    return plugin, conversation_key, tuple(errors)


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    capability_problem = _require_write(ctx, "create")
    if capability_problem is not None:
        return capability_problem

    errors = []
    name = body.get("name")
    if not isinstance(name, str) or not name:
        errors.append({"field": "name", "code": "required"})
    trigger_text = body.get("trigger_text")
    if not isinstance(trigger_text, str) or not trigger_text:
        errors.append({"field": "trigger_text", "code": "required"})
    parsed_cron, cron_error = _validate_cron(body.get("cron"))
    if cron_error is not None:
        errors.append(cron_error)
    timezone, tz_error = _validate_timezone(body.get("timezone", "UTC"))
    if tz_error is not None:
        errors.append(tz_error)
    plugin, conversation_key, id_errors = _resolve_plugin_and_conversation(ctx, body)
    errors.extend(id_errors)
    if errors:
        return problems.make("VALIDATION", "invalid schedule", errors=tuple(errors))

    cron_text = body.get("cron")
    assert isinstance(name, str) and isinstance(trigger_text, str) and isinstance(cron_text, str)
    assert parsed_cron is not None and timezone is not None
    now = ctx.clock()  # type: ignore[attr-defined]
    next_run_at = cron.next_after(parsed_cron, timezone, now)
    try:
        row = conversation_store.create_schedule(
            ctx.conns.writer,  # type: ignore[attr-defined]
            name=name,
            cron=cron_text,
            timezone=timezone,
            interval_seconds=None,
            trigger_text=trigger_text,
            plugin=plugin,
            conversation_key=conversation_key,
            account_key=_account(principal),
            next_run_at=next_run_at,
            now=now,
        )
    except sqlite3.IntegrityError:
        return problems.make("CONFLICT", f"a schedule named {name!r} already exists")
    return _render(row, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def _check_if_match(row: conversation_store.ScheduleRow, if_match: str | None) -> problems.Problem | None:
    if if_match is None:
        return problems.make("PRECONDITION_FAILED", "If-Match is required for this request")
    if if_match != str(row.version):
        return problems.make("PRECONDITION_FAILED", f"version is {row.version}, If-Match named {if_match}")
    return None


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> dict[str, object] | problems.Problem:
    capability_problem = _require_write(ctx, "update")
    if capability_problem is not None:
        return capability_problem
    account = _account(principal)
    row = conversation_store.get_schedule(ctx.conns.reader(), account_key=account, id=id)  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no schedule {id}")
    precondition = _check_if_match(row, if_match)
    if precondition is not None:
        return precondition

    errors = []
    cron_text: str | None = None
    if "cron" in body:
        _, cron_error = _validate_cron(body.get("cron"))
        if cron_error is not None:
            errors.append(cron_error)
        else:
            cron_text = str(body["cron"])
    timezone = row.timezone
    if "timezone" in body:
        given_tz, tz_error = _validate_timezone(body.get("timezone"))
        if tz_error is not None:
            errors.append(tz_error)
        else:
            assert given_tz is not None
            timezone = given_tz
    plugin, conversation_key, id_errors = _resolve_plugin_and_conversation(ctx, body)
    errors.extend(id_errors)
    if errors:
        return problems.make("VALIDATION", "invalid schedule", errors=tuple(errors))

    now = ctx.clock()  # type: ignore[attr-defined]
    next_run_at: float | None = None
    # A cron or timezone change recomputes `next_run_at` from *now* — never
    # from the row's own stale value — the same "recompute on change" shape
    # `resume` below already takes, and the same function: build the row as
    # it would read after this write and hand it to
    # `scheduling.next_schedule_run`, rather than re-deriving cron/interval
    # branching a second time here.
    if ("cron" in body or "timezone" in body) and (cron_text is not None or row.cron is not None):
        merged = dataclasses.replace(row, cron=cron_text if cron_text is not None else row.cron, timezone=timezone)
        next_run_at = scheduling.next_schedule_run(merged, now=now)

    name = body.get("name")
    trigger_text = body.get("trigger_text")
    try:
        conversation_store.update_schedule(
            ctx.conns.writer,  # type: ignore[attr-defined]
            account_key=account,
            id=id,
            now=now,
            name=name if isinstance(name, str) and name else None,
            cron=cron_text,
            timezone=timezone if "timezone" in body else None,
            plugin=plugin,
            conversation_key=conversation_key,
            trigger_text=trigger_text if isinstance(trigger_text, str) and trigger_text else None,
            next_run_at=next_run_at,
        )
    except sqlite3.IntegrityError:
        # The same `name` collision `create` already reports as `409` —
        # `update_schedule`'s `UPDATE ... SET name = ?` hits the identical
        # `UNIQUE NOT NULL` constraint (a deploy-stage cold review caught
        # this returning an uncaught `500` instead).
        return problems.make("CONFLICT", f"a schedule named {name!r} already exists")
    updated = conversation_store.get_schedule(ctx.conns.reader(), account_key=account, id=id)  # type: ignore[attr-defined]
    assert updated is not None, f"schedule {id} vanished immediately after its own update"
    return _render(updated, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> None | problems.Problem:
    capability_problem = _require_write(ctx, "remove")
    if capability_problem is not None:
        return capability_problem
    account = _account(principal)
    row = conversation_store.get_schedule(ctx.conns.reader(), account_key=account, id=id)  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no schedule {id}")
    precondition = _check_if_match(row, if_match)
    if precondition is not None:
        return precondition
    conversation_store.remove_schedule(ctx.conns.writer, account_key=account, id=id)  # type: ignore[attr-defined]
    return None


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> dict[str, object] | problems.Problem:
    account = _account(principal)
    row = conversation_store.get_schedule(ctx.conns.reader(), account_key=account, id=id)  # type: ignore[attr-defined]
    if row is None:
        return problems.make("NOT_FOUND", f"no schedule {id}")
    if name not in ("pause", "resume"):
        return problems.make("HARNESS_CAPABILITY_MISSING", f"{spec.plural}.{name} is not available on this harness")
    precondition = _check_if_match(row, if_match)
    if precondition is not None:
        return precondition

    now = ctx.clock()  # type: ignore[attr-defined]
    if name == "pause":
        conversation_store.set_schedule_state(
            ctx.conns.writer,  # type: ignore[attr-defined]
            account_key=account,
            id=id,
            to_state="paused",
            now=now,
        )
    else:
        # Resuming always recomputes `next_run_at` from the moment of resume
        # — never from whatever was left showing while paused (spec.md
        # requirement 6; `scheduling.next_schedule_run` is the one place
        # both this and the tick's own post-fire advance compute it).
        next_run_at = scheduling.next_schedule_run(row, now=now)
        conversation_store.set_schedule_state(
            ctx.conns.writer,  # type: ignore[attr-defined]
            account_key=account,
            id=id,
            to_state="active",
            now=now,
            next_run_at=next_run_at,
        )
    updated = conversation_store.get_schedule(ctx.conns.reader(), account_key=account, id=id)  # type: ignore[attr-defined]
    assert updated is not None, f"schedule {id} vanished immediately after its own action"
    return _render(updated, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    cron_text = row.get("cron")
    interval_seconds = row.get("interval_seconds")
    subtitle = str(cron_text) if cron_text else (f"every {interval_seconds}s" if interval_seconds is not None else None)
    return SearchDoc(
        title=str(row.get("name", "")),
        subtitle=subtitle,
        facets={"state": row.get("state"), "plugin": row.get("plugin_id")},
    )
