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
