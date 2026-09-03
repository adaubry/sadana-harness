"""A conversation's message history that cannot become malformed.

CONV-01 of the CONVERSATION block (`docs/reference/conversation_block_blueprint.md`
section 7); its full contract is `docs/tasks/C2-keys-transcript/spec.md`.

A conversation's history is a value — ``tuple[Message, ...]`` — never a
shared mutable object this module owns. ``append()`` and ``repair()`` are
the only two ways a history may grow; both take a history and return a new
one, or raise. Per CLAUDE.md: a conversation's message history is mutated
only through these two functions, never by direct list or tuple mutation
elsewhere.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Literal

from sadana import config, model_access

# A caller-supplied natural key, e.g. "support/ticket-4821". This module
# does not mint or validate one, and does not enforce it is unique — that
# needs a store, which is CONV-08's job.
ConversationKey = str


@dataclass(frozen=True)
class MessageKey:
    """A message's position in its conversation's history. ``msg_seq`` is
    never stored — every caller derives it as ``len(messages) - 1`` after
    an append, which cannot drift from the history it describes."""

    conversation: ConversationKey
    msg_seq: int


@dataclass(frozen=True)
class Message:
    """One row in a conversation's history.

    ``tool_calls`` reuses ``model_access.Response.tool_calls``'s exact wire
    shape (``tuple[dict, ...]``, each dict carrying at least ``id`` and
    ``name``) rather than a second type for the same data. MODEL-ACCESS is
    the boundary responsible for every call arriving with a non-empty,
    parsed id — this module trusts that and does not re-check it.
    """

    role: Literal["user", "assistant", "tool"]
    content: str | None = None
    tool_calls: tuple[dict, ...] = ()
    tool_call_id: str | None = None  # only meaningful when role == "tool"


class TranscriptInvariantError(Exception):
    """Raised by ``append`` when a message would break the history's shape.
    Always raised before any mutation is observable — ``messages`` is a
    tuple, so there is nothing to roll back."""


_REPAIR_MARKER = "[repaired] interrupted before execution"


def pending_tool_call_ids(messages: tuple[Message, ...]) -> frozenset[str]:
    """The tool_call ids the most recent assistant message asked for that
    no ``tool`` row has answered yet.

    Bounded to the tail *after* the last assistant message carrying
    ``tool_calls`` — not the whole history — because a provider can reuse a
    short id string (e.g. ``"call_1"``) across separate tool rounds; a
    global scan would treat an earlier round's id as already paired for a
    later round that reused it.
    """
    last_call_index = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.role == "assistant" and msg.tool_calls:
            last_call_index = i
            break
    if last_call_index is None:
        return frozenset()

    declared = {tc["id"] for tc in messages[last_call_index].tool_calls}
    answered = {m.tool_call_id for m in messages[last_call_index + 1 :] if m.role == "tool"}
    return frozenset(declared - answered)


def append(
    conversation: ConversationKey,
    messages: tuple[Message, ...],
    message: Message,
) -> tuple[tuple[Message, ...], MessageKey]:
    """Append ``message`` and return the new history and its key, or raise
    ``TranscriptInvariantError`` and leave ``messages`` untouched."""
    pending = pending_tool_call_ids(messages)

    if pending:
        if message.role != "tool" or message.tool_call_id not in pending:
            raise TranscriptInvariantError(
                f"{len(pending)} tool_call id(s) still unanswered "
                f"({sorted(pending)}); only a matching tool result may be "
                f"appended, got role={message.role!r} "
                f"tool_call_id={message.tool_call_id!r}"
            )
    elif message.role == "tool":
        raise TranscriptInvariantError(
            f"no tool_call is pending; nothing to pair " f"tool_call_id={message.tool_call_id!r} against"
        )

    new_messages = messages + (message,)
    return new_messages, MessageKey(conversation=conversation, msg_seq=len(new_messages) - 1)


def repair(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Close every tool_call id an interruption left unanswered, in the
    order they were declared. A no-op — returns ``messages`` itself,
    unchanged — when nothing is pending. This is the only mutation allowed
    on a conversation's history before a turn begins; never called from
    inside ``append``, so the gap it closes stays visible rather than
    silently patched over."""
    last_call_index = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.role == "assistant" and msg.tool_calls:
            last_call_index = i
            break
    if last_call_index is None:
        return messages

    pending = pending_tool_call_ids(messages)
    if not pending:
        return messages

    declared_order = [tc["id"] for tc in messages[last_call_index].tool_calls if tc["id"] in pending]
    repaired = messages
    for call_id in declared_order:
        repaired = repaired + (Message(role="tool", content=_REPAIR_MARKER, tool_call_id=call_id),)
    return repaired


# ── CONV-02: tool surface ────────────────────────────────────────────────
# Its full contract is `docs/tasks/C3-tool-surface/spec.md`.

# key -> current name, for one build_surface() call only. A tool's describe()
# looks another tool up by key, never by writing its name as a literal.
ResolvedNames = Mapping[str, str]

# Provider-format tool definitions, rendered once by build_surface(). Not a
# wrapper class: build_surface()/filter_surface()/surface_hash() are the
# only three things a caller needs, and a tuple[dict, ...] already is
# exactly what model_access.Request.tools accepts.
ToolSurface = tuple[dict, ...]


@dataclass(frozen=True)
class ToolSpec:
    """Metadata for one action a model can be told about.

    ``key`` is required, with no default derived from ``name`` — a default
    would silently defeat cross-referencing for any author who didn't think
    to set one explicitly. No ``handler``/``concurrency``/``effects``/
    ``max_result_chars`` fields: this work item declined dispatch entirely,
    so those fields would have no reader here (same precedent C2 set for
    ``TurnKey``).
    """

    key: str  # stable; never sent to a provider
    name: str  # provider-facing; unique per build; may change across builds
    parameters: dict  # JSON schema for the tool's arguments
    describe: Callable[[ResolvedNames], str]


class DuplicateToolError(Exception):
    """Raised by ``build_surface`` when two specs share a ``name`` or a
    ``key``. Always raised before any description is rendered."""


def build_surface(specs: Iterable[ToolSpec]) -> ToolSurface:
    """Render a fixed list of tool definitions from ``specs``.

    Two-pass: every spec's ``key``/``name`` is known before any
    ``describe()`` runs, so a spec earlier in ``specs`` can reference one
    that appears later. Raises ``DuplicateToolError`` if any two specs
    share a ``name`` or a ``key``. ``ToolSpec``/``describe`` objects are not
    retained past this call — once definitions are rendered, this module
    has no further use for them.
    """
    spec_list = tuple(specs)

    names = [s.name for s in spec_list]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise DuplicateToolError(f"duplicate tool name(s): {dupes}")

    keys = [s.key for s in spec_list]
    if len(keys) != len(set(keys)):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise DuplicateToolError(f"duplicate tool key(s): {dupes}")

    resolved_names: ResolvedNames = {s.key: s.name for s in spec_list}
    return tuple(
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.describe(resolved_names),
                "parameters": s.parameters,
            },
        }
        for s in spec_list
    )


def filter_surface(surface: ToolSurface, names: frozenset[str]) -> ToolSurface:
    """A smaller surface restricted to ``names``, preserving order.

    Never re-renders a description — there is nothing left to re-render;
    ``build_surface`` already discarded the ``ToolSpec``/``describe``
    objects. An empty result (no match) is a valid, un-erroring return."""
    return tuple(d for d in surface if d["function"]["name"] in names)


def surface_hash(surface: ToolSurface) -> str:
    """``sha256`` of ``surface``'s definitions, deterministic regardless of
    dict key insertion order. Derived fresh on every call, never stored —
    same choice as ``MessageKey.msg_seq``."""
    canonical = json.dumps(list(surface), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── CONV-03: budgets ─────────────────────────────────────────────────────
# Its full contract is `docs/tasks/C4-budgets/spec.md`.


@dataclass(frozen=True)
class IterationBudget:
    """How many more actions a conversation's run is allowed to take.

    Immutable, not hermes's mutable-plus-lock shape: the lock in hermes's
    ``agent/iteration_budget.py`` protects one instance from concurrent
    mutation by more than one thread, a scenario this project's
    async-first loop design removes. No ``refund`` — hermes's only existed
    for ``execute_code`` turns, a tool this project doesn't have.
    """

    max_total: int
    used: int = 0


def consume_iteration(budget: IterationBudget) -> IterationBudget | None:
    """One more iteration used, or ``None`` if ``budget`` was already at
    ``max_total``. Never raises — an exhausted budget is a normal outcome
    of a run, not a broken invariant."""
    if budget.used >= budget.max_total:
        return None
    return replace(budget, used=budget.used + 1)


def iteration_budget_from_config() -> IterationBudget:
    """Reads ``SADANA_CONVERSATION_MAX_ITERATIONS`` (default 60, matching
    `docs/reference/conversation_block_blueprint.md` §5.7). Raises
    ``ValueError`` for a negative configured value rather than building a
    budget nobody could ever consume from."""
    max_total = config.env_int("SADANA_CONVERSATION_MAX_ITERATIONS", 60)
    if max_total < 0:
        raise ValueError(f"SADANA_CONVERSATION_MAX_ITERATIONS={max_total} must not be negative")
    return IterationBudget(max_total=max_total)


@dataclass(frozen=True)
class WallClockBudget:
    """A deadline a conversation's run should not run past.

    ``deadline`` must be on the same clock as every ``now`` passed to
    ``wall_clock_remaining`` — ``time.monotonic()``, never ``time.time()``.
    Nothing here enforces that; it is a caller responsibility, the same
    kind of contract ``MessageKey.msg_seq`` and ``surface_hash`` place on
    their callers without a runtime check.
    """

    deadline: float


def wall_clock_remaining(budget: WallClockBudget, now: float) -> float:
    """Seconds left before ``budget.deadline``, given the caller's own
    ``now``. Possibly negative once exhausted — the caller compares the
    result to 0 itself. Pure: never reads the real clock, so a test can
    call this with any ``now`` it likes."""
    return budget.deadline - now


def wall_clock_budget_from_config(now: float) -> WallClockBudget | None:
    """Reads ``SADANA_CONVERSATION_RUN_BUDGET_SECONDS`` (default 0,
    translating `docs/reference/conversation_block_blueprint.md` §5.7's
    ``run_budget_seconds: null``). Returns ``None`` when unset or 0 — a
    0-second budget is not a coherent allotment, so it is free to mean
    "disabled" without a new ``config.py`` primitive. Raises ``ValueError``
    for a negative value, matching ``iteration_budget_from_config``."""
    seconds = config.env_int("SADANA_CONVERSATION_RUN_BUDGET_SECONDS", 0)
    if seconds < 0:
        raise ValueError(f"SADANA_CONVERSATION_RUN_BUDGET_SECONDS={seconds} must not be negative")
    if seconds == 0:
        return None
    return WallClockBudget(deadline=now + seconds)


# ── CONV-04: provider port ───────────────────────────────────────────────
# Its full contract is `docs/tasks/C6-provider-port/spec.md`.


class ContextOverflow(Exception):
    """The request was too large for the model's context window."""


class ProviderFailure(Exception):
    """This attempt failed outright — not retryable by this function."""


@dataclass(frozen=True)
class Completion:
    """A model's response, with every tool call already carrying a real id
    and already-parsed arguments — never raw text a caller has to trust."""

    content: str | None
    tool_calls: tuple[dict, ...]  # {"id": str, "name": str, "arguments": dict}
    finish_reason: str
    usage: model_access.Usage


def _escape_invalid_chars_in_json_strings(raw: str) -> str:
    """Ported from hermes ``agent/message_sanitization.py:144-183``. Walks
    ``raw`` char by char; inside a JSON string literal, replaces a literal
    control character with its ``\\uXXXX`` escape. Pass-through elsewhere."""
    out: list[str] = []
    in_string = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(raw[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
        i += 1
    return "".join(out)


def repair_tool_call_arguments(raw: str) -> dict:
    """Parse a tool call's raw ``arguments`` string into a dict, repairing
    common malformations a real provider can send. Ported from hermes
    ``agent/message_sanitization.py:195-293`` (``_repair_tool_call_arguments``),
    adapted to return a parsed dict instead of a repaired string, and with
    no logging — this project has no logging convention yet (spec.md's
    Rejected alternatives). Never raises, and always returns a dict — a
    pass that parses valid JSON that isn't an object (a bare array,
    number, string, null, or bool) does not count as a success; the
    cascade falls through to the next pass, and ultimately to ``{}``,
    rather than handing back a non-dict value."""
    raw_stripped = raw.strip() if isinstance(raw, str) else ""

    if not raw_stripped or raw_stripped == "None":
        return {}

    # Pass 0: strict=False accepts literal control chars inside strings.
    try:
        parsed = json.loads(raw_stripped, strict=False)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Pass 1-3: strip trailing commas, close unclosed brackets, trim excess
    # closing brackets (bounded — never loop unboundedly on adversarial input).
    fixed = re.sub(r",\s*([}\]])", r"\1", raw_stripped)
    open_curly = fixed.count("{") - fixed.count("}")
    open_bracket = fixed.count("[") - fixed.count("]")
    if open_curly > 0:
        fixed += "}" * open_curly
    if open_bracket > 0:
        fixed += "]" * open_bracket
    for _ in range(50):
        try:
            json.loads(fixed)
            break
        except json.JSONDecodeError:
            if (
                fixed.endswith("}")
                and fixed.count("}") > fixed.count("{")
                or fixed.endswith("]")
                and fixed.count("]") > fixed.count("[")
            ):
                fixed = fixed[:-1]
            else:
                break
    try:
        parsed = json.loads(fixed)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Pass 4: escape unescaped control chars inside strings, retry.
    try:
        escaped = _escape_invalid_chars_in_json_strings(fixed)
        if escaped != fixed:
            parsed = json.loads(escaped)
            if isinstance(parsed, dict):
                return parsed
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    return {}


def deterministic_call_id(name: str, arguments: str, index: int = 0) -> str:
    """A stable id for a tool call a provider omitted one for. Ported from
    hermes ``agent/message_sanitization.py:654-663``. Never ``uuid4`` — a
    random id would change on every retry of the same call, invalidating a
    provider's prompt cache."""
    seed = f"{name}:{arguments}:{index}"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"call_{digest}"


def coalesce_tool_call_id(tool_call: dict) -> str:
    """The effective id of a raw tool_call dict, or ``""`` if neither field
    is set. Ported from hermes ``agent/message_sanitization.py:713-731``,
    dict-only (every tool_call this project sees is already a parsed-JSON
    dict). Tries ``call_id`` then ``id``; a bridged id can arrive as
    ``"call_id|response_item_id"``, so this splits on ``"|"`` and keeps the
    first half."""
    for key in ("call_id", "id"):
        raw = tool_call.get(key)
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if value:
            return value.split("|", 1)[0].strip() or value
    return ""


def uniquify_tool_call_ids(tool_calls: tuple[dict, ...]) -> tuple[dict, ...]:
    """If two calls in ``tool_calls`` share an ``id``, the first keeps it
    and every later collision gets a deterministic ``_d<n>`` suffix — never
    random, same prompt-cache reasoning as ``deterministic_call_id``.
    Ported from hermes ``agent/message_sanitization.py:734-810``."""
    seen: set[str] = set()
    result = []
    for tc in tool_calls:
        call_id = tc["id"]
        if call_id not in seen:
            seen.add(call_id)
            result.append(tc)
            continue
        n = 2
        new_id = f"{call_id}_d{n}"
        while new_id in seen:
            n += 1
            new_id = f"{call_id}_d{n}"
        seen.add(new_id)
        result.append({**tc, "id": new_id})
    return tuple(result)


def _resolve_tool_calls(raw_tool_calls: tuple[dict, ...]) -> tuple[dict, ...]:
    """The full id-resolution/argument-repair pipeline for one assistant
    message's tool_calls, per spec.md's Design step 4."""
    resolved = []
    missing_index = 0
    for tc in raw_tool_calls:
        fn = tc.get("function") or {}
        name = fn.get("name") or "?"
        raw_arguments = fn.get("arguments") or ""
        call_id = coalesce_tool_call_id(tc)
        if not call_id:
            call_id = deterministic_call_id(name, raw_arguments, missing_index)
            missing_index += 1
        resolved.append({"id": call_id, "name": name, "arguments": repair_tool_call_arguments(raw_arguments)})
    return uniquify_tool_call_ids(tuple(resolved))


async def complete(
    *,
    system: str,
    messages: tuple[dict, ...],
    tools: tuple[dict, ...],
    provider: str,
    model: str,
) -> Completion:
    """Ask a model for a response. Retries a transient failure
    transparently — the caller never sees ``model_access.Retry`` — and
    raises exactly one of two named failures otherwise. No retry cap of
    its own: ``model_access.classify()`` already enforces
    ``SADANA_MODEL_ACCESS_MAX_RETRIES`` and returns ``Abort`` once
    exceeded, so this loop's termination is already guaranteed."""
    request_messages = ({"role": "system", "content": system},) + messages
    attempt = 0
    while True:
        request = model_access.Request(
            messages=request_messages, provider=provider, model=model, tools=tools, attempt=attempt
        )
        outcome = await asyncio.to_thread(model_access.send, request)

        if isinstance(outcome, model_access.Retry):
            attempt = outcome.next_attempt
            continue
        if isinstance(outcome, model_access.NeedsContextCompression):
            raise ContextOverflow(outcome.detail)
        if isinstance(
            outcome,
            model_access.NeedsCredentialOrProviderChange | model_access.Abort | model_access.Degenerate,
        ):
            raise ProviderFailure(outcome.detail)

        return Completion(
            content=outcome.content,
            tool_calls=_resolve_tool_calls(outcome.tool_calls),
            finish_reason=outcome.finish_reason,
            usage=outcome.usage,
        )
