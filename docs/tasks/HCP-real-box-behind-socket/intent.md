# Intent: The real box behind the socket

Author: Adam Aubry (product owner). Status: approved.

Contract impact: none — this checkpoint changes no code; it proves H30's
already-merged door-behind-a-socket on real hardware.
Dangers covered: B6, B14, D5 (the console's own frozen plan, in a sibling
repository this project cannot itself verify; recorded verbatim, on trust).
Reopen cost: the whole console plan — every checkpoint from step 30 onward
is specified to assume this box exists and is graded.

## Problem

Everything sadana-harness has proven about enrolling, tethering, and talking
to a box has been proven on a laptop, against a test relay running in the
same process tree, on a network with no real latency, no real firewall, and
no real outage. The console plan that comes next assumes a real box exists
somewhere: a fresh machine that dialed out over the real internet, enrolled
itself, and can be driven end to end through a relay the way a customer's
box eventually will be. Nobody has actually done that yet. Until someone
does, every claim this project makes about "the box behind the socket" is a
claim about a simulation, not about the thing a customer will actually run.

## Proposed outcome

A real, internet-connected machine — provisioned from nothing but a fresh
Ubuntu image and the published install document, with no hand-run step the
document doesn't name — ends up enrolled, tethered, and reachable end to end
through a relay, entirely by dialing out. Everything the box promises
(naming, ordering, one door, the tether itself, streaming, parking, live
config, self-reporting, and being removable) gets exercised against that
real machine and graded with pasted evidence, one line per promise. A
written verdict says plainly whether the next phase of work — nine services
built against this box's grammar — can start, and if any promise falls
short, that shortfall becomes its own follow-up work item rather than a
silent asterisk.

## Affected users and systems

Nobody outside this project yet — there is no customer and no real console
relay. Inside the project: whoever builds the next phase (the nine console
services) is the direct beneficiary, because their own checkpoints are
specified to run "against this box, if it has landed" — this work item is
what makes that phrase true instead of aspirational. The install document
and the test-relay fixture are exercised as the two other artifacts this
item is really testing, alongside the box's own code.

## Constraints

- No inbound rule to the box, ever, for the tether itself — true regardless
  of how the box is reached for administration.
- The install document is the only source of truth for provisioning steps;
  anything done by hand that it doesn't name is itself a finding, not a
  workaround.
- This item does not build a real console relay — the test relay fixture
  continues to stand in for it, on the same machine as the box.
- This item writes no production code; a promise found short is recorded and
  deferred to a new work item, never patched in place here.
- The box's private key never leaves the machine, in a log, an error, or
  anywhere else — true of every environment, not particular to staging.

## Open questions

- Exact instance sizing, AMI and region for the EC2 box — resolved when the
  user provisions it, following the setup guide this work item produces;
  not a policy decision this intent needs to fix.
- How the user hands over session access and the freshly minted model
  provider key once the box exists — out of band, never pasted into a
  conversation.
- Whether the box, once graded, is reused as-is for the console plan's later
  checkpoints or re-provisioned per checkpoint — the user has said to keep
  it running, but the console plan itself may revisit that once it exists.

## Changed during planning

Two things moved during the interview. First, the task arrived describing
the test relay as running "on the same machine as the relay," ambiguously
enough to read as either one box or two; it was settled as one box running
both the installed gateway and the test relay over loopback, matching the
install document's own local-dev guidance rather than inventing a second
reachable host. Second, the task as dictated assumed reuse of the model
provider key already sitting in the repository's local secrets file; that
key was independently found to be exposed in this conversation's own
context during the interview, and the plan changed to require a freshly
minted key handed over out of band instead of reusing one now treated as
compromised. The access method also resolved during the interview — a
session-manager style connection, so no inbound port opens even for
administration.
