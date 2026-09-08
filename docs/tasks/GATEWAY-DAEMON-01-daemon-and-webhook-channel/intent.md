# Intent: A sadana instance can only be reached while someone is typing at it

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Today, sadana only exists for as long as a person is sitting at a terminal
running it. The moment that terminal closes, sadana is gone — nothing can
wake it up, and nobody can send it a message and expect an answer later. A
person who wants their agent to receive a message from a phone, a chat app,
or any outside system, and act on it even while they're away, cannot do
that with sadana at all today. This isn't a missing convenience with a
workaround — it's the ceiling on what sadana can be used for. Every
instance this project deploys is meant to be a standing presence a user can
message any time, not a tool they have to remember to launch, and right now
that presence doesn't exist.

## Proposed outcome

A sadana instance can run as a long-lived background process on its host
that starts on its own, stays up, and answers a message that arrives from
outside it — even when nobody is running a command at that moment. The
first way a message can arrive from outside is a generic incoming webhook.
Sending a reply back out over that same channel works too, using whatever
already answers a `sadana chat` message today. Someone adding a second way
for a message to arrive later (a chat platform, email, SMS...) can register
it without touching the background process's own code, and can remove it
without breaking any other one already registered.

The smallest version still worth having: the process can be started once
and left running, a message sent to it over the webhook produces a real
reply sent back the same way, and nothing about that exchange required a
person to type a command.

## Affected users and systems

- **The end user of a sadana instance** — currently the only person who can
  talk to their own agent, and only by running `sadana chat` themselves.
  This closes that gap for the first channel; every later channel adapter
  builds on the same door.
- **The CLI-SHELL block's own blueprint** — its work items 5 (`sadana
  gateway`) and 6 (`sadana setup`) are blocked directly on this not
  existing yet; this closes that bottleneck.
- **Every future channel adapter** (Telegram, Slack, email, SMS, Discord,
  and the rest) — each becomes its own later work item, but none of them
  can exist before the registry contract this item introduces does.
- **Not affected**: any fleet or multi-instance control plane. Out of
  scope for sadana-harness by the project's own EC2 deployment model (one
  user, one instance), not something this item touches or defers.

## Constraints

- One EC2 instance per user, always the same physical host. No
  cross-instance federation, relaying, load balancing across instances, or
  scale-to-zero — none of that exists in this project's deployment model,
  so none of it is being built here, deferred, or worked around.
- Every message in or out still goes through the conversation/turn/plugin
  machinery sadana already has. This item adds a way in and a way out for
  a message — it does not add a second way to hold or act on one.
- The registry that channel adapters plug into is its own new contract,
  separate from sadana's existing plugin system (the one a model invokes
  mid-turn). The two look similar on the surface but solve different
  problems; treating them as the same thing would corrupt both.
- Hermes has already paid for this design in production, at a scale this
  project will not redo from scratch. Its shape is adopted where it
  applies to a single always-on instance, and declined explicitly, not
  silently, everywhere it assumes multiple gateways, multiple tenants, or
  a hosting provider's own infrastructure.
- This item does not build every channel adapter hermes has. It builds the
  background process, the registry, and one working adapter that proves
  the whole path end to end. Every other adapter is later work, on
  purpose, not narrowed scope in disguise — the intent is to eventually
  build as many as are worth having, not to stop at one.

## Shape

One commit. This was put to the maintainer directly, twice, because it is
the most consequential shape decision this project has made so far:

First, whether this item's own deliverable should be understanding and an
ordered plan — the same shape CLI-SHELL's own blueprint took for a block
this size — rather than working code. Declined: "this block will be huge
but it will be worth it, do not settle for 'smallest working piece' ...
when it comes to giving the end user the most amount of options to use
sadana."

Second, whether the reference-corpus research behind this item should
become its own blueprint document under `docs/reference/`, matching
`cli_shell_blueprint.md`'s own precedent. Declined: "no blueprint, this
work item is already made to resolve a bottleneck in the cli blueprint."

Given both, the honest one-commit scope — the reference material here is
223,000+ lines across 207 files, more than three times CLI-SHELL's own
combined block size, so none of it was ever landing whole regardless — is:
a working background process (start, stop, one inbound-to-outbound round
trip wired to sadana's existing turn machinery), a real channel-registry
contract an adapter plugs into, and one working adapter proving the round
trip end to end. Every further adapter becomes its own fast-following work
item, the same way CLI-SHELL's blueprint produced four.

## Changed during planning

- The work item's own slug changed from "gateway-channels-blueprint" to
  "daemon-and-webhook-channel" before any artifact was written, once the
  maintainer rejected a blueprint-shaped deliverable for it — the old name
  no longer described what this item ships.
- The interview itself was originally framed around "blueprint-first,
  minimal daemon slice" — deliberately mirroring CLI-SHELL's own
  precedent for a block this size. The maintainer rejected both halves of
  that framing directly, twice (see § Shape), landing on a materially
  larger, more concrete deliverable than the interview opened with.
- Four parallel research passes (the channel-registry contract, a
  representative adapter difficulty/build-order survey, the outbound
  tool surface plus a full platform catalog, and the daemon's own core
  lifecycle) replaced the "one combined blueprint document" originally
  floated as this item's output. Their findings carry forward into
  `spec.md`'s own reference-corpus section directly, rather than into a
  separate `docs/reference/` file, per the maintainer's own "no blueprint"
  instruction.
