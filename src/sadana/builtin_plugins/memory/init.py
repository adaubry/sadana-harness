"""memory plugin's one node body: docs/tasks/MEMORY-01-write-recall-and-forget/spec.md.

`write_entry` is the `call` node's body — the one step in this plugin that
reaches outside the process, gated by F1's approval before it ever runs.
The account it writes under comes only from `value["_sadana_memory_ctx"]`,
placed there by `plugin_dispatch.build_dispatch()`'s own trusted merge —
never from a model-supplied argument, so a missing or malformed key here
(anything but a real `DispatchContext`) is a `KeyError`/`AttributeError`,
caught by `run_graph`'s own blanket exception handler and reported as a
`failed_node`, never written anywhere.

`kind` (H26) is the one field the model itself supplies, per
`schema/remember.json` — `memory.normalize_kind` is what keeps a stray or
missing value from ever reaching the store as anything but one of the three
known words. `source`/`conversation_key` are never model-supplied: this is
the only write path there is today, so `source` is always `"conversation"`,
and `conversation_key` comes from the same trusted context as the account.
"""

import time

from sadana import memory, memory_store


def write_entry(value: dict) -> str:
    ctx = value["_sadana_memory_ctx"]
    memory_store.write_entry(
        ctx.conn,
        ctx.account_key,
        value["entry_key"],
        value["content"],
        now=time.time(),
        kind=memory.normalize_kind(value.get("kind")),
        source="conversation",
        conversation_key=ctx.conversation_key,
    )
    return "remembered."
