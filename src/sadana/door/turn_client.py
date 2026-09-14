"""The one new client of `client_surface.take_turn()` the door adds.

`docs/tasks/H20-door-nouns-turn-side/spec.md` requirement 10. Calls
`take_turn` and nothing deeper — no `conversation_store`, no
`plugin_dispatch` import (Rules: "the new client file calls take_turn and
nothing deeper"). `messages.py` is the only caller; everything else about
the turn (the pre/post reads, the `run_id` backfill, the failure-path
message state) lives in `messages.py` itself, which carries no such
restriction.

`observer` (H21, `docs/tasks/H21-watched-streaming-live-runs-stop/spec.md`)
is a one-line passthrough to `client_surface.take_turn` — the observer
object itself is built in `messages.py`, which already has everything it
needs (`conn`, `conversation_key`, `run_control`); this file stays as
narrow as this docstring already requires.
"""

from __future__ import annotations

from sadana import client_surface, memory
from sadana.conversation import ConversationKey, TurnObserver


async def send(
    runtime: client_surface.Runtime,
    *,
    account: memory.AccountKey,
    conversation: ConversationKey,
    text: str,
    observer: TurnObserver | None = None,
) -> client_surface.TurnOutcome:
    return await client_surface.take_turn(
        runtime,
        account=account,
        conversation=conversation,
        text=text,
        create_as=None,
        observer=observer,
    )
