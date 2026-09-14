"""The `agents` noun: which voice an account speaks in, and the draft an
account is still writing (H26, `docs/tasks/H26-door-nouns-agents-memory/spec.md`).

Characters stay files with an index row over them (H16, `persona_store.py`);
this module is the console-facing read/write surface over that same index
and directory, never a second copy of a character's text.

**Live reconciliation.** `stores.reconcile_indexes`'s own docstring named
this work item as where "a character or plugin file added while a
long-running process is up" gets caught: `list`/`get` call
`persona_store.reconcile_agents` against the writer connection before
reading, so a file added by hand while the door is serving shows up on the
very next request rather than only after the process restarts. Cheap in the
common case — reconciliation is a content-hash compare that skips every
unchanged file.

**`system_prompt` is the file's raw text; `rendered_prompt` is computed, never
stored.** For the account's current selection it is
`persona_store.resolve_voice`'s own output; for every other agent it is the
same raw text as `system_prompt` — this item makes what is already computed
visible, not a predictor for every character's counterfactual render
(spec.md § Design). Neither survives a conversation already created: this
module never touches `conversation_store`, so the byte-stability contract
CLAUDE.md names is simply out of this module's reach, not something it has
to enforce.

**`remove` is declined outright.** Characters are not deleted through the
door yet — `nouns.unavailable()` says so honestly rather than a stub that
pretends to.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from sadana import persona, persona_store
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc, unavailable

spec = NounSpec(
    plural="agents",
    prefix="agt",
    filterable=frozenset({"state", "template_id", "created_at"}),
    orderable=frozenset({"created_at"}),
    states=frozenset({"draft", "active"}),
    actions={
        # No confirmation semantics attached — the ordinary next step after
        # writing a draft, not the "erases the record" caution `remove`
        # would be if it existed.
        "activate": ActionSpec(
            from_states=("draft",), to_state="active", capability=None, scope_verb="activate", consequential=False
        ),
        "set-default": ActionSpec(
            from_states=("active",), to_state="active", capability=None, scope_verb="set-default"
        ),
    },
    parent=None,
)


def _account(principal: Principal) -> str:
    """`console_fit_plan.md` §5(d): the acting account for a console
    principal, never parsed out of a request body."""
    return f"console:{principal.sub}"


def _characters_dir() -> Path:
    return persona_store.characters_dir_from_config()


def _reconcile(ctx: object) -> None:
    persona_store.reconcile_agents(ctx.conns.writer, _characters_dir())  # type: ignore[attr-defined]


def _render(
    row: persona_store.AgentRow, *, harness_id: str, conn: object, account: str, characters_dir: Path
) -> dict[str, object] | None:
    """`None` when the row's file has gone missing or broken since it was
    indexed — a race between reconciliation and a concurrent delete, or a
    row reconciliation hasn't caught up to yet. The caller renders that as
    `NOT_FOUND`, matching `list_characters`' own skip-what-can't-be-read
    posture."""
    try:
        system_prompt = persona_store.read_character_text(characters_dir, row.name)
    except persona.CharacterError:
        return None
    is_default = persona_store.get_selection(conn, account) == row.name  # type: ignore[arg-type]
    rendered_prompt = persona_store.resolve_voice(conn, account, characters_dir) if is_default else system_prompt  # type: ignore[arg-type]
    return {
        "id": row.id,
        "created_at": grammar.render_ts(row.created_at),
        "updated_at": grammar.render_ts(row.updated_at),
        "state": row.state,
        "tags": {},
        "harness_id": harness_id,
        "version": row.version,
        "name": row.name,
        "description": row.description,
        "template_id": persona_store.template_id(row.template) if row.template else None,
        "system_prompt": system_prompt,
        "rendered_prompt": rendered_prompt,
        "is_default": is_default,
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    _reconcile(ctx)
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    characters_dir = _characters_dir()
    account = _account(principal)
    rows = [
        rendered
        for row in persona_store.list_agent_rows(reader)
        if (
            rendered := _render(row, harness_id=harness_id, conn=reader, account=account, characters_dir=characters_dir)
        )
        is not None
    ]
    return grammar.page(rows, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    _reconcile(ctx)
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    row = persona_store.get_agent_row(reader, id)
    if row is None:
        return problems.make("NOT_FOUND", f"no agent {id}")
    rendered = _render(
        row,
        harness_id=harness_id,
        conn=reader,
        account=_account(principal),
        characters_dir=_characters_dir(),
    )
    if rendered is None:
        return problems.make("NOT_FOUND", f"no agent {id}")
    return rendered


def _resolve_template_name(given: object) -> str | None:
    """`template_id` in a `create` body resolves back to the built-in
    template's own *name* before it is stored — `console_fit_plan.md`
    §5(a): the column holds the name, the wire field is a derived id, never
    the reverse. `None` for an absent/empty value; a `template_id` that
    doesn't resolve to any known template is the caller's problem to report
    as `400 VALIDATION`."""
    if not given:
        return None
    for name in persona_store.BUILTIN_TEMPLATES:
        if persona_store.template_id(name) == given:
            return name
    return None


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    name = body.get("name")
    system_prompt = body.get("system_prompt")
    if not isinstance(name, str) or not name:
        return problems.make("VALIDATION", "name is required")
    if not isinstance(system_prompt, str) or not system_prompt:
        return problems.make("VALIDATION", "system_prompt is required")
    template_id_given = body.get("template_id")
    template_name = None
    if template_id_given is not None:
        template_name = _resolve_template_name(template_id_given)
        if template_name is None:
            return problems.make("VALIDATION", f"template_id {template_id_given!r} does not name a known template")
    description = body.get("description") or ""
    try:
        row = persona_store.create_character(
            ctx.conns.writer,  # type: ignore[attr-defined]
            _characters_dir(),
            name,
            system_prompt,
            template=template_name,
            description=str(description),
        )
    except persona.CharacterError as e:
        return problems.make("CONFLICT", str(e))
    rendered = _render(
        row,
        harness_id=ctx.runtime.harness_id,  # type: ignore[attr-defined]
        conn=ctx.conns.reader(),  # type: ignore[attr-defined]
        account=_account(principal),
        characters_dir=_characters_dir(),
    )
    assert rendered is not None, f"agent {row.id} unreadable immediately after create"
    return rendered


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> dict[str, object] | problems.Problem:
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    row = persona_store.get_agent_row(reader, id)
    if row is None:
        return problems.make("NOT_FOUND", f"no agent {id}")
    if if_match is None:
        return problems.make("PRECONDITION_FAILED", "If-Match is required for this request")
    if if_match != str(row.version):
        return problems.make("PRECONDITION_FAILED", f"version is {row.version}, If-Match named {if_match}")
    content = body.get("system_prompt")
    description = body.get("description")
    updated = persona_store.update_character(
        ctx.conns.writer,  # type: ignore[attr-defined]
        _characters_dir(),
        row.name,
        content=content if isinstance(content, str) else None,
        description=description if isinstance(description, str) else None,
    )
    rendered = _render(
        updated,
        harness_id=ctx.runtime.harness_id,  # type: ignore[attr-defined]
        conn=ctx.conns.reader(),  # type: ignore[attr-defined]
        account=_account(principal),
        characters_dir=_characters_dir(),
    )
    assert rendered is not None, f"agent {updated.id} unreadable immediately after update"
    return rendered


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable(spec.plural, "remove")


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> dict[str, object] | problems.Problem:
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    row = persona_store.get_agent_row(reader, id)
    if row is None:
        return problems.make("NOT_FOUND", f"no agent {id}")
    if if_match is None:
        return problems.make("PRECONDITION_FAILED", "If-Match is required for this request")
    if if_match != str(row.version):
        return problems.make("PRECONDITION_FAILED", f"version is {row.version}, If-Match named {if_match}")

    if name == "activate":
        persona_store.set_agent_state(ctx.conns.writer, id, "active")  # type: ignore[attr-defined]
    elif name == "set-default":
        persona_store.set_selection(ctx.conns.writer, _account(principal), row.name, now=ctx.clock())  # type: ignore[attr-defined]
    else:
        return problems.make("HARNESS_CAPABILITY_MISSING", f"{spec.plural}.{name} is not available on this harness")

    updated = persona_store.get_agent_row(ctx.conns.reader(), id)  # type: ignore[attr-defined]
    assert updated is not None, f"agent {id} vanished mid-action"
    rendered = _render(
        updated,
        harness_id=ctx.runtime.harness_id,  # type: ignore[attr-defined]
        conn=ctx.conns.reader(),  # type: ignore[attr-defined]
        account=_account(principal),
        characters_dir=_characters_dir(),
    )
    assert rendered is not None, f"agent {id} unreadable immediately after {name}"
    return rendered


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(
        title=str(row.get("name", "")),
        subtitle=str(row.get("description") or ""),
        facets={"state": row.get("state"), "template": row.get("template_id")},
    )
