# Intent: The smallest complete plugin runs, visibly

Author: Adam Aubry (project owner). Status: draft.

## Problem

A person cannot currently watch the paradigm's simplest possible plugin —
one written procedure, entered by naming it, with no other moving parts —
actually run and then see, afterward, both what that plugin was and what
happened when it ran. Two earlier pieces of work already proved, once, that
a plugin this small can run for real against a live model, but the record
of what happened during that run was discarded the moment the run finished,
and the proof itself was never kept in a form anyone can run again. Right
now there is nothing anyone can point to, run again, and read to see the
smallest complete example of this project's whole idea actually working,
end to end.

## Proposed outcome

Given the simplest possible plugin already sitting on disk — one written
procedure, entered by naming it, nothing else — a person can run a single,
repeatable check against a real model and afterward read two things: what
the plugin was (its own name and the one step it declared) and a real
record of what happened when that step ran (that it happened, and whether
it succeeded). This check can be run again later, by anyone who wants to
confirm the smallest case still works, and its output is kept as evidence
rather than thrown away when the run ends.

## Affected users and systems

Only the maintainer, for now — the same as every other checkpoint of this
kind so far. Nothing outside this one person's own use reads this output
today.

## Constraints

- The check must spend real money against a real model. A simulated or
  mocked model answering this is not a version of "seeing it run" this
  project accepts, matching every proof of this kind produced so far.
- Whether the run succeeded is judged by inspecting what actually
  happened, never by asking a second model for its opinion.
- The plugin used for this must already exist and must not be rewritten
  or duplicated for this check — a second copy of the same minimal
  example would prove nothing a person doesn't already have.
- This does not become part of the checks that run automatically on every
  change. Real spend against a real model is deliberately kept out of
  that set, the same as every other real-model proof this project has
  produced.

## Changed during planning

Started from a framing that could have gone two ways — either formally
re-citing an existing proof, or promoting it into something repeatable —
and the interview surfaced a concrete reason to prefer the second: the
record of what happened during a run (which step ran, whether it
succeeded) is generated once today and discarded the instant the run
finishes, so nothing exists yet for a person to actually read. That
finding is what turned "see the plugin and its trace" from a documentation
exercise into a real, if small, capture-and-report problem. Also settled
during the interview: this belongs to the PLUGINS work item track, not the
separate evaluation-harness track, despite the shape resembling that
track's own past work.
