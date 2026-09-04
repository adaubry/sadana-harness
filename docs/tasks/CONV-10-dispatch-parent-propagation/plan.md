# Plan: The proof script's own bookkeeping stops silently losing track of who it already spawned (from intent.md 2026-09-04)

## Files that change

- `scripts/prove_conversation_e2e.py` — `main()`'s `dispatch()` closure
  and turn loop, per spec.md § Design. No signature changes anywhere;
  purely internal state-handling.
- `docs/reference/dispatch_closure_state_bug.md` — one addition at the
  end stating this fix's scope: closes the concrete collision symptom,
  does not solve the underlying interface gap (a dispatch handler has no
  channel back to its caller beyond a result string). The existing
  root-cause account stays untouched.
- Nothing else. No `src/` changes. `docs/tasks/CONV-09-deploy-evidence/`'s
  four files are not touched (spec.md requirement 6, acceptance criterion
  6).

## Order of work

1. **The fix itself** in `scripts/prove_conversation_e2e.py`: add
   `next_child_seq: int` as a plain local in `main()`, initialized from
   `conversation.next_child_seq`; change `dispatch()` to build `parent =
   replace(conversation, next_child_seq=next_child_seq)`, update
   `nonlocal next_child_seq = updated_parent.next_child_seq` after each
   `run_child()` call, and stop reassigning `conversation` inside
   `dispatch()` entirely; add `conversation = replace(conversation,
   next_child_seq=next_child_seq)` after each of the four `take_turn()`
   calls in `main()`'s own loop; fix the now-inaccurate comment. Needs
   `from dataclasses import replace` added to the imports.
2. **Local, no-network verification, two scenarios** (ad-hoc, matching
   CONV-09's own build-time smoke-check posture — not a committed test
   file, per spec.md § Non-goals):
   - **Regression scenario**: re-run the exact mocked scenario CONV-09's
     build already used (two children, two different `node_name`s) and
     diff the printed output against what that build captured — proves
     this fix is behaviour-preserving for the one real run this project
     has (spec.md acceptance criterion 3).
   - **New collision scenario**: a mocked run that deliberately spawns
     two children under the *same* `node_name` in two different turns,
     asserting their keys differ and that the final `conversation`'s
     `next_child_seq` correctly reads 2 — the scenario the original proof
     never exercised (spec.md acceptance criteria 1-2).
3. **Update `docs/reference/dispatch_closure_state_bug.md`** with the
   fix's scope statement (spec.md requirement 5, acceptance criterion 4).
4. `/ponytail-review` + `/simplify` self-check against the diff.
5. `make verify`.

## Risks

**What could this change break that already works?** Nothing under
`src/` or `tests/` is touched, so `make verify`'s own suite has zero new
surface. The only real risk is to `scripts/prove_conversation_e2e.py`'s
own already-proven real-run behaviour — mitigated directly by step 2's
regression scenario, which exists specifically to catch that before
trusting the fix.

**Which step is riskiest, and why that one?** Step 2's new collision
scenario — not because it's likely to reveal the fix is wrong (the design
was worked through and reasoned about with the user before this plan
existed), but because it's the one piece of work in this plan that's
actually new proof, not a mechanical edit. It's ordered right after the
fix itself, before anything else, so a wrong fix is caught immediately
rather than after the lower-value doc update.

**Which options did spec.md already reject, and is this plan drifting
toward one?** Checked spec.md § Rejected alternatives: not keeping
`conversation` in the propagation path and just "being more careful" with
it (step 1 removes it from the path entirely); not adding the collision
proof to the real committed script or spending real API credit again
(step 2 is explicitly local and mocked); not deleting or rewriting the
reference doc (step 3 adds to it, doesn't replace its root-cause
account). No drift found.

## Proof

- Step 2's regression scenario output, diffed against CONV-09's own
  build-time smoke-check capture. **Not byte-identical, and that's
  correct, not a regression to chase down**: the second child's key
  changes from `.../plugin_b_child/0` to `.../plugin_b_child/1`, because
  the bug made every spawn after the first get numbered wrong (always
  0), not only spawns that happened to reuse a `node_name`. Every
  assertion the original scenario actually makes still passes unchanged.
- Step 2's new collision scenario output, showing two children spawned
  under the same `node_name` get different keys, and the final
  `conversation.next_child_seq == 2`.
- `make verify` output, pasted in full, ending `VERIFY OK`.
- Confirmation that `docs/tasks/CONV-09-deploy-evidence/`'s four files
  are byte-identical to their state before this work item started.
