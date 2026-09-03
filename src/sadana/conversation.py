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

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

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
