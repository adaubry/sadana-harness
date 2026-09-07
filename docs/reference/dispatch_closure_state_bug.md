# The dispatch-closure state bug in `scripts/prove_conversation_e2e.py`

Found during CONV-09's deploy review (`docs/tasks/CONV-09-deploy-evidence/review.md`,
Important finding #1). This document explains the root cause on its own,
separately from the review, because the cause is a general pattern this
project's later blocks (PLUGIN-SYSTEM, any real DAG engine) will run into
again — not a one-off typo. Written before the fix was applied, so a future
reader can see the reasoning that produced the fix, not just the fix itself.

## The claim that turned out to be false

`scripts/prove_conversation_e2e.py`'s `dispatch()` function reads and writes
a variable named `conversation` from its enclosing `main()` via Python's
`nonlocal`, believing that this is how a value `run_child()` produces deep
inside a call gets back to `main()`'s own turn loop. The comment that
shipped alongside it said so explicitly:

> "Reads and writes the enclosing `conversation` directly — `run_child`'s
> updated-parent value (`next_child_seq` advancing) needs to reach the next
> `take_turn()` call, and `nonlocal` says so in one word..."

That claim is false. `nonlocal` does let `dispatch()` rebind `main()`'s
`conversation` name — and it does, correctly, while `dispatch()` is running.
But the value never survives past the `take_turn()` call `dispatch()` was
running inside of.

## Root cause: a stale local parameter, not a broken closure

`take_turn()` (`src/sadana/conversation.py:1024-1070`) is an ordinary
function with an ordinary parameter:

```python
async def take_turn(conversation: Conversation, *, ...) -> tuple[TurnResult, Conversation]:
    result, messages, iteration_budget, new_system_prompt = await run_turn(
        conversation=conversation.key,
        ...
        dispatch=dispatch,
        ...
    )
    updated = conversation          # <- take_turn's OWN local, bound at call time
    ...
    updated = replace(updated, messages=messages, next_turn_seq=..., iteration_budget=...)
    return result, updated
```

`conversation` here is a name bound once, when `take_turn(...)` is first
called, to whatever object the caller passed in. Nothing that happens
later in the call — including `dispatch()`, called from deep inside
`run_turn()`'s `TOOL_ROUND` — can change what this name refers to. A
`nonlocal` rebind in `main()` changes what `main()`'s own `conversation`
name points to; it does not reach backward into a frame that already
captured the old object under its own local name.

So the sequence, concretely:

1. `main()` calls `take_turn(conversation, ...)`. `take_turn`'s local
   `conversation` parameter is bound to object `A`.
2. Deep inside, `dispatch()` runs, calls `run_child(parent=A, ...)`, gets
   back an updated parent `B` (identical to `A` except
   `next_child_seq=1`), and does `nonlocal conversation; conversation = B`.
   This changes `main()`'s `conversation` name to point at `B` — right now,
   while `dispatch()` is still running.
3. `dispatch()` returns a string (its only allowed return value). Control
   returns to `run_turn()`, which finishes the turn and returns
   `(result, messages, iteration_budget, new_system_prompt)` to `take_turn`.
4. `take_turn` builds its own return value from its *own* `conversation`
   local — still `A`, never told about `B` — and returns
   `(result, replace(A, messages=..., ...))`.
5. `main()` executes `result, conversation = await take_turn(...)`. This
   line **overwrites** `main()`'s `conversation` name — which briefly
   pointed at `B` — with `take_turn`'s return value, built from `A`. `B`,
   and the `next_child_seq=1` it carried, is now unreachable from anywhere.

Verified independently (not just asserted) with two no-network repros: a
minimal toy version of this exact shape, and a second repro built directly
against the real `create_conversation`/`take_turn`/`run_child`/`dispatch`
functions with a monkeypatched model call. Both show
`conversation.next_child_seq == 1` *inside* `dispatch()`, and
`conversation.next_child_seq == 0` the instant the enclosing `take_turn()`
call returns.

## Why the real proof run didn't catch it

`pending_tool_call_ids()`, `prompt_sha256`, message history, and
`iteration_budget` are all threaded through `take_turn()`'s own official
return value — the channel this bug does not touch — so every assertion
CONV-09 actually makes passed correctly, every time. The only field the
bug corrupts is `next_child_seq`, and nothing in the script asserts that
field directly.

The bug's actual effect is broader than "only visible on a repeated
`node_name`": `next_child_seq` never advances at all, so *every* spawn
after the very first is silently numbered wrong (always 0, never 1, 2,
...) — confirmed once the fix landed (see "Resolution" below) by
re-running the original two-plugin scenario and observing the second
child's key change from `.../plugin_b_child/0` to the correct
`.../plugin_b_child/1`. What stayed genuinely invisible in CONV-09's one
real run is only the *consequence* of that wrongness — two different
`node_name`s both (incorrectly) numbered 0 never collide with each other,
because the full key includes the name. `plugin_a_entry` and
`plugin_b_entry` are each called exactly once, under different names, in
that run — so the *collision* this bug causes never had an input that
could trigger it, even though the numbering itself was wrong the whole
time.

## What fixing it would fix — and what it wouldn't, on its own

**Fixes:** a plugin invoked more than once (or any two dispatch calls that
reuse a `node_name`) would stop silently colliding on the same child key.
That is the actual, concrete failure this bug produces, and the fix
(tracking `next_child_seq` in a variable `main()`'s own turn loop owns,
independent of what `take_turn()` threads through `conversation`) closes
it directly.

**Does not fix, on its own:** the `Conversation` value `main()` ends the
script holding would still report `next_child_seq=0` even after children
were spawned, unless the corrected counter is *also* written back onto
`conversation` after each `take_turn()` call (`conversation =
replace(conversation, next_child_seq=<counter>)`). Tracking the count
correctly for `run_child()`'s own purposes and keeping the `Conversation`
value's own field honest are two different obligations; the first fix
alone only satisfies the first.

**Does not address at all:** the reason this was possible in the first
place — `dispatch()`'s contract (`Callable[[str, dict], Awaitable[str]]`,
`conversation.py`'s `run_turn` signature) gives a dispatch handler no
channel to report anything back to its caller except a result string.
`run_child()` producing an updated parent is a real, documented part of
its contract (`conversation.py:1256-1261`'s own docstring: "the only
difference between parent and the returned updated parent is
`next_child_seq`"), and nothing between a dispatch handler and the turn
loop that called it can carry that difference forward. A script-local
counter is a legitimate, scoped answer for a one-off proof script. It is
not an answer for a real plugin system, where any entry tool's dispatch
handler might spawn a child the same way this fixture does. That is an
interface question — how does a value produced inside `dispatch()` reach
the turn loop that's calling it? — for whichever future work item builds
real plugin dispatch (PLUGIN-SYSTEM, or a DAG engine), not something this
proof script's fix should be mistaken for having settled.

## Resolution (CONV-10-dispatch-parent-propagation)

Both concrete fixes named above under "Fixes" and "Does not fix, on its
own" are applied, together, in `scripts/prove_conversation_e2e.py`:
`next_child_seq` is tracked as its own plain counter in `main()`, never
routed through `conversation` at all; `dispatch()` overlays that counter
onto a `replace()`d snapshot of `conversation` when building each
`run_child()` call's `parent` argument; `main()`'s own turn loop
reconciles the counter back onto `conversation` after every `take_turn()`
call, so the final value is honest too.

Two things this fix does **not** change, stated here so this document
keeps being accurate rather than needing a second correction later:

- **The underlying interface gap — "does not address at all," above —
  is exactly as open as it was.** This fix is a script-local answer for
  one proof script's own bookkeeping, not a general one. It says nothing
  about how a real plugin's dispatch handler would report an updated
  parent back to its own caller; that question is untouched.
- **The fix changes this script's own observable output, not just its
  correctness.** Re-running the original two-plugin scenario produces a
  different second-child key (`.../plugin_b_child/1`, not `/0`) — because
  the bug's actual effect was "every spawn's number is always wrong,"
  not only "a name reuse produces a collision" (see the corrected
  account above). CONV-09's own captured Evidence and closed decision are
  unaffected and unedited — nothing that run's own assertions checked
  ever depended on this field — but a fresh real run of the same script
  would no longer reproduce that exact printed line, and that is
  expected, not a regression.

## Resolution (D1-dagresult-dispatch)

The interface gap this document names above under "Does not address at
all" — `dispatch()`'s contract gives a handler no channel to report
anything back to its caller except a result string — is closed.
`dispatch`'s declared return type, everywhere it appears
(`run_turn`/`take_turn`/`run_child` in `conversation.py`), is now
`Awaitable[plugins.DagResult]`, not `Awaitable[str]`. `DagResult`
(`src/sadana/plugins.py`) carries a `text` field (what the model reads,
rendered exactly where the old plain string rendered), a `trace` of what
happened along the way, any `artifacts` produced, and a `failed_node` that
is `None` only when the run reached a terminal step — a plugin's own
result now has a real, typed shape instead of one line of text standing in
for all of it.

This proof script's own `dispatch` closure was migrated onto the new
shape as part of the same work item — every branch (`plugin_a_entry`,
`plugin_b_entry`, the unreachable unknown-tool fallback) now returns a
`DagResult`, with `trace` naming the same three hand-written steps this
document's own "first plugin already ran" already pointed to. What this
item does **not** do: build anything that walks a *declared* graph and
produces this trace automatically — the trace above is still hand-written,
by this script, the same way the DAG itself always has been. That remains
future work (`docs/reference/plugin_blueprint.md §11` items 4-5).
