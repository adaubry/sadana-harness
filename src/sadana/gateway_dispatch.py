"""Translates a channel's own `MessageEvent` into a call on the one door.

`docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/spec.md`, rebuilt on
`docs/tasks/CLIENT-SURFACE-01-one-door-in/spec.md`. The one module that
imports both `gateway.py` and `client_surface.py`, and it holds exactly one
thing: the mapping from a channel envelope to the account, conversation key
and template name the door requires a caller to state.

Everything it used to do — load-or-create, build the dispatch, run the turn,
persist it, resume an outstanding pause — is now
`client_surface.take_turn()`, which `subcommands/chat.py` calls too. This
module's own docstring used to record that `chat.py` was a direct copy of
that body; the copy is what CLIENT-SURFACE-01 deleted, along with the drift
it had already grown.

Still a function rather than two inline call sites: `channel_webhook` (via
`subcommands/gateway.py`) and `scheduling.py` both deliver a `MessageEvent`,
so this mapping has two real callers.

`conn_lock` moved to `client_surface.py`, with the body it guards.
"""

from __future__ import annotations

from sadana import client_surface, memory
from sadana.gateway import MessageEvent, session_key_for


async def handle_inbound(
    runtime: client_surface.Runtime, event: MessageEvent, *, account: memory.AccountKey | None = None
) -> tuple[bool, str]:
    """Run one turn for one inbound channel message and return
    `(ok, text)` — `ok` is the turn's own, and `text` is the reply to send.

    The three things a channel knows and the door will not guess:
    `session_key_for(event)` names the conversation (platform, chat id, and
    the thread id when there is one); `memory.account_key_for(platform,
    chat_id)` names the person, deliberately dropping the thread id because
    an account outlives any one thread; and `"webhook"` is the template name
    a conversation gets created under, which is what every conversation
    reaching the agent through this bridge has always been called — including
    one a scheduled trigger starts (`scheduling.py` fires a `MessageEvent`
    through here rather than owning a second path).

    `text` is `answer or diagnostic`: the turn's own words when it produced
    any, else the rendered `[exit_reason] detail` line. `ok` cannot be
    recovered from `text` after the fact — a `BUDGET_EXHAUSTED` turn still
    carries a real summary — which is why both values cross back.

    `account` overrides that derivation, and exists for exactly one caller:
    `scheduling.tick()`, where the work was set in motion by the owner rather
    than by whoever is on the other end of a socket, so it runs as the owner
    (PERSONA-02). It is keyword-only and has no field on `MessageEvent` on
    purpose — a keyword can only be passed by a Python caller that already
    decided who this is, while an envelope field gets filled by whatever
    parses the payload next. CLAUDE.md: an inbound channel envelope never
    carries the account it belongs to. A channel adapter must never pass this.

    Never raises for an expected turn outcome. Cannot raise
    `ConversationNotFound` either: a channel always passes `create_as`, since
    a message arriving for a conversation nobody has started yet is the
    normal case, not an error.
    """
    outcome = await client_surface.take_turn(
        runtime,
        account=account if account is not None else memory.account_key_for(event.platform, event.chat_id),
        conversation=session_key_for(event),
        text=event.text,
        create_as="webhook",
    )
    return outcome.ok, outcome.answer or outcome.diagnostic
