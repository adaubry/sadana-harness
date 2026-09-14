# Spec: Approval and ownership in the work-item chain

Intent: docs/tasks/H15-chain-approval-ownership-plan/intent.md

Author: Adam Aubry (product owner). Status: approved.

## Requirements

Each traces to a sentence in `intent.md`.

1. `spec.md` and `plan.md` each carry an `Author: … Status: …` line, checked
   the same way `intent.md`'s already is. (Intent: "a word only that person
   writes" — there is nowhere to write it today.)
2. Writing a stage's artifact is refused unless the **previous** stage's
   artifact reads `Status: approved`, case-insensitively, trailing punctuation
   ignored. (Intent: "nothing … happens before the word is there".)
3. A write or edit to a guarded path — `src/…` or `tests/…` — is refused
   unless the active work item's `plan.md` reads approved. (Same sentence.)
4. A write or edit to a guarded path is refused unless that `plan.md`'s
   `## Files that change` section names the path, exactly or by an `fnmatch`
   glob. (Intent: "may touch only the files its own plan named".)
5. Each refusal names the file it is about and says what to do next. The
   approval refusal says the word is the user's and must not be written by the
   agent. The ownership refusal says to add the path *and tell the user*,
   because the agreement was for the older list. (Intent: "refused by name, at
   the moment it is attempted, with a sentence saying what to do instead".)
6. A commit is refused unless `intent.md`, `spec.md` and `plan.md` all read
   approved. (Intent: "nothing that changes the program happens before".)
7. The Bash-side hook gates `tests/…` the way it already gates `src/…` and
   `docs/tasks/…`. (Intent: "a guard … which could be talked around until now
   by writing a file from inside a shell command".)
8. `CLAUDE.md`'s "Do not" list gains **one** entry covering both halves of the
   honour rule: writing the approval word, and widening `## Files that change`
   after approval without saying so. (Intent: "merged into one entry … because
   they are one rule".)
9. Whatever prints a stage template shows `Status: draft`, never the other
   word. (Intent constraint: "No agent writes it, in any file, for any
   reason".)
10. `docs/reference/console_fit_plan.md` exists, hand-authored and tracked,
    with the six sections the intent's "the plan for this machine exists as a
    tracked document" names, and every citation of it from tracked content
    resolves under `scripts/check_reference_citations.py`.
11. `docs/reference/cli_shell_blueprint.md` §7 question 6 is marked
    **Resolved** in the same in-place form question 5 already uses, pointing at
    `console_fit_plan.md` §2. Nothing else in that file changes. (Intent: the
    eight settled decisions are "recorded rather than reopened".)
12. `scripts/artifact.py` stays one file with its four commands, and `STAGES`
    stays the only place a stage's rules are tuned. (Intent constraint.)

## Design

Two files that already exist get smaller changes than the prose below
suggests: everything mechanical is two pure functions, two table entries and
three refusals. The rest of this section is why each one is shaped that way,
and the four design guidelines worked in order.

### Where it lives

Everything mechanical lands in two files that already exist:

- `scripts/artifact.py` — two new pure functions, two new `header_rules`
  entries, three new refusals inside `cmd_gate`. No new command, no new module,
  no import added beyond `fnmatch`.
- `.claude/hooks/gate-artifact-chain.sh` — one alternation added to `pathre`.

and one test file, `tests/unit/test_artifact_gate.py`, which is the first test
in this repository to exercise anything under `scripts/`.

The documents are `docs/reference/console_fit_plan.md` (new), an in-place edit
to `docs/reference/cli_shell_blueprint.md` §7, and one bullet in `CLAUDE.md`.

### The two functions

```python
STATUS_RE = re.compile(r"^Author:.*?Status:\s*([^\s.,;]+)", re.M)

def status_of(path: Path) -> str:
    """The approval word, lowercased, or "" when there is no header line."""

def planned_paths(text: str) -> set[str]:
    """Every path-shaped token in plan.md's '## Files that change'."""
```

`status_of` reads the file at gate time. Nothing is cached, nothing is stored:
the approval *is* the bytes in the tracked artifact, and `git log -p` is its
audit trail. `approved(path)` is `status_of(path) == "approved"` and is small
enough to stay inline.

`planned_paths` reuses the `sections_of`/`norm` pair the validator already
uses, then collects every token containing a `/` from that section, with
backticks stripped. That is deliberately more permissive than parsing the
section's *layout*: the four plan formats already in the tree disagree with
each other (comma-separated line, `-` bullets with em-dash prose that wraps
onto lines carrying further paths, and two different four-space aligned-column
styles), and a parser that understands three of them refuses the fourth. A
token scan understands all four and is six lines. `(new)`/`(edit)`/`(delete)`
annotations fall out by construction — they contain no `/`, so they are not
paths.

Matching is `fnmatch.fnmatch(target, entry)` after normalising `\` to `/` and
stripping a leading `./`, so a lane may write `src/sadana/console/*` and own a
directory.

### The three refusals in `cmd_gate`

Same three branches the function already has; each gains one check, in the
cheapest order, so an unapproved plan is reported before a path is parsed.

1. *Artifact branch.* After the existing "every earlier stage validates" loop,
   the immediately previous stage's `Status` is read. Transitivity does the
   rest: `spec.md` cannot exist until `intent.md` is approved, so checking one
   link checks the chain.
2. *Source branch.* `plan.md` must validate (as today), then read approved,
   then name the path.
3. *Commit branch.* The existing loop over the three pre-deploy stages gains
   an approval read per stage.

### The hook

`pathre` becomes `(docs/tasks/…|src/…|tests/…)`. The `file_path` branch already
routes every ordinary Write/Edit through `artifact.py`; this closes the shell
half, where `cat > tests/unit/test_x.py <<EOF` is a write wearing a Bash coat.

### Policies applied

- **testing-conventions** — the new test is `unit`-marked, touches only
  `tmp_path`, and *executes* `artifact.py` rather than reading its text, which
  is the banned move. See `## Concerns` for the tension it does raise.
- **project-structure** — no such skill exists in `.claude/skills/`; the
  placement rule that applies is CLAUDE.md's own ("a Make target is one line";
  "lint and format belong to pre-commit"), and nothing here adds to either.
- **reference-lookup** — no such skill exists either; guideline 1 was worked
  directly against `docs/reference/hermes_core_blocks_kind.csv`, below.

### Guideline 1 — what the reference did

Filtered to `SAFETY`, `kind == production-code`. Three files are analogous and
were read:

- `tools/approval.py` (5,971 lines) is hermes's approval system: pattern
  detection, a per-session in-memory approval state keyed by `session_key`, an
  auxiliary-LLM auto-approver, and a permanent allowlist persisted to
  `config.yaml` and matched with `fnmatch`. **Adopted:** `fnmatch` as the
  matching primitive for an allowlist of paths — it is what they settled on
  after the same problem. **Declined:** everything else. Their approval is
  per-session process state because their approver is a person at a prompt in
  the middle of a run; ours is a person reading a document *before* the run,
  so the record belongs in the tracked artifact, where it survives the process
  and shows up in the commit. Their auto-approver is the opposite of this work
  item's point.
- `tools/approval.py:36-38` freezes `HERMES_YOLO_MODE` at import "because
  reading `os.environ` on every call would allow any skill running inside the
  process to set this variable and instantly bypass all approval checks — a
  prompt-injection escalation path." **Adopted as a constraint, not as code:**
  our marker lives in a tracked file rather than an environment variable for
  the same reason, and there is no bypass switch at all.
- `tools/path_security.py:15` (`validate_within_dir`, `resolve()` +
  `relative_to()`). **Declined here, deliberately.** We match the
  *repo-relative string as written*, never a resolved filesystem path: the
  guarded-set test is already `^(src|tests)/` on that string, and anything
  weird (`src/../../etc/x`, a symlink) fails to match any plan entry and is
  therefore refused, which is the safe direction. Resolving first would turn a
  refusal into a path that no longer starts with `src/` and is waved through.

### Guideline 2 — the bets

This work item sits entirely *outside* the plugin seam: it is the repository's
own build tooling, and nothing in `src/sadana/` imports it or ever will. The
"future value arrives as an addition" form still applies in its local shape —
a new stage, a new rule or a new guarded prefix is a line in `STAGES` or a line
in `pathre`, not a change to `cmd_gate`. Two bets are taken and they are
small: that `Status:` on the author line is where approval lives, and that
`## Files that change` is where ownership lives. Both are reversible by editing
one function.

### Guideline 3 — which of the three moves

**It makes an existing step heavier.** The gate already runs on every write;
it gains two file reads and a set membership test. Two cheaper-looking
alternatives were considered and are in `## Rejected alternatives`: a new
pre-commit hook (a *new step*) catches the same scenario, but at commit time —
after the work is done, which is the exact cost `intent.md` names — and a
harder step (refusing all source writes until a human types a command) catches
it at the price of stopping every well-behaved chain too. The weight this puts
on every future caller is bounded: two `Path.read_text` calls of a file the
gate already opened once, on a path that only runs for guarded prefixes.

### Guideline 4 — state inventory

**No new stored state.** Both new facts are derived at gate time from bytes
that are already tracked: the approval word from the artifact's header line,
the owned paths from the artifact's own section. Nothing is cached, so nothing
can go stale, and there is no migration story. The one thing that *looks* like
state — `.claude/active-task` — already exists and is unchanged.

## Interface

`scripts/artifact.py`, importable as `scripts.artifact` (the sibling test
`tests/unit/test_check_reference_citations.py` already does this; `python -m
pytest` puts the repository root on `sys.path`).

| Name | In | Out |
| --- | --- | --- |
| `status_of(path)` | `Path` | lowercased word, or `""` if absent/missing |
| `planned_paths(text)` | `plan.md` text | `set[str]` of path-shaped tokens |
| `cmd_gate(action, target)` | `"write"｜"edit"｜"commit"`, repo-relative path | `0` allow, `2` block + reason on stderr |

Three refusals, verbatim, because the tests pin them:

```
Blocked: <dir>/<artifact> is not approved.
  Status is '<word>'; the user writes 'approved' after reading it.
  Do not write that word yourself.
```

```
Blocked: <dir>/plan.md does not name <path> under '## Files that change'.
Add it there and tell the user you did — the approval was for the old list. Or stop.
```

```
Blocked: cannot commit — <dir>/<artifact> is not approved.
  Status is '<word>'; the user writes 'approved' after reading it.
  Do not write that word yourself.
```

Nothing else crosses a boundary: no new command, no new exit code, no change
to `new`/`status`/`check`.

## Acceptance criteria

- [ ] `spec.md` and `plan.md` without an `Author: … Status: …` line fail
      `scripts/artifact.py check`.
- [ ] With `intent.md` at `draft`, writing `spec.md` is refused and the message
      names `intent.md` and the word `draft`.
- [ ] With `plan.md` at `draft`, writing `src/sadana/x.py` is refused with the
      approval sentence.
- [ ] With `plan.md` approved but `src/sadana/x.py` absent from `## Files that
      change`, the write is refused with the ownership sentence.
- [ ] With the path listed, the write is allowed.
- [ ] A listed glob (`src/sadana/console/*`) allows a file underneath it.
- [ ] `path/one.py (new)` parses to `path/one.py`.
- [ ] A comma-separated line and a bulleted list both parse.
- [ ] Commit is refused while any of the three reads `draft`, and allowed when
      all three read approved.
- [ ] A path outside `src/` and `tests/` is allowed exactly as it is today.
- [ ] The hook's `pathre` matches a heredoc into `tests/`.
- [ ] `CLAUDE.md` has one new "Do not" bullet, not two.
- [ ] `docs/reference/console_fit_plan.md` exists with §1–§6; `make lint`
      passes, which runs the citation checker.
- [ ] `cli_shell_blueprint.md` differs only in §7 question 6.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Enforcing that a widened `## Files that change` resets the status. Declined
  below; the sentence tells the truth instead.
- Guarding `scripts/`, `docs/` or `.claude/`. The guarded set is unchanged —
  `intent.md`'s open question, left open.
- Any code in `src/sadana/`. Nothing the product ships changes.
- Moving the chain to the other repository. That is its own step.

## Rejected alternatives

- **A fingerprint of the approved file list** (`Approved-files: <hash>`,
  recomputed by the gate). Rejected: anyone who can edit one line can edit two,
  so it stops nobody who intends to; it costs a person a second thing to type
  correctly on every approval; and it makes the machine assert a guarantee it
  does not have. The honest framing was chosen instead — this is a speed bump
  against drift, not a control against intent, and what it catches is an agent
  that forgot the chain, which is the same agent that forgets to touch
  `Status:`.
- **A separate approval artifact** (`approved.md`, or an `approvals/`
  directory), which is roughly hermes's `config.yaml` allowlist shape.
  Rejected: a second file to keep in step with the first, and it separates the
  agreement from the thing agreed to. The status line sits three lines above
  the text it approves.
- **Per-session approval state, hermes-style.** Rejected: it does not survive
  the process, and this approval has to survive into the commit.
- **A new pre-commit hook instead of a gate check** (guideline 3's "add a
  step"). Rejected: it catches the scenario after the work is written, which is
  the cost `intent.md` exists to remove. It is also where the *commit* half of
  this already lives, so the scenario is caught twice — early by the gate,
  finally by the commit branch.
- **`importlib.util.spec_from_file_location` to load the gate in the test**, as
  `model_access._discover` does for providers. Rejected in favour of a plain
  `from scripts.artifact import …`: the sibling `test_check_reference_
  citations.py` proves the plain import already works under the hermetic
  runner, `scripts/artifact.py` has no hyphen in its name and no package
  ambiguity to dodge, and the `importlib` dance is six lines that buy nothing
  here. `_discover`'s reason for `spec_from_file_location` — hyphenated
  directory names that are not valid Python identifiers — does not apply.
- **Parsing `## Files that change` by layout** (first token per bullet or
  comma-separated fragment). Rejected: four incompatible formats are already in
  the tree, and a wrapped bullet puts a second real path on a continuation
  line where a layout parser would not look. Missing a path means a false
  refusal, which teaches people to route around the gate.
- **Resolving the target path before matching** (`tools/path_security.py`'s
  shape). Rejected above under guideline 1: it converts an odd path from a
  refusal into an allow.

## Open questions

- `sections_of` splits on any `#`-heading, so a `###` sub-heading *inside*
  `## Files that change` would end that section early and hide paths below it.
  No plan in the tree does this, and the build-skill's format does not produce
  it. Left as a known ceiling rather than a parser that understands nesting.

## Concerns

**The policy tension worth reading.** `testing-conventions` says a unit test
touches "only the module under test", and CLAUDE.md says "isolate by process,
and delete the state-reset fixtures when testing". The gate resolves
`docs/tasks` and `.claude/active-task` relative to the process's current
directory, so testing it *requires* mutating process-global state — the exact
thing both rules are pointed at. Both cannot be satisfied. The design followed
testing-conventions and accepted the process mutation, using
`monkeypatch.chdir(tmp_path)`, which pytest restores on teardown, rather than a
hand-rolled fixture that could leak a wrong directory into every test after it.
The cost is real and named: this test file is not safe under `pytest-xdist`
should the suite ever gain it. The alternative — giving `cmd_gate` a `root`
parameter so no directory change is needed — was not taken because it widens
the one-file tool's interface for the benefit of its test, which is the tail
wagging the dog; it is the obvious fix if parallelism ever arrives.

**The over-permissive parser, on purpose.** Any path-shaped token in the
section counts as a declaration, including one that appears in prose ("…
mirrors `tests/fixtures/plugins/plugin-a/`…"). A plan can therefore accidentally
own a file it only mentioned. This was chosen over the opposite error: a false
refusal trains people to work around the gate, and a gate that is worked around
guards nothing. Reviewers should read `## Files that change` as a list of
*claims*, which is what it already was.

**`fnmatch`'s `*` crosses `/`.** `tests/*` in a plan owns every test file, not
just the top level. Documented rather than fixed — a lane that wants a
directory writes one, and a lane that writes `tests/*` has said something
sweeping out loud.

**This work item blocks its own review.** Once the gate lands, `review.md`
cannot be written until `plan.md` reads approved, and the commit cannot happen
until all three do. That is the mechanism working on its first customer, and it
means the deploy stage of this item has a human in it by construction.

**The guarded set still excludes `scripts/`.** `artifact.py` — the principal
file this work item changes — is not covered by the ownership rule it adds, and
neither is the hook. Left as `intent.md`'s open question; naming it here so a
reviewer does not have to discover it.

**One thing I am uneasy about and cannot close.** Requirement 9 says the
template shows `Status: draft`, but the templates live in three skill files and
in `cmd_new`'s printed hint, and a skill file is instruction to a model, not
code — nothing validates that a skill's template text stayed correct. If one
drifts, the only symptom is an agent writing a header line the validator then
rejects, which is a loud failure rather than a silent one. Acceptable, but it
is the least-guarded requirement in this spec.

## CLAUDE.md amendment, for approval

This spec establishes one rule that binds every work item, not just this one.
Proposed entry under **## Do not**, replacing nothing:

> - Write `Status: approved` in any artifact, or add a path to `## Files that
>   change` after approval without saying so in your next message. That word is
>   the user's, and it is the approval.
