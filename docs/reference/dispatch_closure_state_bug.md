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
field directly. Its only externally visible effect is on the *next* child
spawned under the *same* `node_name`: it would get the same key a prior
child already used, instead of a new one. `plugin_a_entry` and
`plugin_b_entry` are each called exactly once, under different
`node_name`s, in the one real run this proof produced — so the collision
this bug causes never had an input that could trigger it.

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
