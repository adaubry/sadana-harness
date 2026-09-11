# Plan: Running a program, and saying what that costs (from intent.md 2026-09-11)

## Files that change

    src/sadana/execution.py            ProgramRequest, Ran, ProgramOutcome,
                                       run_program, the environment allowlist,
                                       two config keys, and the docstring
                                       paragraph that is the real deliverable
    tests/unit/test_execution.py       every branch of run_program, plus the
                                       existing run_http tests passing unedited

Two files. The intent's whole scope is one function, and nothing else in the
tree calls it yet — deliberately, the same way every early block here landed
with no caller.

## Order of work

1. **The types and the environment.** `ProgramRequest`, `Ran`,
   `ProgramOutcome`, and `_child_env(request)` — the allowlist merge, which is
   pure and is the piece with the exposure in it. Tests first for the
   environment, because "no secret is inherited" is the requirement most
   easily satisfied in appearance.

2. **`run_program` itself.** Temporary files for both streams, the bounded
   read back, the timeout, and each failure branch returning rather than
   raising.

3. **The docstring.** Written last on purpose: after the code exists, so it
   describes what was built rather than what was intended. It states the
   posture in the intent's own words.

4. **Self-check, then verify.** `/ponytail-review` and `/simplify` over the
   diff, act on what is worth taking now, then `make verify`.

## Risks

**What this could break, by name.** `execution.py` is imported by both plugin
bodies and by `model_providers/openrouter`. Everything here is additive — new
types, a new function, two new config keys — so `run_http` and its callers are
untouched. The proof is `tests/unit/test_execution.py`'s existing tests passing
with no edit; if any needs changing, something was not additive.

**The riskiest step is 1, and not because it is hard.** "The child's
environment contains no secret" is trivially satisfiable by a test that
asserts on an environment which never had a secret in it. The test has to set
`SADANA_PLUGIN__*` and `OPENROUTER_API_KEY` in the *parent* and then read what
the child actually saw, by running a program that prints its own environment.
Anything weaker passes while the exposure is wide open. This is the same shape
of hole the cold review found in `IMAGE-GEN-01`, where the assertion searched
one field of a result instead of all of them.

**Tests here run real processes, which is new for this suite.** `sys.executable
-c` only: present by construction, deterministic, no network, and no reliance
on what is installed on the machine. A test that shells out to `echo` or
`sleep` would be testing the developer's PATH.

**Where this could drift back into something `spec.md` rejected.** Three
places.

`setrlimit` will look like an obvious omission to anyone reading the code
without the spec — it is declined for a specific reason (`preexec_fn` is
unsafe in a thread and every `call` body runs in one), not for taste.

`capture_output=True` is one keyword and obviously simpler than temporary
files. It is the unbounded read the last work item's review caught in
`run_http`.

A `cwd` confinement check will look responsible. It implies a boundary this
does not have, and `spec.md` is explicit that the real containment is
`run_graph`'s check on a returned artifact.

**What has no mitigation.** The primitive says it is not a boundary and
nothing stops it being used as one — `spec.md` § Concerns names this and the
only defence is that it is written down. And declining `setrlimit` leaves the
runaway-allocation case genuinely uncovered.

## Proof

`tests/unit/test_execution.py` covers, by running real `sys.executable -c`
programs:

A program that succeeds returning `Ran` with exit code 0 and its stdout; one
that exits 3 returning `Ran` with that code, not a `Failure` — the program ran
and said no, which is a different thing from not running.

The environment, asserted the only way that means anything: the parent sets
`SADANA_PLUGIN__WEB_SEARCH__API_KEY`, `OPENROUTER_API_KEY` and an arbitrary
`SOMETHING_ELSE`, the child prints `os.environ` as JSON, and the test asserts
none of the three is in it while `PATH` and `HOME` are. Then that
`request.env` reaches the child, and that a value in `request.env` beats the
same name in the allowlist.

Nothing is interpreted: `argv` carrying `a; rm -rf /` as one element comes back
printed as one argument, semicolon and all.

Each failure returning rather than raising: an empty `argv`, a program that
does not exist, a `cwd` that does not exist — three `Failure`s, three
different sentences.

The timeout: a program that sleeps longer than a one-second limit returns
`Failure` naming the timeout, the call returns in about that second rather
than the sleep, and the process is gone afterwards.

The output cap: with the cap set small, a program printing more returns `Ran`
with `truncated` true and exactly the cap's worth of output; one printing
exactly the cap returns `truncated` false — which is what the read-one-past
trick buys and is the boundary most likely to be off by one.

Both config keys honoured, by setting each and observing the behaviour change.

Every existing `run_http` test in that file passes with no edit.

`make verify` ends `VERIFY OK`, pasted in full.


## Amended during implementation

**No file outside the two named was touched**, which is the one thing this
plan got exactly right — everything below is about what the artifacts *said*,
not about where the code went.

**The self-check corrected three claims this plan and `spec.md` made.**

Requirement 8 said "one local way of running a program". False as written:
`plugin_install._git` and `gateway_service._run_systemctl` both predate this
function. Narrowed to "one way for a *plugin* to run a program", because the
original told a later reader the audit was finished. The finding underneath it
— that `_git` inherits the whole parent environment, which is verbatim the
exposure `intent.md` exists to name — is recorded in `spec.md` § Concerns and
owed as its own work item rather than folded in here.

`spec.md` justified keeping `execution.py` whole "at roughly 140 lines". That
was the size *before* this change doubled it; the module is 286 lines. The
decision stands on its stated reason — a third *kind* of outward reach, not a
line count — but the number it was first defended with was measured before the
thing being defended.

The module docstring still described one HTTP case and asserted that no
untrusted source existed yet. A marketplace shipped two commits ago. Rewritten,
and that matters more than usual here: if two outward capabilities are going to
share a file, the header saying which half you are in *is* the mitigation.

**Two behaviours changed from what this plan described.** The environment
allowlist no longer inherits `LANG`/`LC_ALL` — it sets them to `C.UTF-8`,
because `_read_capped` decodes as UTF-8 and an inherited `LC_ALL=C` produces
exactly the mangled text the allowlist is there to prevent.
`scripts/run_tests.sh` had already solved this the same way for its own
children. And `_safe`'s 200-character cap — sized for a stranger's HTTP
response body — was being applied to sentences this project wrote, with the
caller-controlled `argv[0]` at the front, so a long program name truncated away
the informative half of a timeout message.


**Two of the item's own claims were false and were found by the cold review,
not by the suite.**

The timeout killed one process. `subprocess.run` kills the pid it spawned;
anything that program spawned is reparented and keeps running — measured, a
two-second limit on a program that starts a twenty-five-second sleeper returned
in two seconds and left the sleeper alive, still holding the capture file. The
docstring's "nothing runs forever" was false for any program that spawns, which
is most of what this exists for. Fixed with `start_new_session` and a group
kill, and the regression test earns its place: removing the flag to check what
the test would catch killed the entire test run, because without it the child
shares this process's group and `killpg` takes down the caller. The two can
never be separated and the code now says so.

Nothing bounded what a program *wrote*. Only the read-back was capped, so a
print loop put 1.12 GB into the temp directory in two seconds and at the
default limit would have written tens of gigabytes and thrown them away. This
plan and `spec.md` both described the cap as covering that, and it did not.
