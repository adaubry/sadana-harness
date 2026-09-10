# Intent: A plugin nobody can find is a plugin nobody uses

Author: Adam Aubry (maintainer). Status: draft.

## Problem

A person who builds a useful plugin has no way to let anyone else find or
use it beyond telling them directly. A person looking for a plugin that
does something they need has nowhere to look for one — they would have to
already know it exists and where its code lives. And the person willing to
vouch for a plugin before other people trust it has no safe way to see
what it actually does without running code they do not control, on their
own machine, sight unseen.

## Proposed outcome

Someone with a plugin they built can submit it so other people can find
it. Someone browsing sees, for every plugin that has been checked and
approved, exactly what it does and how its steps fit together — described
in enough detail to judge whether it is worth using — without anyone ever
having run a line of that plugin's own code to produce that description.

Between a submission and it reaching an ordinary browser, one or more
trusted reviewers see the same description early, for anything not yet
approved, and decide whether it becomes visible to everyone else:
approving it, rejecting it with a reason the creator can see, or leaving
it undecided. A plugin that has already been approved once keeps showing
its last approved version to ordinary browsers even while a newer version
is waiting on review, so nothing that used to be visible ever disappears
on its own. Tagging a new release is what puts a plugin in front of a
reviewer in the first place — nobody has to run a separate command by
hand to start that review.

## Affected users and systems

Three kinds of people, none of them logging into anything:

- **Creators** — anyone who builds a plugin and wants it found, not only
  the person running this instance.
- **Reviewers** — one or more trusted people, all at the same level (any
  one of them can decide alone; they do not need each other's agreement).
  They are not necessarily the same person as any given creator.
- **Browsers** — anyone looking for a plugin to use. They only ever see
  releases a reviewer has approved.

Nobody here has an account or logs in. A role is whichever action someone
takes, the same trust posture the rest of this project already has.

What finds out this changed: the command-line interface gains new
commands people actually run to submit, review, and browse. A separate,
future web application will eventually read from what this work item
produces instead of building its own version of this same logic — that
application is not decided or built here.

## Constraints

- Nothing in this feature ever runs a line of a plugin's own code to
  produce what a reviewer or a browser sees. Avoiding exactly that is the
  entire reason this is being built.
- Only public repositories, and no credential of any kind anywhere in the
  fetch path — the same limit already set for installing a plugin.
- No accounts or logins for creators, reviewers, or browsers.
- This is not a web application, and does not build one. It has to leave
  behind something a future, separate web application can use as its own
  back end, but nothing about that later application is decided here.
- Every single tagged release goes through review before ordinary
  browsers can see it as available — even a new release from a creator
  whose earlier release already passed. Being approved once does not
  exempt a later release from being checked again.

## Open questions

- Whether an already-approved release can later be taken back — a
  reviewer discovering, after the fact, that something they approved
  should not have been — was not decided. Today's proposed outcome only
  describes moving a release forward through review, never backward.

## Changed during planning

Two real decisions came out of this interview, not just narrower wording.
First, the trust model widened partway through: the opening framing sat
between "one operator's own private list" and a fully open marketplace,
and landed specifically on real, independent creators and reviewers with
no accounts — a middle ground, not either extreme. Second, and more
consequentially, this was raised as almost certainly two or three work
items rather than one — safe display, the submit/review workflow, and a
real webhook listener are three fairly separate pieces of work, and
splitting them was explicitly recommended — and the user chose, on the
record, to keep it as a single work item anyway. Also settled, each a real
either/or rather than an assumption: review applies to every release, not
just a creator's first; a rejection carries a reason; and an
already-approved release stays visible to ordinary browsers while its
successor is still under review, rather than disappearing.
