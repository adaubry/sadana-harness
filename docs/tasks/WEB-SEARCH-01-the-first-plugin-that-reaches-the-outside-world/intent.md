# Intent: The first plugin that reaches the outside world

Author: adam aubry (owner). Status: draft.

## Problem

The agent cannot look anything up. Everything it says comes out of what it
absorbed when it was trained, so anything recent, anything local, anything that
changed after that point is simply unavailable to it.

The failure is worse than not knowing, because it does not present as not
knowing. Asked about something it has no information on, it answers anyway, in
the same confident voice it uses for things it is right about. The person
asking has no way to tell the two apart, and neither does the agent.

Everything built here so far has been the machinery for an agent to do things,
with nothing yet that a person would actually ask for. A plugin can be drawn,
installed, given a key, given somewhere to write, and run. Not one of them does
anything a person wants done. The parts have never been put together and
pointed at a real service, so nobody knows whether the shape works.

There is a second problem underneath the first, and it is the one that matters
more. The whole plan for bringing capabilities over rests on a claim nobody has
tested: that a real outside capability can be expressed as one of our plugins
without distorting either. Every piece of that claim has been argued on paper.
None of it has been run.

## Proposed outcome

A person can ask something whose answer is on the web, and the agent looks it
up rather than guessing, and says where it got the answer.

The search is a plugin, built and installed the same way any other plugin
would be, using only the pieces that already exist. Nothing about it is special
or wired in behind the scenes. If it needs something the plugin system cannot
do yet, that is a finding, and a more valuable one than the feature.

The person has to go and get an account with whichever service does the
searching, and is told clearly that they need one, what it is for, and where
to get it. Nothing here ships a shared account or works without one.

There is a demonstration that this genuinely reached the outside world and
came back — not a test standing in for one. It runs on its own, against the
real service, and what it printed is recorded where the decision to ship gets
made.

The smallest version still worth having: one question in, a handful of results
out, each with a title, an address, and enough text to tell whether it is worth
reading. Not reading the pages themselves, not summarising them, not following
links, not remembering what was searched before.

## Affected users and systems

The person running this project, who gets the first thing here that does
something they would actually ask for, and who has to sign up for an account
to get it.

Anyone writing a plugin later, because this becomes the worked example. What
it turns out to be awkward about is what everyone after will hit.

What finds out that this changed:

- What the agent can do. It gains an ability, which is what the model is told
  about at the start of a conversation.
- The step that asks permission before a plugin does something outside. It has
  only ever guarded rare things; this is the first common one.
- The settings a plugin can declare, which until now has had no real user.
- Whatever reads a plugin's answer, which now carries text written by
  strangers.

## Constraints

One search service, reached directly. Not a choice of services, not a way to
add more later, not an arrangement that anticipates a second one. When there
is genuinely a second, that is when the shape for having two gets built.

Nothing new gets installed to make this work. If it cannot be done with what
this project already depends on, the answer is a different service, not a new
dependency.

The account key is a setting the plugin declares and the person supplies. It
never appears in this repository, in the plugin's own files, or in anything
the agent says.

What comes back is text a stranger wrote, and it goes straight into what the
model reads. It is information about the world, never instructions, and
nothing downstream may treat it as a request. This is the first time anything
here has taken untrusted text from outside and put it in front of the model,
and it is the single most dangerous thing about this work item.

The proof that it really works cannot live in the ordinary tests. Those are
forbidden from touching the network, for good reasons that are not being
relaxed for this. The real round trip is demonstrated separately and its
output is pasted where a person reviewing can see it.

Nothing here reads the contents of a page. Results only.

## Open questions

The step that asks permission before a plugin reaches outside will now fire on
every single search. That was designed for rare, consequential actions, and a
search is neither. Whether searching should be exempt, whether permission
should be remembered for a while, or whether it should simply be accepted as
noisy for now, is a question about how this feels to use, and the owner
answers it. Nothing in this work item changes that step.

Whether reading the actual contents of a page follows this, and how soon, is
undecided. It is the obvious next thing to want and it is deliberately not
here.

## Changed during planning

The framing changed once, and it changed what this work item is for.

The first version of this was "add web search", a feature. Reading the plan
this comes from made it clear that is the smaller half. The capability is
worth having, but the reason it is scheduled second out of six is that it is
the first time the pieces get assembled and pointed at something real, and the
thing being tested is the shape, not the search. That is now written into the
outcome: if the plugin system cannot express this, the finding is the
deliverable and it is worth more than the feature would have been.

The scope narrowed twice. Reading the contents of a page came out — it is a
second capability wearing the same coat, and it needs decisions about size
limits and formats that have nothing to do with whether the shape works.
Choosing between search services came out too, for a reason already settled
elsewhere in this project: the machinery for having several of something is
worth building when there are several, and not before.

A constraint was added that was not in the original framing at all, and it is
the most important line here. Search results are text written by strangers,
arriving in front of a model that reads instructions. Nothing in this project
has ever taken outside text and put it in the model's path before. That was
not in the first draft of this intent and should have been.

One assumed constraint turned out to be a preference and was dropped. "It
should work without an account" was written down as a requirement, on the
grounds that a key is friction. It is not a constraint — it is a wish that
would silently decide which service is used and would throw away the only real
test of the settings mechanism built for exactly this. The friction stays.
