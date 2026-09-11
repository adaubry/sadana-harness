# Review: The first plugin that makes a file (from plan.md 2026-09-11)

Reviewed: 0e4f537..working tree — 21 files, 8 of them new, 2 renamed
Reviewer context: cold — delegated to a subagent with no context beyond the
diff and the three artifacts, per deploy-skill § 1. Its findings were then
fixed here and both `make verify` and the proof script re-run.
Second opinion: a combined simplification/altitude pass ran during Build
(self-check); its eight findings are summarised under Findings.

## Evidence

`make verify`:

```
docs/tasks/IMAGE-GEN-01-the-first-plugin-that-makes-a-file: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 48 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [  8%]
........................................................................ [ 16%]
........................................................................ [ 24%]
........................................................................ [ 33%]
........................................................................ [ 41%]
........................................................................ [ 49%]
........................................................................ [ 57%]
........................................................................ [ 66%]
........................................................................ [ 74%]
........................................................................ [ 82%]
........................................................................ [ 90%]
........................................................................ [ 99%]
.......                                                                  [100%]
871 passed in 27.49s
TESTS OK
VERIFY OK
```

The real round trip **and the real file** — the half that had never been
exercised, since nothing in this project had written one before
`ARTIFACT-STORE-01` built somewhere to put it. This reports the size and the
magic bytes because the claim being proved is that a picture exists on disk,
not that a request succeeded. Each run bills the owner's account.

```
$ python3 scripts/prove_image_gen.py
key found via SADANA_PLUGIN__IMAGE_GEN__API_KEY (value not shown)
model: google/gemini-2.5-flash-image
output directory: /home/adam/.local/state/sadana/artifacts/prove-image-gen-a46dc22e/0/0
drawing 'a single red bicycle against a plain white wall' … (this bills the account behind the key)

  [approval] image-gen.draw — auto-accepted (no keyboard here)

result text : /home/adam/.local/state/sadana/artifacts/prove-image-gen-a46dc22e/0/0/a-single-red-bicycle-against-a-plain-white-wall.png
failed_node : None   trace=['draw']

file        : /home/adam/.local/state/sadana/artifacts/prove-image-gen-a46dc22e/0/0/a-single-red-bicycle-against-a-plain-white-wall.png
size        : 1,355,360 bytes
first bytes : b'\x89PNG\r\n\x1a\n'
looks like  : PNG

OK: a real PNG was drawn by a real service and written to disk through the plugin graph.
```

The written file was opened and looked at: it is a red bicycle against a plain
white wall, which is what was asked for. A valid PNG of the wrong thing would
have passed every automated check above.

For scale, and for the constraint this item exists to enforce: 1,355,360 bytes
is roughly 1.8 million characters of base64. That is what a body returning the
data URI would have put into the conversation — correctly, and invisibly.

## Findings

The cold review raised seven Important findings and five nits; the Build-stage
self-check raised eight before it. All were acted on. **Three of the cold
review's findings were regressions introduced by the self-check's own fixes** —
which is the most useful thing this chain has produced, and is discussed at the
end.

### Important — fixed in this branch

- **[Security] Moving the `defang` guard into `run_http` narrowed it instead of
  centralising it.** The self-check correctly moved the filter from the two
  plugin bodies to the one place a stranger's bytes become a string a model
  reads. It then applied it to `exc.read()` only — but `exc.reason` is equally
  the other end's: `http.client` reads it off the status line and decodes it
  latin-1, so ANSI escapes and C1 controls passed straight through. The
  previous code, which defanged the *composed* detail at the plugin, had
  covered it. **Fixed:** every `Failure.detail` this function builds now goes
  through one `_safe()` — composed value, reason, `OSError` arm and the
  unsupported-scheme message alike — and a test feeds an escape through the
  reason phrase specifically.

- **[Security] An unbounded third-party string could reach the transcript.**
  `picture()`'s unknown-media-type sentence interpolated `media_type` straight
  from the reply, which `_DATA_URI` bounds not at all. A 200KB media type
  produced a 200,078-character "sentence" that became `DagResult.text` — in the
  one plugin whose entire purpose is that a large payload must not travel.
  Every other string it returns is a constant. **Fixed:** sliced to 40
  characters, with a test that a 200,000-character type yields under 200.

- **[Security] Nothing bounded the reply that was read, parsed, decoded and
  written.** `resp.read()` had no ceiling, so a reply's size was whatever the
  other end chose to send — and this is the first caller that both expects
  megabytes and commits them to disk, with a 180-second window to receive them.
  Not a deferred cost: the chain's size discussion is entirely about retention
  over time, never about one hostile or malfunctioning reply. **Fixed:**
  `run_http` reads at most 32MB and refuses beyond it, with tests at and over
  the ceiling.

- **[Bugs] The seeding call ran before `parse_args`, under a comment claiming
  it ran after.** So `sadana --version` and `--help` created directories and
  copied trees, and could raise `PermissionError` on an unwritable state dir —
  turning pure-stdout commands into tracebacks, and brushing CLAUDE.md's rule
  that argparse's own exits are never given side effects. The comment asserted
  the exact property the code did not have. **Fixed:** moved below
  `parse_args`; the first-run trap it exists to close is unaffected, since
  `plugin settings` dispatches through `args.func`.

- **[Compliance] The plan's amendment described a change that did not ship.**
  It recorded a mypy exclusion for plugin bodies, which the self-check had
  tried and then *reverted*, and claimed bodies lose static checking, which
  they do not. CLAUDE.md: never write a plausible trace for work you did not
  do. **Fixed:** replaced wholesale — not patched — with what actually
  happened, including the five changes it was silent on and the eight files it
  did not name.

- **[Compliance] The body carried an unreachable no-key branch.** The
  `PLUGIN-CONFIG-01` preflight refuses the run before any node starts, so the
  check written again in the body could never fire — and no test could reach
  it. Unreachable code a reader trusts as the live behaviour, returning wording
  that differs from what the preflight prints. **Fixed:** removed from this
  plugin and from `web-search`, which carried the identical dead branch, and
  replaced with an `assert` that fires only if the preflight itself has stopped
  working.

- **[Compliance] Requirement 9's evidence did not exist.** It does now, above.

### Nits — all five fixed

- A timeout hardcoded in a plugin body, where CLAUDE.md keeps behaviours in
  config. Now `SADANA_IMAGE_GEN_TIMEOUT_S`.
- `image_gen.py`'s docstring still referred to `init.py`, in a diff whose point
  was renaming it.
- A Proof sentence named the wrong file for the outside-the-directory test;
  the behaviour is covered by a pre-existing test in `test_plugin_manifest.py`.
- A stale comment in `test_subcommands_chat.py` describing seeding `cmd_chat`
  no longer does.
- `b64decode(validate=True)` rejected line-wrapped and base64url payloads,
  reporting them as corruption a reader could not distinguish from the real
  thing.

### Raised, not findings

- **The containment check is post-write.** The body writes, then `run_graph`
  checks the returned `Artifact`. A symlink pre-planted at the target path
  would be written through before the artifact was refused. That requires
  write access to the owner's own state directory, which already implies more
  than it buys, and the artifact is still rejected — but `spec.md` describes
  the check as satisfying containment, and it satisfies the *recording* half,
  not the *writing* half.
- **Nothing deletes anything, and this is the item that makes it cost.** Three
  proof runs during this work left three 1.3MB files behind. The follow-up
  `intent.md` is owed and is the loosest thread in this chain.
- **Two more maintain findings surfaced from a real failure**, not from
  testing: the repo-root `.env` is a symlink out of the worktree that no code
  path reads, and running from the wrong checkout reports `invalid choice:
  'set'`, which blames the command rather than the build. Both want their own
  `intent.md`.
- **`prove_image_gen.py` costs money each run**, which makes it the kind of
  check nobody re-runs — a photograph, and slightly worse than the search one.

### The pattern worth naming

Three of the seven Important findings were introduced by the *self-check's own
fixes*, not by the original build: the narrowed defang, the mis-ordered
seeding call, and the false amendment. Each fix was correct in direction and
wrong in a detail that only a reader coming to it cold would see. The
self-check moved things to better altitudes; the cold review caught what moving
them broke. That is the separation working as intended, and it is an argument
against ever collapsing the two passes into one — a reviewer who watched a
change being made is the worst person to check whether it landed.

It is also an argument for the cold review running *after* the self-check
rather than before, which is the order `deploy-skill` already prescribes.

## Decision

Approved by adam, 2026-09-11, with all seven Important findings and all five
nits already fixed in this branch before the decision was given.

Accepted as read rather than fixed: nothing deletes the files this item
creates, and the containment check is post-write rather than a write guard.
The three follow-up `intent.md`s this work generated — retention, the
repo-root `.env` nothing reads, and the misleading `invalid choice` error —
are to be written before the next work item starts.
