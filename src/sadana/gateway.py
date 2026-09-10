"""What a message arriving from outside sadana looks like, and how it maps
onto a conversation.

`docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/spec.md`. Pure, no
I/O, no import of `conversation.py` or anything below it — the one module
every channel adapter and the dispatch bridge both depend on, kept as small
as this item actually needs (`MessageEvent`, `session_key_for()`). No
adapter interface, no registry: this item ships exactly one adapter, and
CLAUDE.md's own rule is that a registry for a family of pluggable backends
earns its cost only once a second real member exists to register.

`session_key_for()` returns a plain `str`, not `conversation.ConversationKey`
— that alias is `str` itself (`conversation.py:30`), and importing it here
just for a type hint would violate this module's own "no import of
conversation.py" rule for no behavioral gain.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class MessageEvent:
    """One inbound message, from whatever channel received it.
    `platform` is always `"webhook"` for this item's one adapter — kept as a
    real field, not hardcoded downstream, so a second adapter later needs no
    change here."""

    platform: str
    chat_id: str
    thread_id: str | None
    text: str


def session_key_for(event: MessageEvent) -> str:
    """A pure function of the inbound envelope, matching
    `session.py::build_session_key`'s own shape (hermes's GATEWAY-DAEMON
    block) trimmed to what one platform needs: always `:`-delimited,
    `platform:chat_id`, with `:thread_id` appended only when given — never a
    live reference to anything, this project's own "names, not pointers for
    anything long-lived" rule."""
    if event.thread_id is not None:
        return f"{event.platform}:{event.chat_id}:{event.thread_id}"
    return f"{event.platform}:{event.chat_id}"


def header_value(headers: Mapping[str, str], name: str) -> str:
    """HTTP header names are case-insensitive (RFC 7230 §3.2); a plain
    `Mapping[str, str]` is not. Shared by every channel adapter that reads
    an incoming webhook's headers (`channel_webhook.py`,
    `marketplace_webhook.py`, PLUGIN-MARKET-01) rather than each keeping
    its own copy — moved here once a second real caller existed."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""
