# Review: Running a program, and saying what that costs (from plan.md 2026-09-11)

Reviewed: 508829b..working tree — 5 files (2 code, 3 artifacts)
Reviewer context: cold — delegated to a subagent with no context beyond the
diff and the three artifacts, per deploy-skill § 1. Its findings were then
fixed here and `make verify` re-run.
Second opinion: a combined simplification/altitude pass ran during Build
(self-check); its eight findings are summarised under Findings.

No proof script. This item adds no external round trip — it runs local
programs, and the tests run real ones (`sys.executable -c`), so the thing
CLAUDE.md requires a standalone script for does not arise.

## Evidence

```
$ make verify
docs/tasks/SUBPROCESS-01-running-a-program-and-saying-what-that-costs: all present artifacts valid
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
........................................................................ [ 32%]
........................................................................ [ 40%]
........................................................................ [ 48%]
........................................................................ [ 56%]
........................................................................ [ 64%]
........................................................................ [ 72%]
........................................................................ [ 80%]
........................................................................ [ 88%]
........................................................................ [ 97%]
..........................                                               [100%]
890 passed in 26.78s
TESTS OK
VERIFY OK
```

## Findings

The cold review raised four Important findings and five nits; the Build-stage
self-check raised eight before it. All were acted on.

**Two of the four Important findings were the item's own claims being false** —
in the one work item whose entire deliverable is an accurate description of
what a primitive does and does not protect against. That is the result worth
recording.

### Important — fixed in this branch

- **[Bugs] The timeout killed one process, not what it started.**
  `subprocess.run`'s own timeout kills the pid it spawned; anything that
  program spawned is reparented and keeps running. Measured: a two-second
  limit on a program that starts a twenty-five-second sleeper returned in two
  seconds and left the sleeper alive, still holding the capture file. The
  docstring's "nothing runs forever" was therefore false for any program that
  spawns — which is most of what this exists for, `browser-use` included.
  **Fixed** with `start_new_session` plus a process-group kill. That flag is
  specifically *not* in the same category as the `setrlimit`/`preexec_fn`
  hazard this item declined: subprocess implements it inside the child it
  forks.

  The regression test earns its place in an unusual way. Removing
  `start_new_session` to check what the test would catch **killed the entire
  test run** — because without it the child shares this process's group and
  `killpg` takes down the caller. The flag and the group kill can never be
  separated, and the code now says so where someone might remove one.

- **[Security] Nothing bounded what a program *wrote*.** Only the read-back
  was capped. Measured: a print loop put **1.12 GB into the temp directory in
  two seconds**, and at the default time limit that is tens of gigabytes
  written and discarded. `spec.md` had claimed such a program "costs disk it
  was going to cost anyway", which was wrong — with a pipe an unread writer
  blocks, and with `capture_output=True` the cost is memory that the same cap
  bounds. **Fixed:** a write ceiling at a generous multiple of the read cap,
  past which the program is stopped, because a program legitimately printing
  more than will be read is normal and should be truncated rather than killed.

- **[Bugs] "Never raises" was false on two reachable paths.** `config.env_int`
  is called outside the `try` and raises on a malformed value; and
  `TypeError` — from a non-string in `argv`, which is exactly what a plugin
  assembling arguments from model-supplied JSON produces — was not caught.
  **Fixed by qualifying the contract rather than widening the catch:** both
  are stated in the docstring as deliberate exceptions. A bad config value
  failing loudly at the one site that reads it is how every config read in
  this project behaves on purpose, and a non-string `argv` is a bug in the
  caller that `run_graph` already reports as the node failing. Swallowing
  either would hide a defect rather than handle one.

- **[Compliance] The `_git` reasoning in `spec.md` overstated its own
  conclusion.** It said "this git refuses the `ext::` transport". It refuses
  it *by default*: `protocol.ext.allow` is a config key, and with
  `-c protocol.ext.allow=always` the helper runs — verified both ways. `_git`
  reads the person's own gitconfig and pins no protocol policy, so the refusal
  belongs to their configuration rather than to this code. The load-bearing
  half is unchanged and still true: git does not transmit its environment to a
  remote, so there is no live exfiltration. **Corrected**, and the owed
  follow-up is bigger than first stated — nothing validates `repo_url`'s
  scheme anywhere, so it needs to pin `protocol.allow` or check the scheme as
  well as scrub the environment.

### Nits — all five fixed

- The `TERM` test was vacuous under the hermetic runner, which uses `env -i`
  and passes no `TERM` — so it passed even if `TERM` were added to the
  allowlist. The exact "satisfied in appearance" shape `plan.md` warns about.
- Nothing asserted `HOME` or `TZ` actually reach the child; deleting either
  from the allowlist broke no test.
- `spec.md` still described `LANG`/`LC_ALL` as inherited after the self-check
  changed them to being set.
- `plan.md` promised "three `Failure`s, three different sentences"; two shared
  one, and neither was asserted.
- `spec.md`'s "that is what a program needs to find its own libraries" was not
  true for a program installed somewhere unusual — `TMPDIR`,
  `LD_LIBRARY_PATH`, `XDG_RUNTIME_DIR` and `DISPLAY` are all absent by design
  and a caller passes what it needs.

### Raised, not findings

- **`run_program` has no caller**, deliberately, the way every early block here
  landed. Its first consumer is blueprint item 6, which is still blocked on
  the posture decision this item deliberately does not make.
- **`PATH` is inherited, and the lookup happens after `chdir`**, so a relative
  entry in a person's `PATH` resolves against a caller-chosen working
  directory — such as an artifact directory a plugin just wrote a file into.
  Requires the person's `PATH` to contain a relative entry. Not worth a
  mechanism given the stated posture; worth knowing.
- **Memory is still uncovered.** The write ceiling closes the disk half of what
  `setrlimit` would have done; a program that allocates until the machine
  swaps is stopped by nothing here.
- **`wait()` after `kill()` has no timeout**, so an unkillable child stuck in
  uninterruptible I/O hangs past the limit. Exotic enough not to code around.
- **A multibyte character split at the byte cap** decodes to one U+FFFD.
  Correct behaviour for a byte cap; the only consequence is that truncated
  output is not guaranteed parseable, which truncation already implies.
- **Three tests really sleep**, about three seconds total. `testing-conventions`
  tolerates it and the bounds are loose enough not to flake on a busy machine.

## Decision

Approved by adam, 2026-09-11, with all four Important findings and all five
nits already fixed in this branch before the decision was given.

Accepted as read rather than fixed: memory is uncovered (the write ceiling
closes only the disk half of what `setrlimit` would have done), `PATH` is
inherited, and `plugin_install._git` still inherits the whole parent
environment and pins no git protocol policy — owed as its own work item, not
folded in here.

The posture question this item deliberately did not answer — whether to run
software this project did not write — remains open and still gates blueprint
item 6.
