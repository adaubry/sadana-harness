# Intent: Scope the gitignore to this project

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Anyone setting up or reviewing this repository cannot tell, by reading the
ignore file, what this project actually needs kept out of version control.
The file in place is a generic template pulled in for Python projects in
general, and it lists tools this project does not use. It also hides some
directories more broadly than intended: a directory literally named the same
as a common build output, anywhere in the source tree, would be hidden from
git — not only one sitting at the project root.

## Proposed outcome

The ignore rules cover exactly what this project's own tooling produces, and
nothing else — reading the file top to bottom takes under a minute and every
line is traceable to something this repo actually generates. A directory
inside the source tree that happens to share a name with a common build
output is never silently hidden. A minimal, separate allowance stays for
common editor and OS artifacts, since those come from whoever's machine is
open, not from this project's own tooling.

## Affected users and systems

Only the maintainer works in this repository today; there are no other
contributors or forks to account for. The pre-commit hooks that already run
against every commit are the system that would otherwise be the last check
against something unwanted getting tracked.

## Constraints

Nothing currently tracked in git may end up excluded by the new rules —
verified before writing this: no venv, cache, or environment file is
presently committed, so there is nothing this change would need to untrack.
The rules must stay traceable to what this project's own tooling produces
(virtual environment, bytecode/tool caches, packaging output, secrets/env
files, the session-state file, bootstrap backups) rather than anticipating
tools not in use here.

## Changed during planning

Went in framed as "leaks the gitignore has," which implied something had
already been committed by mistake; checked git history and tracked files
first and found nothing sensitive was ever tracked, so this is preventative
scoping, not a leak repair, and the intent above says so plainly. Also
narrowed on editor/OS entries: the first proposal dropped them entirely, and
the interview surfaced that a minimal section (editor/OS artifacts only) is
cheap enough to keep as a deliberate allowance rather than an oversight.
Confirmed the audience is solo (no other contributors) and that this is a
single file, single commit change with nothing to split out.
