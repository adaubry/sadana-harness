# Intent: Plugins on the console

Author: adam aubry (project owner). Status: approved.

## Problem

Someone using the console today cannot see what plugins are installed on a
harness, cannot tell what a plugin is made of or where it came from, cannot
install a new one, cannot check a candidate plugin before installing it, and
cannot turn one off without deleting it. All of that plugin management exists
only inside a local editor that runs on the harness's own machine and answers
nobody but that machine's browser — none of it reaches the console screens a
person actually works from day to day.

## Proposed outcome

From the console, a person can: browse the plugins installed on a harness and
see what each one is made of — its steps and how they connect — without the
harness running any of that plugin's code; install a new plugin by pointing at
a git repository and a tag; check what a candidate plugin at a given tag would
look like before installing it, again without running anything it contains;
save an edit to a plugin's structure back to the harness under the same
validation the harness already enforces on itself; and turn a plugin off or
back on without removing it, with the harness actually honouring that choice
the next time it decides what a plugin can do. All of this shows up through
the same wire contract every other console-facing capability on this harness
already uses.

## Affected users and systems

This harness's own operator — today, one person, running the console and this
harness side by side — is the direct user. The console is the other system
this touches: it gains five new kinds of thing it can ask a harness about.
Nothing changes for anyone using this harness from the command line or from
the existing local plugin editor; both keep working exactly as they do today,
untouched by this work.

## Constraints

- Nothing done here may run a plugin's own code, at any point — including
  while checking a candidate before installing it.
- The plugin's own file on disk stays the one real definition of a plugin; the
  console never becomes a second place that fact lives.
- Someone else is doing related work on the same shared wire contract at the
  same time. This work stays inside the pieces it already owns and does not
  touch the parts that person is mid-way through, even where it would be easy
  to.
- A capability this harness cannot fully honour yet (upgrading itself) is not
  switched on by this work, even though it would cost nothing to flip on.

## Changed during planning

One thing grew during the interview rather than shrinking: "turning a plugin
off" only works today by coincidence, because nothing has ever actually
written the "off" state to disk. A name-matching gap between how this harness
currently indexes a plugin and how the "is this plugin switched off" check
looks it up means a plugin someone disables could keep running anyway, the
moment this work ships the verb that finally writes "off" for real. That
correctness gap is now part of this item's proposed outcome, not a follow-up
noted for later — because it would otherwise ship as a silent, false safety
control. Past that one addition, the intent arrived unusually well-formed: it
came in as a fully worked technical brief from someone who already knows this
codebase closely, and the interview's real job was pressing on the one place a
judgment call was genuinely open, not originating new scope.
