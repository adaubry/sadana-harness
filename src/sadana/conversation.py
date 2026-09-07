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
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Literal

from sadana import config, context, model_access, plugin_manifest, plugins

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
    is_summary: bool = False  # True only for a message compact() produced


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


# ── C11: compaction ──────────────────────────────────────────────────────
# Its full contract is `docs/tasks/C11-context-completion/spec.md`.


def find_compaction_boundary(messages: tuple[Message, ...], keep_tail_count: int) -> int:
    """The largest safe cut index: ``messages[index:]`` holds at least
    ``keep_tail_count`` messages when the history is long enough, and never
    starts on an orphaned ``tool`` row — walked backward past any leading
    ``tool``-role messages, the same idiom ``pending_tool_call_ids``/
    ``repair`` already use, so a compaction cut can never separate an
    assistant message's ``tool_calls`` from any of its answers. Pure, no
    I/O; trusted as-is by ``compact()``."""
    index = max(0, len(messages) - keep_tail_count)
    while 0 < index < len(messages) and messages[index].role == "tool":
        index -= 1
    return index


def compact(messages: tuple[Message, ...], tail_start: int, summary_text: str) -> tuple[Message, ...]:
    """The one sanctioned way to replace, rather than grow, a
    conversation's history — CLAUDE.md: mutated only through
    ``append``/``repair``/``compact``. Used once, when CONTEXT decides an
    oversized conversation needs shortening. ``tail_start`` must already be
    a safe boundary (never inside a tool_calls group) — get one from
    ``find_compaction_boundary``, never compute it ad hoc; this function
    trusts it rather than re-validating shape it didn't produce, the same
    posture ``append`` already has toward a caller-supplied ``Message``."""
    return (Message(role="user", content=summary_text, is_summary=True),) + messages[tail_start:]


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
    cache_hint: context.CacheHint | None = None,
) -> Completion:
    """Ask a model for a response. Retries a transient failure
    transparently — the caller never sees ``model_access.Retry`` — and
    raises exactly one of two named failures otherwise. Retrying itself is
    ``model_access.resolve()``'s job now, not this function's own loop —
    CLAUDE.md: a retry loop over a provider's ``Retry`` outcome lives once,
    in MODEL-ACCESS, never duplicated per caller. No retry cap of its own:
    ``model_access.classify()`` already enforces
    ``SADANA_MODEL_ACCESS_MAX_RETRIES`` and returns ``Abort`` once
    exceeded, so ``resolve()``'s own termination is already guaranteed.

    ``cache_hint``, when given, is applied to the wire messages via
    ``model_access.mark_cache_boundary`` before the request is built —
    including a retry, so a retried attempt still carries the marker."""
    request_messages = ({"role": "system", "content": system},) + messages
    if cache_hint is not None:
        request_messages = model_access.mark_cache_boundary(request_messages, cache_hint)
    request = model_access.Request(messages=request_messages, provider=provider, model=model, tools=tools)
    outcome = await model_access.resolve(request)

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


# ── CONV-05: turn loop ───────────────────────────────────────────────────
# Its full contract is `docs/tasks/C7-turn-loop/spec.md`.


class PromptDriftError(Exception):
    """A turn's ``system_prompt``/``tool_surface`` don't hash to the
    caller's own ``prompt_sha256``. Always a bug — this never becomes an
    ``ExitReason``."""


class ExitReason(Enum):
    COMPLETED = "completed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    WALL_CLOCK_EXHAUSTED = "wall_clock_exhausted"
    PERSISTENCE_FAILED = "persistence_failed"
    PROVIDER_FAILED = "provider_failed"
    CONTEXT_OVERFLOW_UNHANDLED = "context_overflow_unhandled"
    INTERRUPTED = "interrupted"
    INVALID_TOOL_CALLS = "invalid_tool_calls"


@dataclass(frozen=True)
class TurnKey:
    """Defined here, not sooner — C2's own spec.md named this work item
    as the first with a real turn-boundary caller."""

    conversation: ConversationKey
    turn_seq: int


@dataclass(frozen=True)
class TurnResult:
    """What one ``run_turn`` call did. Carries ``turn_key`` only, not a
    second, redundant ``conversation_key`` field — ``turn_key`` already
    names the conversation."""

    turn_key: TurnKey
    final_text: str | None
    exit_reason: ExitReason
    detail: str | None
    model_calls: int
    usage: model_access.Usage
    appended: range
    context_state: context.ContextState


def turn_prompt_hash(system_prompt: str, tool_surface: ToolSurface) -> str:
    """``sha256(system_prompt + the tool surface's canonical JSON)`` — the
    same canonicalization ``surface_hash()`` already uses, so the two stay
    consistent with each other. This, not ``system_prompt`` alone, is what
    a turn's byte-stability check covers."""
    canonical = json.dumps(list(tool_surface), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((system_prompt + canonical).encode("utf-8")).hexdigest()


def _deduplicate_tool_calls(tool_calls: tuple[dict, ...]) -> tuple[dict, ...]:
    """Drop a later call sharing the same ``(name, arguments)`` as an
    earlier one in the same batch, keeping the first occurrence. Adapted
    from hermes ``run_agent.py:5103``, simplified: hermes canonicalizes
    still-raw argument *strings* inside a ``try/except``; here,
    ``arguments`` is already a parsed dict (C6's own guarantee), so
    canonicalizing it for comparison is a plain, always-safe
    ``json.dumps()`` call."""
    seen: set[tuple[str, str]] = set()
    unique = []
    for tc in tool_calls:
        canonical_args = json.dumps(tc["arguments"], sort_keys=True, separators=(",", ":"))
        key = (tc["name"], canonical_args)
        if key not in seen:
            seen.add(key)
            unique.append(tc)
    return tuple(unique)


async def _noop_persist(messages: tuple[Message, ...]) -> None:
    """The default ``persist``: durability doesn't exist yet (CONV-08's
    job) so this does nothing and always succeeds."""
    return None


def _message_to_wire(message: Message) -> dict:
    """Convert one ``Message`` into the dict shape ``model_access.Request.messages``
    (and so ``complete()``) expects — the actual wire format a provider
    receives. An assistant message's ``tool_calls`` carry already-parsed
    ``arguments`` (``Completion``'s own shape); the wire format needs them
    re-serialized to a JSON string, since that is what a real
    OpenAI-compatible API expects on the way out."""
    wire: dict = {"role": message.role}
    if message.content is not None:
        wire["content"] = message.content
    if message.role == "assistant" and message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
            }
            for tc in message.tool_calls
        ]
    if message.role == "tool" and message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def _add_usage(a: model_access.Usage, b: model_access.Usage) -> model_access.Usage:
    return model_access.Usage(
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
    )


def _cap_tool_result(text: str, turn_chars_used: int, result_cap: int, turn_cap: int) -> tuple[str, int]:
    """Cap ``text`` against the per-result limit, then again against the
    turn's running total. Returns the (possibly capped) text and the
    updated running total. A capped result says it was capped."""
    if len(text) > result_cap:
        text = text[:result_cap] + f"\n[capped: over the {result_cap}-char per-result limit]"
    remaining = turn_cap - turn_chars_used
    if remaining <= 0:
        text = f"[capped: the turn's {turn_cap}-char tool-result budget is already used up]"
    elif len(text) > remaining:
        text = text[:remaining] + f"\n[capped: over the turn's {turn_cap}-char tool-result budget]"
    return text, turn_chars_used + len(text)


async def _append_invalid_results(
    conversation: ConversationKey,
    messages: tuple[Message, ...],
    invalid: tuple[dict, ...],
    turn_chars_used: int,
    result_cap: int,
    turn_cap: int,
    context_state: context.ContextState,
) -> tuple[tuple[Message, ...], int]:
    """Append a capped ``tool_error`` result for each invalid call — shared
    by both TOOL_ROUND branches (the all-invalid exit and the mixed
    continue), the same logic rather than two copies to keep in sync.

    Routes through ``context.after_tool_result`` first, same as the valid-
    dispatch loop: ``tc["name"]`` is model-supplied and not bounded by
    anything here, so this content is just as eligible for spilling as a
    real dispatch result — cold review caught that this path bypassed the
    checkpoint entirely, a real gap against requirement 2 (never silently
    truncated with the rest permanently lost)."""
    for tc in invalid:
        raw = Message(role="tool", tool_call_id=tc["id"], content=f"tool_error: unknown tool {tc['name']!r}")
        tool_result = await context.after_tool_result(context_state, raw)
        capped_text, turn_chars_used = _cap_tool_result(
            tool_result.content or "", turn_chars_used, result_cap, turn_cap
        )
        messages, _ = append(conversation, messages, replace(tool_result, content=capped_text))
    return messages, turn_chars_used


_SUMMARY_REQUEST_TEXT = (
    "You've reached the maximum number of tool-calling iterations allowed. "
    "Please provide a final response summarizing what you've found and "
    "accomplished so far, without calling any more tools."
)


async def run_turn(
    *,
    conversation: ConversationKey,
    turn_seq: int,
    messages: tuple[Message, ...],
    user_input: str,
    system_prompt: str,
    prompt_sha256: str,
    tool_surface: ToolSurface,
    iteration_budget: IterationBudget,
    wall_clock_budget: WallClockBudget | None,
    now: float,
    provider: str,
    model: str,
    dispatch: Callable[[str, dict], Awaitable[plugins.DagResult]],
    context_state: context.ContextState,
    stable_prompt_len: int,
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
) -> tuple[TurnResult, tuple[Message, ...], IterationBudget, str]:
    """Run one turn: ask the model, carry out whatever it asks for, ask
    again if needed, until one of ``ExitReason``'s eight members ends it.
    Returns ``(TurnResult, updated messages, updated iteration_budget,
    current system_prompt)`` — four values with different lifetimes and
    different callers, kept separate rather than folded into one
    dataclass. Full contract: ``docs/tasks/C7-turn-loop/spec.md``.

    ``context_state``/``stable_prompt_len`` replace the old injected
    ``compress`` callback (`docs/tasks/C10-context-lifecycle/spec.md`):
    CONTEXT's own checkpoints are called directly instead of through a
    caller-supplied seam. The turn's final ``context_state`` rides on the
    returned ``TurnResult``, not as a fifth value here — see plan.md's own
    refinement note."""
    # REPAIR — the only mutation allowed before PROLOGUE.
    messages = repair(messages)

    # PROLOGUE
    if turn_prompt_hash(system_prompt, tool_surface) != prompt_sha256:
        raise PromptDriftError(f"system_prompt/tool_surface hash does not match prompt_sha256={prompt_sha256!r}")
    messages, _ = append(conversation, messages, Message(role="user", content=user_input))
    turn_start_seq = len(messages) - 1

    model_calls = 0
    usage_total = model_access.Usage()
    result_cap = config.env_int("SADANA_CONVERSATION_TOOL_RESULT_CHARS", 100_000)
    turn_cap = config.env_int("SADANA_CONVERSATION_TOOL_TURN_BUDGET_CHARS", 200_000)
    if result_cap < 0:
        raise ValueError(f"SADANA_CONVERSATION_TOOL_RESULT_CHARS={result_cap} must not be negative")
    if turn_cap < 0:
        raise ValueError(f"SADANA_CONVERSATION_TOOL_TURN_BUDGET_CHARS={turn_cap} must not be negative")
    turn_chars_used = 0

    exit_reason: ExitReason | None = None
    detail: str | None = None
    final_text: str | None = None

    try:
        while True:
            new_budget = consume_iteration(iteration_budget)
            if new_budget is None:
                exit_reason = ExitReason.BUDGET_EXHAUSTED
                detail = f"iteration budget exhausted ({iteration_budget.max_total})"
                break
            iteration_budget = new_budget

            if wall_clock_budget is not None and wall_clock_remaining(wall_clock_budget, now) <= 0:
                exit_reason = ExitReason.WALL_CLOCK_EXHAUSTED
                detail = "wall clock budget exhausted"
                break

            cache_hint = context.before_send(history=messages, stable_prompt_len=stable_prompt_len)
            try:
                completion = await complete(
                    system=system_prompt,
                    messages=tuple(_message_to_wire(m) for m in messages),
                    tools=tool_surface,
                    provider=provider,
                    model=model,
                    cache_hint=cache_hint,
                )
            except ContextOverflow as e:
                model_calls += 1
                tail_start = find_compaction_boundary(messages, context.compaction_tail_messages_from_config())
                turn_complete_result = await context.turn_complete(
                    context_state, messages, system_prompt, tail_start, provider, model
                )
                context_state = turn_complete_result.context_state
                handled = False
                if turn_complete_result.new_summary_text is not None:
                    messages = compact(messages, tail_start, turn_complete_result.new_summary_text)
                    turn_start_seq = min(turn_start_seq, len(messages))
                    handled = True
                if turn_complete_result.new_system_prompt is not None:
                    system_prompt = turn_complete_result.new_system_prompt
                    handled = True
                if not handled:
                    exit_reason = ExitReason.CONTEXT_OVERFLOW_UNHANDLED
                    detail = str(e)
                    break
                continue
            except ProviderFailure as e:
                model_calls += 1
                exit_reason = ExitReason.PROVIDER_FAILED
                detail = str(e)
                break

            model_calls += 1
            usage_total = _add_usage(usage_total, completion.usage)
            context_state = context.after_response(context_state, completion.usage)

            if not completion.tool_calls:
                messages, _ = append(conversation, messages, Message(role="assistant", content=completion.content))
                exit_reason = ExitReason.COMPLETED
                final_text = completion.content
                break

            # TOOL_ROUND (spec.md's own numbered steps)
            deduped = _deduplicate_tool_calls(completion.tool_calls)
            valid_names = {d["function"]["name"] for d in tool_surface}
            valid = tuple(tc for tc in deduped if tc["name"] in valid_names)
            invalid = tuple(tc for tc in deduped if tc["name"] not in valid_names)

            if not valid:
                messages, _ = append(
                    conversation,
                    messages,
                    Message(role="assistant", content=completion.content, tool_calls=deduped),
                )
                messages, turn_chars_used = await _append_invalid_results(
                    conversation, messages, invalid, turn_chars_used, result_cap, turn_cap, context_state
                )
                exit_reason = ExitReason.INVALID_TOOL_CALLS
                detail = f"no valid tool call in a batch of {len(deduped)}"
                break

            messages, _ = append(
                conversation,
                messages,
                Message(role="assistant", content=completion.content, tool_calls=deduped),
            )
            try:
                await persist(messages)
            except Exception as e:
                exit_reason = ExitReason.PERSISTENCE_FAILED
                detail = str(e)
                break

            messages, turn_chars_used = await _append_invalid_results(
                conversation, messages, invalid, turn_chars_used, result_cap, turn_cap, context_state
            )

            for tc in valid:
                try:
                    dag_result = await dispatch(tc["name"], tc["arguments"])
                    result_text = dag_result.text
                except Exception as e:
                    result_text = f"tool_error: {e}"
                # after_tool_result runs on the raw result first (it may
                # spill an oversized one to disk, leaving a short
                # reference) — _cap_tool_result is the final safety-net
                # cap on whatever it decided to leave in context, not the
                # other way around: capping first would truncate the very
                # data spilling exists to preserve.
                tool_result = await context.after_tool_result(
                    context_state, Message(role="tool", tool_call_id=tc["id"], content=result_text)
                )
                capped_text, turn_chars_used = _cap_tool_result(
                    tool_result.content or "", turn_chars_used, result_cap, turn_cap
                )
                messages, _ = append(conversation, messages, replace(tool_result, content=capped_text))
            # loop back to MODEL_CALL
    except asyncio.CancelledError:
        exit_reason = ExitReason.INTERRUPTED
        detail = "turn cancelled"

    assert exit_reason is not None  # every break/except path above sets it

    # EPILOGUE — the one named exception to "no model call outside MODEL_CALL".
    if exit_reason == ExitReason.BUDGET_EXHAUSTED and final_text is None:
        summary_messages, _ = append(conversation, messages, Message(role="user", content=_SUMMARY_REQUEST_TEXT))
        try:
            cache_hint = context.before_send(history=summary_messages, stable_prompt_len=stable_prompt_len)
            completion = await complete(
                system=system_prompt,
                messages=tuple(_message_to_wire(m) for m in summary_messages),
                tools=(),
                provider=provider,
                model=model,
                cache_hint=cache_hint,
            )
            model_calls += 1
            usage_total = _add_usage(usage_total, completion.usage)
            context_state = context.after_response(context_state, completion.usage)
            messages, _ = append(conversation, summary_messages, Message(role="assistant", content=completion.content))
            final_text = completion.content
        except (ContextOverflow, ProviderFailure):
            pass  # best-effort — exit_reason stays BUDGET_EXHAUSTED, final_text stays None
        except asyncio.CancelledError:
            # Same guarantee the main loop already makes: no exception escapes
            # run_turn uncancelled. Overrides BUDGET_EXHAUSTED — the caller
            # explicitly asked to stop, which is more informative than "ran
            # out of budget" for a summary attempt that never got to finish.
            exit_reason = ExitReason.INTERRUPTED
            detail = "turn cancelled during epilogue summary"

    result = TurnResult(
        turn_key=TurnKey(conversation=conversation, turn_seq=turn_seq),
        final_text=final_text,
        exit_reason=exit_reason,
        detail=detail,
        model_calls=model_calls,
        usage=usage_total,
        appended=range(turn_start_seq, len(messages)),
        context_state=context_state,
    )
    return result, messages, iteration_budget, system_prompt


# ── CONV-06: conversation aggregate + template ──────────────────────────
# Its full contract is `docs/tasks/C8-conversation-aggregate/spec.md`.

# A caller-minted natural key for a starting point conversations are
# created from — same posture as `ConversationKey`: not generated, not
# validated, not enforced unique here. That needs a store, CONV-08's job.
TemplateName = str


@dataclass(frozen=True)
class PluginCatalogEntry:
    """One caller-supplied fact about an outside procedure. Nothing here
    discovers, loads, or validates one — a future PLUGINS block is the real
    producer of this data; this work item only defines the three fields
    blueprint §8 Open Question 1 named. ``entry_tool`` is stored as a
    literal string, not resolved against a ``ToolSurface``'s resolved
    names — see spec.md's Open questions."""

    name: str
    purpose: str  # one sentence, rendered verbatim
    entry_tool: str


@dataclass(frozen=True)
class TemplateRecipe:
    """Everything a starting point contributes to a conversation's prompt
    and tool surface, as one atomic bundle — not three separately-pending
    fields (spec.md's Rejected alternatives: no reader needs a partial
    change yet, so one bundle is fewer bets and less state)."""

    stable_prompt: str
    catalog: tuple[PluginCatalogEntry, ...]
    tool_specs: tuple[ToolSpec, ...]


@dataclass(frozen=True)
class ConversationTemplate:
    """A starting point conversations are created from. Frozen, like every
    other value in this module: recording a deferred change is a function
    returning a new value, never an in-place mutation — the same shape
    ``IterationBudget``'s ``consume_iteration`` already established, and
    CLAUDE.md's explicit rule for a consumable resource."""

    name: TemplateName
    recipe: TemplateRecipe
    pending_recipe: TemplateRecipe | None = None


def defer_invalidation(template: ConversationTemplate, new_recipe: TemplateRecipe) -> ConversationTemplate:
    """Records ``new_recipe`` as ``template``'s pending replacement.
    Returns a new ``ConversationTemplate`` value; ``template`` itself, and
    every ``Conversation`` already built from it, are unreachable from this
    call — not merely untouched by this call's own code path, but
    unreachable by construction, since ``Conversation`` holds only
    ``template.name``, never ``template`` itself (spec.md's central design
    choice — see its Design section, "structural removal")."""
    return replace(template, pending_recipe=new_recipe)


class PromptRotationReason(Enum):
    """The one member blueprint §4.3 names. A second member is a visible
    diff at every call site that already pattern-matches on this enum —
    that visibility, not the value itself, is what this type is for."""

    COMPRESSION = "compression"


@dataclass(frozen=True)
class Conversation:
    """One continuing conversation. Holds ``template_name``, never a
    ``ConversationTemplate`` — see ``defer_invalidation`` above."""

    key: ConversationKey
    template_name: TemplateName
    system_prompt: str
    prompt_sha256: str
    prompt_epoch: int
    tool_surface: ToolSurface
    messages: tuple[Message, ...]
    next_turn_seq: int
    iteration_budget: IterationBudget
    wall_clock_budget: WallClockBudget | None
    stable_prompt_len: int
    context_state: context.ContextState
    next_child_seq: int = 0  # CONV-07's own counter; see run_child below.


def _render_context(system_message: str, catalog: tuple[PluginCatalogEntry, ...]) -> str:
    """The context tier: a caller-supplied message plus one rendered line
    per catalog entry, each part skipped if empty (blueprint §4.3)."""
    catalog_lines = "\n".join(f"{e.name}: {e.purpose} (start with {e.entry_tool})" for e in catalog)
    return "\n\n".join(part for part in (system_message, catalog_lines) if part)


def create_conversation(
    template: ConversationTemplate,
    key: ConversationKey,
    system_message: str,
    *,
    iteration_budget: IterationBudget,
    wall_clock_budget: WallClockBudget | None = None,
) -> tuple[Conversation, ConversationTemplate]:
    """If ``template.pending_recipe`` is set, it is promoted to
    ``template.recipe`` and cleared — this call, not ``defer_invalidation``'s
    call, is "the next conversation created from the same template" per
    blueprint §4.3. Builds ``tool_surface`` from the (possibly just-promoted)
    recipe's ``tool_specs``, composes ``system_prompt`` from
    ``recipe.stable_prompt`` plus the rendered context tier, and hashes it
    with ``turn_prompt_hash`` — the same function C7 already defined, not a
    second one. Returns the new ``Conversation`` (epoch 0, empty history,
    ``next_turn_seq=0``) alongside the template value the caller should keep
    using next; ``template`` itself is never mutated."""
    recipe = template.pending_recipe if template.pending_recipe is not None else template.recipe
    updated_template = replace(template, recipe=recipe, pending_recipe=None)

    tool_surface = build_surface(recipe.tool_specs)
    context_tier = _render_context(system_message, recipe.catalog)
    system_prompt = "\n\n".join(part for part in (recipe.stable_prompt, context_tier) if part)
    prompt_sha256 = turn_prompt_hash(system_prompt, tool_surface)

    conversation = Conversation(
        key=key,
        template_name=template.name,
        system_prompt=system_prompt,
        prompt_sha256=prompt_sha256,
        prompt_epoch=0,
        tool_surface=tool_surface,
        messages=(),
        next_turn_seq=0,
        iteration_budget=iteration_budget,
        wall_clock_budget=wall_clock_budget,
        stable_prompt_len=len(recipe.stable_prompt),
        context_state=context.ContextState(),
    )
    return conversation, updated_template


def rotate_prompt(conversation: Conversation, *, reason: PromptRotationReason, new_prompt: str) -> Conversation:
    """The only function in this module that increments ``prompt_epoch``.
    Recomputes ``prompt_sha256`` against ``new_prompt`` and the
    conversation's existing ``tool_surface`` so the result is
    self-consistent for the very next turn's own PROLOGUE check. ``reason``
    selects behavior at the call site (there being only one member today)
    and is not stored — no reader exists yet (spec.md's Rejected
    alternatives)."""
    del reason  # accepted for call-site clarity only; see docstring.
    return replace(
        conversation,
        system_prompt=new_prompt,
        prompt_sha256=turn_prompt_hash(new_prompt, conversation.tool_surface),
        prompt_epoch=conversation.prompt_epoch + 1,
    )


async def take_turn(
    conversation: Conversation,
    *,
    user_input: str,
    provider: str,
    model: str,
    dispatch: Callable[[str, dict], Awaitable[plugins.DagResult]],
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
    now: float,
) -> tuple[TurnResult, Conversation]:
    """Calls C7's ``run_turn`` with every value it needs, read off
    ``conversation``. If the returned ``system_prompt`` differs from
    ``conversation.system_prompt`` (compression rotated it mid-turn), the
    result goes through ``rotate_prompt`` first, so epoch and hash always
    move together through the one sanctioned path; ``messages``,
    ``iteration_budget``, ``context_state``, and ``next_turn_seq + 1`` are
    then folded in. Never mutates ``conversation`` — returns a new value."""
    result, messages, iteration_budget, new_system_prompt = await run_turn(
        conversation=conversation.key,
        turn_seq=conversation.next_turn_seq,
        messages=conversation.messages,
        user_input=user_input,
        system_prompt=conversation.system_prompt,
        prompt_sha256=conversation.prompt_sha256,
        tool_surface=conversation.tool_surface,
        iteration_budget=conversation.iteration_budget,
        wall_clock_budget=conversation.wall_clock_budget,
        now=now,
        provider=provider,
        model=model,
        dispatch=dispatch,
        context_state=conversation.context_state,
        stable_prompt_len=conversation.stable_prompt_len,
        persist=persist,
    )

    updated = conversation
    if new_system_prompt != conversation.system_prompt:
        updated = rotate_prompt(updated, reason=PromptRotationReason.COMPRESSION, new_prompt=new_system_prompt)

    updated = replace(
        updated,
        messages=messages,
        next_turn_seq=conversation.next_turn_seq + 1,
        iteration_budget=iteration_budget,
        context_state=result.context_state,
    )
    return result, updated


# ── CONV-07: child conversation ──────────────────────────────────────────
# Its full contract is `docs/tasks/C9-child-conversation/spec.md`.
# SkillRef, SkillLoadError, _plugins_root, _skill_path, _parse_skill_md and
# load_skill moved to plugins.py/plugin_manifest.py — see D2's spec.md.


class ChildDepthExceeded(Exception):
    """A spawn's derived depth exceeds the configured maximum. Raised
    before any budget, transcript, or model-call state is touched — same
    posture as ``PromptDriftError``: a structural refusal, not a run
    outcome, so it is never an ``ExitReason``."""


def _child_key(parent: ConversationKey, node_name: str, seq: int) -> ConversationKey:
    """A child's key always embeds its parent's key and the node that
    spawned it — the lineage is in the name (blueprint §4.1), not a
    foreign key anything has to join."""
    return f"{parent}/child/{node_name}/{seq}"


def _conversation_depth(key: ConversationKey) -> int:
    """How many ``run_child`` hops produced ``key``, recovered by
    counting ``"child"`` path segments rather than stored anywhere — a
    stored counter could only ever agree with, never usefully disagree
    from, what the key ``_child_key`` built already encodes. Caveat, not
    enforced: a ``node_name`` that is itself exactly ``"child"`` would be
    miscounted by one; ``node_name`` is short and caller-chosen, and
    nothing external supplies one today."""
    return sum(1 for segment in key.split("/") if segment == "child")


def child_iteration_budget_from_config() -> IterationBudget:
    """Reads ``SADANA_CONVERSATION_CHILD_MAX_ITERATIONS`` (default 20,
    matching blueprint §5.7). Same negative-value ``ValueError`` posture
    as ``iteration_budget_from_config``. Only the default a caller falls
    back to when ``ChildSpec.budget`` is ``None`` — a caller-supplied
    value is never clamped against this."""
    max_total = config.env_int("SADANA_CONVERSATION_CHILD_MAX_ITERATIONS", 20)
    if max_total < 0:
        raise ValueError(f"SADANA_CONVERSATION_CHILD_MAX_ITERATIONS={max_total} must not be negative")
    return IterationBudget(max_total=max_total)


def child_max_depth_from_config() -> int:
    """Reads ``SADANA_CONVERSATION_CHILD_MAX_DEPTH`` (default 2, matching
    blueprint §5.7). Unlike the iteration budget, this is a real ceiling
    with no per-spawn override: depth bounds recursive spawning across
    many children, a scenario no self-exhausting budget catches on its
    own."""
    max_depth = config.env_int("SADANA_CONVERSATION_CHILD_MAX_DEPTH", 2)
    if max_depth < 0:
        raise ValueError(f"SADANA_CONVERSATION_CHILD_MAX_DEPTH={max_depth} must not be negative")
    return max_depth


@dataclass(frozen=True)
class ChildSpec:
    """One bounded sub-task a caller wants run. ``budget`` and ``model``
    default to ``None`` — inherit/config-default — but ``tools`` has no
    default: a caller must say what a child may touch."""

    node_name: str
    skill: plugins.SkillRef
    input: str
    tools: frozenset[str]
    budget: IterationBudget | None = None
    model: str | None = None


# What run_child returns for its single turn. Not a new type: TurnResult
# already carries the child's own key (turn_key.conversation) — see
# spec.md's Rejected alternatives.
ChildResult = TurnResult


_CHILD_TASK_FRAMING = (
    "You are a focused subagent, launched to complete one bounded task "
    "and then stop. Make a reasonable choice and proceed rather than "
    "asking a clarifying question. When you are done, report plainly: "
    "what you did, what you found, what (if anything) changed, and any "
    "issues you ran into. Nothing else reads your intermediate steps, "
    "so make your final answer stand on its own."
)


async def run_child(
    parent: Conversation,
    spec: ChildSpec,
    *,
    stable_prompt: str,
    provider: str,
    model: str,
    dispatch: Callable[[str, dict], Awaitable[plugins.DagResult]],
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
    now: float,
) -> tuple[ChildResult, Conversation, Conversation]:
    """Run one closed, single-skill sub-task to completion.

    Returns ``(the child's turn result, the child's own updated
    Conversation, the parent's updated Conversation)`` — three values
    with three different lifetimes, kept separate rather than folded into
    one dataclass, the same call ``run_turn``'s own docstring already
    made. ``parent.messages`` is never read from or written to beyond
    this; the only difference between ``parent`` and the returned updated
    parent is ``next_child_seq``. Raises ``ChildDepthExceeded`` before
    anything else if the spawn would nest too deep; raises
    ``plugins.SkillLoadError`` (via ``plugin_manifest.load_skill``) before
    anything else if ``spec.skill`` doesn't resolve to a valid SKILL.md.

    ``stable_prompt`` is a plain caller-supplied string, the same posture
    ``create_conversation``'s ``system_message`` already has — this
    module never discovers it from ``parent.template_name``, since
    ``Conversation`` deliberately holds only the name, never the
    template (C8's own design).
    """
    depth = _conversation_depth(parent.key) + 1
    if depth > child_max_depth_from_config():
        raise ChildDepthExceeded(f"spawning {spec.node_name!r} would reach depth {depth}")

    seq = parent.next_child_seq
    updated_parent = replace(parent, next_child_seq=seq + 1)
    child_key = _child_key(parent.key, spec.node_name, seq)

    skill_body = plugin_manifest.load_skill(spec.skill)
    system_prompt = "\n\n".join(part for part in (stable_prompt, skill_body, _CHILD_TASK_FRAMING) if part)
    tool_surface = filter_surface(parent.tool_surface, spec.tools)
    prompt_sha256 = turn_prompt_hash(system_prompt, tool_surface)

    child = Conversation(
        key=child_key,
        template_name=parent.template_name,
        system_prompt=system_prompt,
        prompt_sha256=prompt_sha256,
        prompt_epoch=0,
        tool_surface=tool_surface,
        messages=(),
        next_turn_seq=0,
        iteration_budget=spec.budget if spec.budget is not None else child_iteration_budget_from_config(),
        wall_clock_budget=parent.wall_clock_budget,
        stable_prompt_len=len(stable_prompt),
        context_state=context.ContextState(),
    )

    result, updated_child = await take_turn(
        child,
        user_input=spec.input,
        provider=provider,
        model=spec.model if spec.model is not None else model,
        dispatch=dispatch,
        persist=persist,
        now=now,
    )
    return result, updated_child, updated_parent
