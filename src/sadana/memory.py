"""You shouldn't have to reintroduce yourself every time.

`docs/tasks/MEMORY-01-write-recall-and-forget/spec.md`. Pure, no I/O — the
sqlite-touching half of this block is `memory_store.py`, kept separate per
CLAUDE.md's rule that a module touching real I/O never shares a file with a
block's pure-function module.

Recall and the capture rubric both ride into an existing, already-unused
parameter (`conversation.create_conversation`'s `system_message`) rather
than through any new kernel state — the two render functions below are the
whole of that seam's content.
"""

from __future__ import annotations

from dataclasses import dataclass

from sadana import config

# Caller-supplied; not generated, not validated, not enforced unique here —
# the store's job, the same posture `conversation.ConversationKey` and
# `conversation.TemplateName` already take. Distinct from ConversationKey:
# a conversation is one thread, an account is the person having many
# (CLAUDE.md).
AccountKey = str

_DEFAULT_RUBRIC = "whatever helps you know this person better"


@dataclass(frozen=True)
class MemoryEntry:
    """One remembered fact or decision. Keyed by `(account_key, entry_key)`
    — a natural key, never a synthetic id (CLAUDE.md's names-not-pointers
    rule); `memory_store.py` enforces it as the table's primary key."""

    account_key: AccountKey
    entry_key: str
    content: str
    updated_at: float


def render_recall(entries: tuple[MemoryEntry, ...]) -> str:
    """The recall half of the context tier: one line per entry, empty
    string if there is nothing to recall — the same skip-empty-parts
    posture `conversation._render_context` already uses for the plugin
    catalog line."""
    if not entries:
        return ""
    lines = "\n".join(f"- {entry.content}" for entry in entries)
    return f"What you already know about this person:\n{lines}"


def render_capture_guidance(default_rubric: str, account_override: str) -> str:
    """Tells the model when to call `memory.remember`, using the
    deployer's default rubric plus the account's own addition, if any —
    on top of, never replacing, the default (spec.md requirement 4)."""
    guidance = f"When you notice something worth remembering about this person, call memory.remember. {default_rubric}"
    if account_override:
        guidance = f"{guidance} This person has also asked: {account_override}"
    return guidance


def system_message_for(entries: tuple[MemoryEntry, ...], default_rubric: str, account_override: str) -> str:
    """`render_recall` and `render_capture_guidance`, combined into the one
    string a new conversation's `system_message` actually needs — both real
    callers (`chat.py`, `gateway_dispatch.py`) build exactly this, so it
    lives once here rather than twice at the call sites."""
    return "\n\n".join(
        part for part in (render_recall(entries), render_capture_guidance(default_rubric, account_override)) if part
    )


def account_key_for(platform: str, chat_id: str) -> str:
    """`f"{platform}:{chat_id}"` — deliberately drops the thread id
    `gateway.session_key_for` includes, since an account outlives any one
    conversation thread. A new function in a new module, not an edit to
    `gateway.py` (GATEWAY-DAEMON-01, closed)."""
    return f"{platform}:{chat_id}"


def owner_account() -> AccountKey:
    """The account this installation belongs to — the person running it.

    `SADANA_MEMORY_ACCOUNT`, else `"local"`: the same variable and default
    `subcommands/chat.py` resolved inline before PERSONA-02, so an
    installation that had set it keeps the identity it already had.

    Resolved from config on every call, never stored: nothing in this project
    writes down who the owner is, so nothing goes stale when it changes.

    Three callers need the same answer and would otherwise each pick their
    own: the terminal (whose default this was), the scheduler (work the owner
    set in motion is the owner's work, `scheduling.tick`), and
    `persona use`, which always accepts the owner's own name because choosing
    a voice before you have said anything is an ordinary first move. A
    *channel* is never one of them — an inbound envelope names its own sender
    and can never name the owner (CLAUDE.md).
    """
    return config.env("SADANA_MEMORY_ACCOUNT", "local")


def default_rubric() -> str:
    """The deployer's default rubric — one env var, resolved the way every
    other block resolves its own behaviour config (`config.py`'s own
    docstring: "the resolution primitive every future block uses to build
    its own typed config in its own module")."""
    return config.env("SADANA_MEMORY_DEFAULT_RUBRIC", _DEFAULT_RUBRIC)
