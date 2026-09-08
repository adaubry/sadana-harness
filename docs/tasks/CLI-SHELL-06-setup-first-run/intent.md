# Intent: one command turns a fresh instance into a working one

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Someone setting up a sadana instance for the first time — a new box for a
new user, or the software newly installed on a machine of their own — cannot
get from "installed" to "actually works" without already knowing, piece by
piece, what the thing needs and where each piece goes. Which provider, which
key, which secret, which one lives where: today that knowledge lives in the
head of whoever set up the last box. A person who does not already know it
gets partway, then hits a wall the first time they actually try to use the
thing — an answer that cannot come back because a key is missing, a
background process that will not start because its secret was never set.
There is no single guided step that takes a fresh instance to ready.

## Proposed outcome

One command turns a fresh instance into a working one. Running it finds out
what is missing for the instance to actually run, and gets each missing
piece into place — either by asking the person running it, or by taking the
values as input when no one is at a keyboard. When it finishes, the instance
can actually be used: an exchange can happen, a message can be received. The
same command can be run again on an instance that is already partly set up;
it fills only what is missing and leaves everything already in place alone.

The smallest version still worth having: a guided run that collects the
provider and its key, and the secret the background process needs — the
things a fresh instance cannot work without — either conversationally or
from input, and stores them so the instance uses them on its next run.

## Affected users and systems

- The person setting up a fresh instance. Today they have to know what is
  missing and where each value goes; with this, one command guides them
  through it.
- Any automated workflow that later stands up a new instance for a user.
  This command is the step that workflow invokes on a box that already
  exists, which is why it must be able to run with no one at the keyboard.
- The part of the system that reports "I cannot run yet because a secret is
  missing." It gains a way to have that gap filled, rather than only to name
  it.
- The background process that receives messages. It gains a way to have its
  secret collected during setup, instead of being left as a separate job for
  the operator to remember.
- Not affected: how a brand-new box gets created in the first place — that
  is a separate system's job, outside this. Nor a person's identity on the
  instance, which is already created on first use and needs no setup step.

## Constraints

- Setup configures a box that already exists. It does not create one,
  register it with any service, or manage accounts on it.
- Whatever already refuses to work when a secret or setting is missing keeps
  doing that. Setup does not add a second, separate check — it only fills
  the gap the existing system already names.
- The values setup stores go through the one configuration path the rest of
  the system already reads. Nothing here invents a second way to hold
  configuration.
- Secrets are stored, never printed back or shown.
- The command must work equally well with a person at the keyboard and with
  no one there, because the caller that matters most later is an automated
  workflow with no keyboard to answer prompts.

## Open questions

- Where the stored credentials land, and how the running instance reads them
  back, is not settled here. The instance reads its settings from its own
  environment today, and its background process reads secrets from one
  specific file; there is no single place a "write it down now, read it back
  on every run" flow already exists. This intent only requires that setup
  leave the instance able to use what it stored. The mechanism is decided at
  design time, by the maintainer.

## Changed during planning

- The blueprint named this item's scope loosely — "credentials, identity,
  initial config" — and deliberately left it unscheduled for having no caller
  yet. The interview changed three things: "identity" dropped out, because it
  is already created automatically on first use and needs no setup step;
  scope settled on the full guided onboarding — provider, key, the background
  process's secret, and any other setting the operator wants — rather than
  just the key; and the command must work both interactively and scripted,
  because its eventual caller is an automated workflow with no keyboard.
  Building it now, before that caller exists, is the maintainer's decision,
  deliberately reversing the blueprint's "not scheduled."
