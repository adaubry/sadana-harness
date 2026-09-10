"""memory plugin's one node body: docs/tasks/MEMORY-01-write-recall-and-forget/spec.md.

`write_entry` is the `call` node's body — the one step in this plugin that
reaches outside the process, gated by F1's approval before it ever runs.
The account it writes under comes only from `value["_sadana_memory_ctx"]`,
placed there by `plugin_dispatch.build_dispatch()`'s own trusted merge —
never from a model-supplied argument, so a missing or malformed key here
(anything but a real `DispatchContext`) is a `KeyError`/`AttributeError`,
caught by `run_graph`'s own blanket exception handler and reported as a
`failed_node`, never written anywhere.
"""

import time

from sadana import memory_store


def write_entry(value: dict) -> str:
    ctx = value["_sadana_memory_ctx"]
    memory_store.write_entry(ctx.conn, ctx.account_key, value["entry_key"], value["content"], now=time.time())
    return "remembered."
