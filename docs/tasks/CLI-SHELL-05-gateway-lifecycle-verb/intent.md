# Intent: a real instance's background process behaves like a normal service

Author: Adam Aubry (maintainer). Status: draft.

## Problem

An operator setting up or maintaining a real sadana instance has no way to
run its always-on background process the way any other reliable service on
that machine runs — something that starts on boot, restarts itself if it
crashes, and can be checked on or stopped with a normal command. Today the
only way to keep that process alive is to start it by hand in a terminal
and leave it there. That does not survive a reboot, a crash, or the
operator's own SSH session disconnecting — the exact moments a service is
supposed to keep working without anyone watching it.

## Proposed outcome

Someone setting up or maintaining a real instance can install its
background process as a normal, supervised system service in one step,
then start it, stop it, restart it, and check whether it's running — the
same way they would manage any other service on that machine, without
hand-writing any service configuration themselves. Once installed, the
process comes back on its own after a crash and survives a reboot without
anyone having to remember to start it again.

The smallest version still worth having: install, start, stop, restart, and
status — enough for an operator to fully manage the running process's
lifecycle without ever hand-editing a service file or manually backgrounding
a process themselves.

## Affected users and systems

- **The person setting up or maintaining a real instance** — currently the
  only way they can keep the background process alive is to run it by hand
  and hope nothing disturbs the terminal it's running in.
- **Every end user of that instance, indirectly** — their only way of
  reaching the agent when they are not actively typing at it depends on
  this process actually staying up across reboots and crashes.
- **Not affected: how a new instance gets created in the first place.** A
  separate, unbuilt system's job. This item only manages a process on a
  machine that already exists — it does not provision one.

## Constraints

- Service management has to work the way the real machine already manages
  services — nothing here invents a second way of keeping a process alive.
  It has to look and behave like installing any other real, reliable Linux
  service, not a bespoke supervisor.
- Whatever already stops two copies of the background process from running
  at the same time keeps doing that job. This item does not add a second,
  separate version of that check.
- This does not configure the secret or address the background process
  needs to actually work. Those are assumed to already be set before
  someone installs it, the same way any real service assumes its own
  configuration already exists.
- This does not decide how a brand-new machine gets set up for a new user
  for the first time. That is separate, unscheduled work.
- The environment sadana-harness is developed in does not have the real
  service manager this item targets, so this cannot be proven by the
  automated test suite alone the way most other work here is. The real
  proof has to run somewhere that actually has one.

## Changed during planning

- The interview opened already well-formed by design: the maintainer asked
  for this item to be started directly from
  `docs/reference/cli_shell_blueprint.md`'s own item 5, which already named
  the problem, the five-verb surface, and most of the constraints before
  any question was asked. What the interview genuinely resolved rather than
  confirmed: system-level service installation over a simpler user-level
  one (chosen because the process is meant to run whether or not anyone is
  logged in, matching "the primary way end users reach their instance");
  restart-on-crash was pulled into this item's own scope rather than
  deferred, since it costs one line once the service exists at all; and the
  existing single-instance guard already built for the background process
  itself was confirmed sufficient, so this item adds no second one.
- One casual mention of an "uninstall" step, made while proposing how this
  item's own proof script would clean up after itself, was not adopted as a
  sixth verb — the scope stays the five the blueprint actually named.
- The blueprint's own open question — whether sadana-harness would ever
  build the messaging-platform bridge itself, named as the thing that had
  to be decided before this item could even be specced — is what
  `GATEWAY-DAEMON-01` already answered by existing. This item could not
  have been started before that one closed.
