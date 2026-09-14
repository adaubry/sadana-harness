# Intent: Watched — streaming, live runs and stop

Author: Adam Aubry (project owner). Status: approved.

## Problem

A person using the console sends a message and the screen goes blank. For a
long stretch — sometimes tens of seconds — there is no sign the request is
progressing: no words appearing, no indication of which step is currently
running, and no way to interrupt it if something is clearly wrong or taking
too long. They cannot tell "still thinking" from "stuck" from "already
crashed." If they decide to give up, their only option is to abandon the
page and hope the underlying work stops on its own.

## Proposed outcome

While a turn is running, the console can show: the reply's words as they
arrive rather than all at once at the end; which step of the current work is
executing right now; whether the overall request is running, waiting on
something, or finished — successfully, with an error, or because it was
stopped; and a way for the person to stop it mid-flight, honored at the next
safe point rather than left stuck. When a turn finishes normally, the answer
that settles is exactly the answer the console would have shown without any
of this — nothing about the eventual result changes, only how much the
person can see and control while it happens.

## Affected users and systems

The person on the console, watching their own request run, is who this item
exists for. No other caller of a turn — a script, an automated test run, a
command-line session — is affected: every existing way of starting a turn
keeps behaving exactly as it does today unless it explicitly asks to be
watched. Confirmed during planning, not assumed.

## Constraints

- This item does not change what a finished turn's answer is, only what is
  visible and controllable while it runs.
- Stopping a turn asks it to stop at its next safe pausing point; nothing
  already underway is forcibly killed.
- What is shown while a turn runs is not saved anywhere. If nobody is
  watching at the moment a word arrives, that word is not recoverable
  afterwards — only the finished answer is kept.
- This item does not build or change the console's own screens. It makes the
  underlying request watchable and stoppable; the console side already
  assumes this capability exists and lives outside this repository.
- A client reconnecting mid-turn and picking up a stream where it left off,
  and two people watching the same request at once, are both explicitly out
  of scope here.

## Open questions

None. The scope, the audience, and the shape of "watched" were settled
during planning and by the standing chain plan this item belongs to.

## Changed during planning

Two things were confirmed rather than assumed. First, that this stays one
work item rather than splitting into a smaller first slice: its three parts
share one underlying seam, and reviewing them separately would mean
revisiting the same code twice. Second, that no existing caller of a turn
needs to change: watching and stopping are opt-in, so everything that starts
a turn today keeps its current, unwatched behavior unless it asks for the
new one. Neither was obvious without asking, since the size and framing of
the request made both look assumable rather than decided.
