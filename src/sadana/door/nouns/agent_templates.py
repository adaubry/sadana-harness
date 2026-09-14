"""The `agent_templates` noun: the built-in starting point(s) a new voice
can begin from (H26, `docs/tasks/H26-door-nouns-agents-memory/spec.md`).

Not a row. `persona_store.BUILTIN_TEMPLATES` is a closed, hardcoded dict —
this module only renders it — `console_fit_plan.md` §5(a)'s "names, not
pointers" rule applied to the plugin-seam guideline too: a second built-in
template, if one is ever wanted, is a second dict entry in a later work
item, not a registry built now for a consumer that doesn't exist. Lives in
`persona_store.py`, not here, because `door/nouns/agents.py` needs it too
and a noun module never imports another noun module.

Its id is `"tmpl_" + sha256(name)[:32]` — a pure function of the name,
never `ids.make_id` (which would change on every call) and never stored.
It still matches `ids.PREFIXES`/`ids._HEX32`'s shape, so it validates
anywhere the wire format is checked, even though it is not uuid7-derived.
`created_at`/`updated_at` are a fixed epoch-0 timestamp, documented as the
non-fact it is (`console_fit_plan.md` §5(b)'s "a floor, not a fact",
applied here to a value with no real moment behind it at all) rather than
`time.time()`, which would make a stable resource look like it changes on
every read.

Read-only: `create`/`update`/`remove`/`act` all answer
`HARNESS_CAPABILITY_MISSING` via `nouns.unavailable()`.
"""

from __future__ import annotations

from collections.abc import Mapping

from sadana import persona_store
from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import NounSpec, SearchDoc, unavailable

spec = NounSpec(
    plural="agent_templates",
    prefix="tmpl",
    filterable=frozenset(),
    orderable=frozenset({"created_at"}),
    states=frozenset(),
    actions={},
    parent=None,
)

_EPOCH = grammar.render_ts(0.0)


def _render(name: str, tpl: Mapping[str, str], *, harness_id: str) -> dict[str, object]:
    return {
        "id": persona_store.template_id(name),
        "created_at": _EPOCH,
        "updated_at": _EPOCH,
        "tags": {},
        "harness_id": harness_id,
        "version": 1,
        "name": name,
        "description": tpl["description"],
        "system_prompt": tpl["system_prompt"],
    }


def list(
    ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
) -> grammar.ListResponse:
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    rows = [_render(name, tpl, harness_id=harness_id) for name, tpl in persona_store.BUILTIN_TEMPLATES.items()]
    return grammar.page(rows, params)


def get(
    ctx: object, principal: Principal, id: str, parent_id: str | None = None
) -> dict[str, object] | problems.Problem:
    for name, tpl in persona_store.BUILTIN_TEMPLATES.items():
        if persona_store.template_id(name) == id:
            return _render(name, tpl, harness_id=ctx.runtime.harness_id)  # type: ignore[attr-defined]
    return problems.make("NOT_FOUND", f"no agent_template {id}")


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable(spec.plural, "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable(spec.plural, "update")


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable(spec.plural, "remove")


def act(
    ctx: object,
    principal: Principal,
    id: str,
    name: str,
    body: Mapping[str, object],
    if_match: str | None,
) -> problems.Problem:
    return unavailable(spec.plural, name)


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title=str(row.get("name", "")), subtitle=str(row.get("description", "")))
