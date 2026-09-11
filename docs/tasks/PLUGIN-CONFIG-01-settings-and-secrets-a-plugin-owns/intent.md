# Intent: Settings and secrets a plugin owns

Author: adam aubry (owner). Status: draft.

## Problem

Someone installs a plugin that talks to an outside service — a weather
service, a search service, a home automation hub. That service wants an
account key, and the plugin needs a setting or two of its own: which region,
how long to wait, which of the service's tiers to use.

Right now none of that is possible, at three separate points.

Nobody is told. The plugin cannot say what it needs, so the person who
installed it finds out only when it fails, and the failure does not say what
is missing.

There is nowhere to put it. The one place this project stores secrets is a
short list of values it was built knowing about — the model provider's key
and the daemon's shared secret. A stranger's plugin is not on that list and
cannot get on it, because the list is written into the program.

The plugin cannot read it if it were there. When a plugin runs, each step
receives the previous step's output and nothing else. There is no channel by
which a step learns "the key for the service you are about to call is this."

The consequence is larger than the inconvenience. Every capability this
project has planned to bring over from the reference system — search, images,
speech, home automation, browsing — needs an account key at the outside
service. Not one of them can be installed and run until this exists. It is
the smallest piece of the plan and it holds up all of the rest.

## Proposed outcome

A plugin can state, as part of itself, the values it needs in order to work,
and whether each one is a secret or an ordinary setting.

Anyone can see that list before installing, and is told about it at install
time rather than at first failure.

The person supplies those values once. Secrets go where this project already
keeps secrets. Settings go where it already keeps settings. Neither ends up
inside the plugin's own files, where an update would overwrite them or a
backup would copy them somewhere they should not go.

When the plugin runs, the steps that need those values have them, supplied by
the system rather than by the conversation. A secret never appears in what
the model reads or writes, and the model cannot choose, invent, or override
one.

If a required value has not been supplied, the run stops with a message that
names the value and says how to supply it, instead of failing inside a call
to an outside service.

The smallest version still worth having: one declaration in the plugin, one
way for a person to fill it in, and the values reaching a running step. A
way to edit them later, share them between plugins, rotate them, or hold a
different set per end user are all separate, later, and not part of this.

## Affected users and systems

The person running this project, who has to supply the values. Today that
person is the owner alone, and this work assumes that — one installation, one
set of credentials, the same for every conversation.

The author of a plugin, who has to declare what their plugin needs and can
then stop writing setup instructions in prose that nobody reads.

What finds out that this changed:

- What a plugin is. Plugins gain a part they did not have, so every existing
  plugin has to keep working without one.
- The check that decides whether a plugin is valid. A new declaration is a new
  way to be wrong, and it has to be caught before anything runs.
- Installing. The moment a person learns what a plugin will need is the moment
  they install it.
- The first-run setup conversation, which today asks for a fixed pair of
  values and would be the natural place to ask for others.
- Running a plugin. The values have to arrive at the step that uses them.
- The place where plugins are submitted and reviewed before others can find
  them. A plugin that asks for secrets is a plugin worth looking at more
  carefully, and whoever reviews one needs to see what it asks for.
- Anyone reading a plugin's record of what it did. A secret must not be in it.

## Constraints

Secrets and settings are different things and are not stored in the same
place. This project already separates them and the separation holds here: a
key is a secret, a timeout is a setting, and a plugin declaring both gets both
treated according to what it is.

The model never sees a secret and never supplies one. Where a running step
gets its identity today, it gets it from the system, placed so that anything
the model said is overridden. A secret arrives the same way or not at all.

A plugin reads its own values and no other plugin's. The values are keyed by
the plugin, and a plugin cannot ask for another one's.

A plugin's name decides where its values are looked up, and a name arriving
from outside this project is not trusted to be a sane one. Names are checked
against what a name is allowed to be before they are used to find anything.

What the model is told about the available tools does not change during a
conversation. Supplying a value, or failing to, cannot alter that.

Nothing here becomes a general settings system. This is one facility for one
kind of thing, built for the plugins that exist. If a second kind of consumer
appears later it can have its own answer then.

An existing plugin that declares nothing keeps working exactly as it does
today, with no file changes and no migration.

## Open questions

Whether the first-run setup conversation grows to ask for plugin values, or
whether filling them in is its own separate step, is a question about how a
person's first ten minutes should go. The owner answers it; the design stage
proposes.

Whether a plugin may declare a value as optional with a default, or whether
every declared value is required, is undecided. Optional-with-default is what
turns a setting into something a plugin author can use freely; required-only
is smaller and can be widened later.

## Changed during planning

The intent arrived well formed, because it was derived from a design document
written against this exact gap rather than from a fresh conversation — and
that is worth naming as a weakness, not a strength. The blueprint framed this
as a namespace for a plugin's API key. Three things changed when it was
checked against what is actually in the tree.

The problem turned out to be three problems, not one. There is no place to put
a value, and no way for a plugin to say it needs one, and no channel by which
a running step receives one. The blueprint named only the first. The second
and third are what make it a work item rather than a directory.

Scope narrowed once. An earlier reading had this covering the case where each
end user of the system has their own credentials for a service. That is a
different thing — it belongs to the identity that outlives a conversation,
which this project has already separated from a conversation on purpose — and
it is now explicitly out.

One item moved out of the constraints. "Settings should be editable later
without reinstalling" was written down as a constraint and is not one: a good
design would give it away for free, and no design is forbidden by it. It is a
preference, and it has been dropped rather than smuggled in as a requirement.

The stated dependency was checked rather than trusted. The blueprint asserts
this item blocks the five after it. Every capability on its list reaches an
outside service that authenticates, and nothing in the tree today can carry a
credential to a plugin step, so the claim holds.
