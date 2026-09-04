# Spec: The proof script's own bookkeeping stops silently losing track of who it already spawned

Intent: docs/tasks/CONV-10-dispatch-parent-propagation/intent.md

## Requirements

1. Two children spawned in the same script run under the same `node_name`
   get different, non-colliding keys — the actual bookkeeping mechanism is
   corrected, not merely never exercised. (Intent §Proposed outcome.)
2. A local, no-network check proves requirement 1 directly, by deliberately
   reusing a `node_name` and asserting the resulting keys differ — the one
   scenario `scripts/prove_conversation_e2e.py`'s real run never covered.
   (Intent §Proposed outcome.)
3. The `Conversation` value the script ends with reports the true count of
   children spawned during the run, not merely a value that was briefly
   correct inside one `dispatch()` call and then overwritten. (Intent
   §Proposed outcome — "fixed at its actual source," not just the
   collision avoided.)
4. `spec.md`'s own Design section (this document) describes the corrected
   mechanism accurately enough that a reader learns how the script
   actually works from it, not from a separate explanation of a bug that
   no longer applies. (Intent §Proposed outcome, §Changed during
   planning.)
5. `docs/reference/dispatch_closure_state_bug.md` is updated, not deleted
   or replaced, to say plainly that this work item closes the concrete
   symptom it named, without claiming the underlying interface gap is
   solved. (Intent §Affected users and systems, §Constraints.)
6. Nothing belonging to the closed `CONV-09-deploy-evidence` work item
   (its `intent.md`/`spec.md`/`plan.md`/`review.md`) is edited. (Intent
   §Constraints.)
7. Nothing in `src/` changes, and no real call to a model provider is
   needed to prove this fix. (Intent §Constraints.)
8. `docs/reference/dispatch_closure_state_bug.md`'s account of the deeper,
   still-open interface gap — a dispatch handler has no channel to report
   anything back to its own caller except a result string — is preserved,
   not weakened into implying this work item resolved it. (Intent
   §Constraints.)

## Design

The fix removes `conversation` from the propagation path entirely rather
than patching the closure that misused it, moving `next_child_seq`'s
bookkeeping into its own small, independently-owned counter — checked
against the reference corpus and found to have no applicable precedent
there, for a specific, stated reason.

### What was actually broken, restated precisely

`docs/reference/dispatch_closure_state_bug.md` already has the full
root-cause account; this section states only what the fix changes,
against that account.

`main()`'s `dispatch()` closure tried to carry `run_child()`'s updated
parent (identical to the parent passed in, except `next_child_seq`
incremented) back out to the enclosing turn loop by rebinding `main()`'s
own `conversation` name via `nonlocal`. That rebind is real while
`dispatch()` is running, but `take_turn()` — the function `dispatch()` is
running *inside of* — had already captured the old `conversation` object
under its own local parameter name before `dispatch()` ever ran, and
builds its return value from that stale local, not from anything the
nested closure did. The very next line in `main()`,
`result, conversation = await take_turn(...)`, overwrites the nonlocal
with `take_turn()`'s stale-derived return value, discarding the correct
update.

### The fix: stop routing `next_child_seq` through `conversation` at all

The only field a spawn ever changes on the parent is `next_child_seq`
(`run_child()`'s own docstring, `conversation.py:1256-1261`: "the only
difference between parent and the returned updated parent is
`next_child_seq`"). Nothing else about the parent needs threading through
`dispatch()` — `key`, `template_name`, `system_prompt`, `tool_surface`,
`wall_clock_budget` are all byte-stable across the whole run and already
read correctly off `conversation` as it stood at the start of the current
turn. So `next_child_seq` gets its own, independent piece of state, never
routed through `take_turn()`'s return value at all:

- `main()` holds a plain `next_child_seq: int` counter, starting at
  whatever `conversation.next_child_seq` was created with (0).
- `dispatch()` builds each `run_child()` call's `parent` argument as
  `replace(conversation, next_child_seq=next_child_seq)` — the live
  counter overlaid onto an otherwise-current snapshot of `conversation`.
- After `run_child()` returns, `dispatch()` updates the counter —
  `next_child_seq = updated_parent.next_child_seq` — not `conversation`.
  `dispatch()` no longer reassigns `conversation` at all; there is no
  longer anything about a spawn that needs to reach it.
- After every `take_turn()` call in `main()`'s own loop (whether or not
  that turn spawned anything), `main()` reconciles:
  `conversation = replace(conversation, next_child_seq=next_child_seq)`.
  This is requirement 3: the counter, not `take_turn()`'s own threading,
  is now the single source of truth for this one field, and every reader
  of the final `conversation` value — including a future caller that
  might persist it — sees the true count.

This removes the closure-based propagation attempt entirely rather than
patching it: there is no longer any path by which a spawn's effect needs
to survive a `nonlocal` rebind across a function boundary that can't see
it.

### Reference corpus check (design guideline 1)

Checked whether hermes-agent's own child-spawning code
(`tools/delegate_tool.py`, read during CONV-07/C9) offers a pattern worth
adopting here. It doesn't, for a specific, checkable reason rather than a
stylistic preference: hermes's `agent` is one shared, mutable object every
part of the loop reaches into directly (`agent._cached_system_prompt`,
`agent._tool_guardrail_halt_decision`, etc. — C9's own spec.md already
named this "the #1 transplant hazard"). A spawn's effect on hermes's agent
is just a plain attribute mutation, visible everywhere immediately,
because nothing in hermes threads an immutable value through a return
type the way `take_turn()`/`run_child()` do here. hermes never faces this
exact problem because it never made the design choice (immutable
`Conversation` values, no shared mutable agent) that this problem is a
direct, on-purpose consequence of. There is nothing to adopt from it; the
fix has to be local to this project's own already-chosen shape.

## Interface

No public interface changes — `scripts/prove_conversation_e2e.py` is not
imported by anything. Internally, `dispatch()`'s signature is unchanged
(`Callable[[str, dict], Awaitable[str]]`, matching `take_turn()`'s
`persist`-adjacent parameter contract exactly as before); only its body's
state-handling changes, per §Design.

## Acceptance criteria

- [ ] A local, no-network scenario spawns two children under the same
      `node_name` in two different turns and asserts their `key`s differ.
- [ ] The same scenario asserts the final `Conversation`'s
      `next_child_seq` equals the true number of children spawned (2),
      not the pre-fix value (always 0 regardless of how many were
      spawned).
- [ ] Re-running the existing local, no-network smoke scenario from
      `CONV-09-deploy-evidence`'s build (two children, two different
      `node_name`s) still passes every one of that scenario's own
      assertions — but its second child's printed key now correctly ends
      in `/1`, not `/0`. That difference from what CONV-09 captured is
      itself evidence the fix works, not a regression to explain away:
      the bug didn't only risk a collision when a `node_name` repeats, it
      made *every* spawn's sequence number wrong (always 0, since the
      counter never advanced), and this was invisible in CONV-09's own
      real run purely because its two plugins use different names, so no
      two children ever compared their (both-wrong) numbers against each
      other. See §Concerns.
- [ ] `docs/reference/dispatch_closure_state_bug.md` is updated to state
      this work item's fix and its scope, without removing or
      contradicting its root-cause account or its statement of the
      deeper, still-open interface gap.
- [ ] `make verify` ends `VERIFY OK` (this script remains outside the
      suite; nothing under `src/` or `tests/` changes).
- [ ] `docs/tasks/CONV-09-deploy-evidence/`'s own four artifact files are
      byte-identical to what they were before this work item started.

## Non-goals

- Solving the deeper interface gap — a dispatch handler having no channel
  to report anything back to its own caller except a result string. That
  is a design question for whichever future work item builds real plugin
  dispatch (PLUGIN-SYSTEM, or a DAG engine), not this one.
- A new committed test file. `testing-conventions` and
  `CONV-09-deploy-evidence/spec.md §Non-goals` already established this
  script stays outside `make test`'s suite; its verification stays a
  local, ad-hoc, no-network check, the same posture CONV-09's own build
  used before spending real API credit.
- Any change to the real proof run's own turn sequence or a fresh real
  run against OpenRouter. The fix is provably behaviour-preserving for
  the already-captured real evidence (§Acceptance criteria's third
  bullet) because that run never exercised the buggy path in the first
  place — see §Concerns.

## Rejected alternatives

- **Keep routing the update through `conversation` via `nonlocal`, and
  just remember to reconcile it more carefully.** Rejected: the root
  cause isn't carelessness, it's that `take_turn()` structurally cannot
  see a mid-call mutation of a name in its caller's scope — no amount of
  "being more careful" with the same mechanism closes that gap, since the
  gap is in what the mechanism can express, not in how it was used.
  Removing `conversation` from the propagation path entirely, per
  §Design, is the smaller bet: one small piece of state gets one owner
  (the counter), instead of two things (`conversation` and the counter)
  racing to describe the same fact.
- **Add the collision-reuse proof to the real, committed script itself**
  (e.g. have turn 3 reuse turn 2's `node_name`), which would need a fresh
  real run to produce updated Evidence. Rejected per intent.md's own
  constraint — no real spend is needed to prove a pure state-threading
  fix, and the real run's existing Evidence already stands on its own
  for what it was proving (blueprint §1.2's milestone), unrelated to this
  bug.
- **Delete or rewrite `docs/reference/dispatch_closure_state_bug.md`**
  once the symptom is fixed. Rejected per intent.md and requirement 5 —
  the root-cause account stays correct and worth keeping regardless of
  this fix, and the document's account of the deeper, unresolved
  interface gap remains genuinely open.

## Concerns

- **Why the already-captured real Evidence needs no new run, corrected
  after actually re-running the scenario rather than assumed from
  design.** This document's first draft claimed the fix wouldn't change
  the original scenario's observable output at all — checking that
  assumption by actually re-running it (acceptance criterion 3) proved it
  wrong: the second child's printed key changes from `.../0` to `.../1`,
  because the bug didn't only create a collision *risk* when a
  `node_name` repeats — it made the sequence number wrong on *every*
  spawn after the first, silently, since the counter never advanced past
  0 no matter how many children existed already. Two different
  `node_name`s both incorrectly numbered `/0` never collide with each
  other, which is exactly why CONV-09's real run never surfaced it. What
  *does* still hold, and is what actually matters for not needing a new
  real run: every value that run's own assertions check
  (`prompt_sha256`, `exit_reason`, `pending_tool_call_ids`, `child.key !=
  parent.key`, `len(child.messages) > 0`, `child.tool_surface == ()`) is
  unaffected by which of `next_child_seq`'s two behaviours was in effect,
  because none of them read `next_child_seq` at all. CONV-09's own
  Evidence, and the decision it recorded, stand exactly as they were —
  correct for what they actually asserted, then and now.
- No policy skill named `project-structure` or `reference-lookup` exists
  in this project (same gap CONV-08 and CONV-09's specs already
  recorded). `testing-conventions` was checked and confirmed not to bind
  new test-file conventions here, since this fix adds no test file (see
  §Non-goals).
