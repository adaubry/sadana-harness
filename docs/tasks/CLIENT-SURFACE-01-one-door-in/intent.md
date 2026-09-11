# Intent: One door in

Author: Adam Aubry (maintainer). Status: draft.

## Problem

There is already more than one way to reach an agent built on
sadana-harness, and they are not the same agent. A person typing in a
terminal and a person sending a message through a chat channel get
different behaviour from the same agent, running the same procedures, over
the same saved conversations.

The clearest case is a procedure that stops halfway to wait for someone.
Reached through the chat channel, the person's next message picks that
procedure back up where it stopped. Reached from the terminal, the waiting
step is dropped without a word: no error, no notice, and no way to resume
it — the work is simply gone.

This is not an accident that got caught late, it is the predictable result
of how the second way in was built. It was copied from the first, and has
since grown behaviour the original never got. Two ways in were already
enough for them to disagree. Every further way of reaching the agent — a
browser, a desktop app — would start from another copy of the same wiring
and drift the same way, and the person on the other end has no way to know
which version of the agent they are talking to.

## Proposed outcome

There is one way in, and everything that reaches the agent comes through
it. Whoever is asking states who the person is, which conversation this
belongs to, and what was said; what comes back is what happened during
that turn. Both existing ways of reaching the agent go through it, and the
disagreement between them is gone: a procedure that stops to wait can be
resumed from the terminal exactly as it can from a chat channel.

Whoever is asking always states who the person is. The way in never
guesses it and never falls back to an assumption of its own, so anyone
reading a single client can tell whose saved conversations and whose
remembered facts it will touch, without reading the agent's insides.

Adding a further way to reach the agent is then a small, well-understood
piece of work: receive a request, say who is asking and what they said,
show the answer. Nothing about how the agent takes a turn is written a
second time, and nothing already working has to be edited to let the new
one in.

From the outside, the two ways in that exist today behave as they do now —
same output, same exit codes, same replies — with the single exception of
the waiting-procedure defect, which is fixed for the terminal.

## Affected users and systems

- Anyone who will build a client on sadana: this is a contract they write
  against, so what it asks for, what it returns, and how it fails have to
  be documented and hard to get wrong, not merely correct for the two
  callers we control today.
- People reaching the agent from a terminal: a procedure that stops to
  wait becomes resumable for them, where today it is silently lost.
- People reaching the agent through a chat channel: no visible change.
  What they have works and is the behaviour the other way in is being
  brought up to.
- Every saved conversation, every remembered fact about a person, and
  every procedure that can stop and wait: all of them are now reached
  through a single path instead of two, so how they are found, created and
  written is answered once.
- Whoever operates a deployment: the terminal keeps working with nothing
  running in the background. Reaching the agent from a machine that isn't
  this one is a separate piece of work and is not part of this.

## Constraints

- Who the person is, and which conversation this is, are stated by
  whoever is asking, every time. The way in holds no default identity of
  its own.
- One finished answer per turn. Nothing in the agent can produce a partial
  answer today, so nothing here pretends to. An answer arriving in pieces
  as it is written is a known future need, and the design may not paint
  itself into a corner that would make every client be rewritten to get
  it.
- No machinery is built for a client that does not exist yet. No table of
  ways-in with one entry in it, no swappable part with a single
  implementation. That a further client can be added without editing
  anything that already works is a property of how the door is shaped, and
  it is checked when this work is reviewed.
- Nothing about how a client is received is decided here: no protocol, no
  message format, no server. Whoever is asking already exists in whatever
  form it has.
- A failure inside a turn still reaches whoever asked as an ordinary
  answer describing what happened. Turning that into an exit code, an
  error page or a reply is the client's business, and each existing client
  keeps the choice it already made.
- Authentication, rate limiting and keeping one deployment's people apart
  from another's are not part of this. They belong to whatever eventually
  accepts a request from off this machine.

## Open questions

- The terminal today assumes a single local person when nobody has said
  who is asking. If whoever asks must now always state it, does the
  terminal refuse to start without one, or does it keep naming its one
  local person as its own answer to that question? Left for spec.md.
- The two ways in disagree about starting a conversation that does not
  exist yet: the terminal is told explicitly to start one, the chat
  channel starts one the moment a message arrives for a conversation it
  has never seen. Whether the single way in decides this itself or is
  told, is unresolved. Left for spec.md.

## Changed during planning

The interview moved the problem from the future into the present. This
item arrived framed as a cost we were about to pay — three clients, three
copies of everything — and the reading of the code done during the
interview found that it had already happened: the second way in was built
by copying the first, says so in its own notes, and the two have already
diverged in a way a person can feel, because a procedure that stops to
wait is resumable through one and silently discarded through the other.
The problem section was rewritten around that witness, and the fix for it
is now an outcome of this item rather than a thing we hoped to prevent.

Identity moved out of the agent and onto whoever is asking. The two ways
in quietly answer "who is this person" differently today; the first
instinct was to have the single way in settle it centrally, and the user
chose the opposite — the caller must say, every time — on the grounds that
a client should be readable on its own for whose data it touches.

"Make it future proof" was asked for mid-interview and was deliberately
narrowed before it was accepted. The version that builds a seam with one
implementation in it is exactly what this project forbids, so what was
accepted instead is that adding a client must be additive and that this is
checked at review. The user was offered a stronger version of the same
guarantee — write a third, deliberately boring client in this same item so
the shape is proven against three callers rather than two — and chose the
rule over the third caller. That is the weaker of the two and it is worth
recording as such: the guarantee now rests on the reviewer's judgement,
and the first real proof will be the next client someone actually builds.

The audience widened. This started sounding like plumbing for one
operator; the user confirmed it is a contract anyone building a client
writes against, which raises what the design stage owes in documentation
and in how hard the surface is to misuse.

Scope was held to one commit and one door, against the alternative of
defining the surface first and moving the existing callers later. A door
with no caller at the end of its own commit is a guess, and splitting it
would have left the waiting-procedure defect live in between.
