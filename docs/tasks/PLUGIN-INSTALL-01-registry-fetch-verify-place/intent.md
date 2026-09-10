# Intent: Naming a plugin is enough to get a verified copy of it

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Right now, using someone else's plugin means copying its files onto your
machine by hand: finding its repository, picking a version, placing the
files exactly where the running agent expects them, and trusting — with no
way to check — that what you copied is actually the version you meant to
get. There is no way to ask for a plugin by name and have the right,
verified copy simply appear. And there is nowhere to even look up what
repository a given plugin name refers to unless someone tells you directly.

## Proposed outcome

A person can name a plugin they want and the exact released version of it,
and get back a verified, ready-to-use copy sitting where the agent already
looks for installed plugins — without touching a repository, a URL, or a
file by hand.

Behind that name lookup is a registry that records which plugin name points
to which repository. It doesn't exist anywhere today; this is the first
place it will. For now, only the person running this instance can add a
name to it.

Before anything is put in place, the tool confirms that what it fetched is
genuinely the released version that was asked for, not something that
arrived in its place. If that check fails, it refuses rather than silently
accepting a mismatch. Installing over a plugin that's already there under
that name is refused too, unless replacing it is explicitly asked for.

## Affected users and systems

Just the person running this sadana instance today — this replaces their
own manual copy-and-hope step. Nobody else needs a plugin installed by
anyone but themselves yet.

What notices this changed: whichever part of the agent already reads
installed plugins from disk starts seeing real entries that arrived this
way, instead of ones placed there by hand — no change needed on that side.
The registry is genuinely new: a place that holds plugin names now exists
where nothing did before.

## Constraints

- Only a git repository at a tag counts as a "released version" here — not
  a branch, not an arbitrary commit. That already matches how a plugin's
  own version is understood project-wide.
- A plugin that arrives this way is placed as a whole and never edited
  afterward — installing again under the same name is a fresh placement,
  never a patch of what's already there.
- Public repositories only. No credential of any kind is involved in
  fetching a plugin, so nothing here touches secrets.
- Only the person running this instance can register a name in the
  registry. There is no open submission process yet.
- Whether it is safe to actually run a newly installed plugin's code is a
  separate, already-deferred question. This work item ends once a verified
  copy is sitting on disk — it does not execute anything from it.

## Open questions

- Whether this registry is ever shared across machines, or stays local to
  one instance, is left open. It only matters once a marketplace becomes
  its own work item — this registry is deliberately just the seed of that,
  not a design for it.

## Changed during planning

The interview changed the shape of this item twice. First, "confirm it's
really what it says it is" narrowed from a broad idea to one concrete
check: the fetched tree matches its claimed tag, not an author-signature
check. Second, and more consequentially, the source of a plugin moved from
"a URL given directly" to "looked up by name in a registry this same work
item also builds" — which reaches into ground the reference blueprint
(plugin_blueprint.md §9) explicitly calls out as the marketplace's
"discovery" and defers. The user chose to accept that expansion deliberately,
on the record: this registry is meant to become what a future marketplace is
built on, not a throwaway. To keep it from quietly becoming the marketplace
itself, registration was scoped down to instance-owner-only with no public
submission, and reinstall was settled as refuse-by-default rather than
silent overwrite. Both narrowings kept this a single work item instead of
forcing a split into a registry item and an install item.
