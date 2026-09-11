# Spec: Running a program, and saying what that costs

Intent: docs/tasks/SUBPROCESS-01-running-a-program-and-saying-what-that-costs/intent.md

## Requirements

1. **`execution.run_program(request)` runs one named program and reports what
   happened** — exit code, what it printed, whether output was cut short.
2. **The program and its arguments are a sequence from the start.** No shell,
   ever; nothing assembles a command line from text. *(intent Constraints.)*
3. **The child's environment is built, never inherited.** A fixed allowlist of
   variables plus whatever the caller passes explicitly. Every `SADANA_*`
   value — which now includes two plugins' API keys — is absent by
   construction. *(intent Constraints; the exposure the obvious
   implementation creates for free.)*
4. **Time is bounded**, from a config key, and a program that exceeds it is
   killed — *with everything it spawned*, which is a different and harder
   claim than killing the program — and reported as such.
5. **Output is bounded**, from a config key, in both directions: what is read
   back is capped and truncation reported, and what a program may *write* has
   a ceiling above that, past which it is stopped. *(The second was added at
   Deploy; without it a print loop put 1.12 GB into the temp directory in two
   seconds and nothing noticed.)*
6. **Failures are returned, never raised** — a missing program, one that is
   not executable, a timeout, a working directory that does not exist.
7. **This is not a security boundary, and the code says so** in its own
   docstring, in the words the intent uses.
8. **One local way for a *plugin* to run a program.** No backend seam, no
   remote runner, nothing anticipating one. *(capability blueprint §6
   Rule 1.)*

   *(Corrected at Deploy. This originally said "one local way of running a
   program", which is false: `plugin_install._git` and
   `gateway_service._run_systemctl` both predate it. The wording mattered
   because it told a later reader the audit was done — see Concerns.)*
9. **No new dependency.**

## Design

Two dataclasses and one function in `execution.py`, plus the honest paragraph
that is the actual deliverable.

### Where it lives, and the deliberate look this triggers

`src/sadana/execution.py`. `WEB-SEARCH-01` added redirect refusal, and
`IMAGE-GEN-01` added a per-request timeout and then a reply ceiling; that
item's Concerns said **a fourth widening should prompt a deliberate look at
this module rather than a fifth**. This is the fourth, so here is the look.

Splitting was considered: `run_http` and `run_program` share nothing but the
`Failure` type and the "never raises" contract. Against that — the module is
one block's job (reach outside this process), both halves are consumed by
plugin bodies through the same `Outcome`-shaped discipline, and the result of
splitting is two files that both mean "reaching outside", with a rule nobody
can state about which goes where. Kept together. **The trigger for revisiting
is a third kind of outward reach, not more lines.**

*(Corrected at Deploy: this originally said "kept together at roughly 140
lines", and Concerns said 140 was defensible where 300 would not be. 140 was
the size of the module *before* this change doubled it — it is 286 now. The
decision stands on the stated reason; the number it was justified with was
measured before the thing being justified.)*

### The posture, stated once, plainly

The intent left this open and it is decided here: **a bounded local
subprocess, running as the person running this project, which is not a
sandbox.**

What it does: no shell, so nothing is interpreted; a built environment, so no
secret is inherited; a time limit, so nothing runs forever; an output limit,
so nothing floods memory or the transcript. Those stop an accident — a program
that hangs, one that prints a gigabyte, one that would have been handed a key
it had no business seeing.

What it does not do: contain a program that means harm. A program run this way
can read and write anything the person can. The docstring says that in those
words.

**The consequence is the point.** Because this primitive claims nothing about
running untrusted software, the decision to run software this project did not
write stays a separate one somebody has to make deliberately. `browser-use`
(capability blueprint §5.5 #8) is still blocked on that decision and does not
inherit an answer from the existence of this function.

### Resource limits are deliberately absent, and the reason is specific

The obvious hardening is `setrlimit` for CPU, address space and file size, via
`subprocess`'s `preexec_fn`. It is not used, and not for the usual "declined
as over-engineering" reason.

`preexec_fn` is documented as unsafe in the presence of threads: the child can
deadlock before `exec`. Every `call` node body in this project runs through
`asyncio.to_thread` (`plugin_manifest.run_graph`), so **this code always runs
in a thread**, which is exactly the condition the warning names. A hardening
that can hang the thing it hardens is worse than its absence.

The time and output limits are thread-safe and cover the accidental cases
`setrlimit` would. If real limits are ever wanted, they belong with real
isolation, which is the deferred decision above — not bolted on here.

### Output goes to temporary files, not pipes

`subprocess.run(capture_output=True)` reads without a ceiling, which is the
exact defect `IMAGE-GEN-01`'s review found in `run_http` a commit ago — and
draining two pipes by hand to bound them invites the deadlock every
`subprocess` caution is about.

Instead each stream is redirected to a `tempfile.TemporaryFile`, and at most
`cap + 1` bytes are read back from each afterwards. No pipe, so no deadlock;
a bound applied at the read, so a program printing a gigabyte costs disk it
was going to cost anyway and never becomes a gigabyte-long `str`. Reading one
byte past the cap is what distinguishes "exactly at the limit" from
"truncated" without a second stat.

### The environment, and why an allowlist

`PATH`, `HOME` and `TZ` are inherited; `LANG` and `LC_ALL` are *set* to
`C.UTF-8` rather than inherited, because `_read_capped` decodes as UTF-8 and an
inherited `LC_ALL=C` gives a child whose output this reader then mangles by
replacement — `scripts/run_tests.sh` had already solved the same problem the
same way for its own children. `request.env` is merged last and wins over both.

That is enough for an ordinary program. It is deliberately *not* enough for one
installed somewhere unusual: `TMPDIR`, `LD_LIBRARY_PATH`, `XDG_RUNTIME_DIR` and
`DISPLAY` are all absent, and a caller that needs one passes it explicitly.
`TERM` is absent too, which is a quiet win — a child that cannot tell it is on
a terminal emits far fewer escape sequences for anything downstream to strip.

A denylist was rejected on the usual grounds and one specific one: the set to
exclude grows every time a plugin declares a setting. `PLUGIN-CONFIG-01`
namespaced every plugin's secret as `SADANA_PLUGIN__*` precisely so they are
recognisable — and a denylist would mean this function has to keep knowing
that, forever, correctly. An allowlist knows nothing about secrets and cannot
fall behind them.

### Interface shape

A second result type rather than reusing `Success`: an HTTP reply has a status
and a body, a program has an exit code, two streams and a truncation flag, and
one dataclass covering both would be a union pretending to be a record.
`Failure` *is* shared — a thing that did not happen has the same shape either
way, and it already carries the defang-and-bound treatment
`IMAGE-GEN-01` gave it.

### Working directory

`cwd` is caller-supplied and **not** confined to anywhere. The intent left
this open; confining it here would imply a boundary this does not have, and a
caller that can choose the program can choose what it does regardless. A
plugin producing files passes `artifact_store.output_dir()`; `run_graph`'s
existing check still refuses an artifact claiming a path outside the run's
directory, which is where the real containment lives.

`execution.py` does not import `artifact_store` — the caller passes a path.

### Policies applied

- **`testing-conventions`** — every test runs a real program, but only
  `sys.executable` with `-c`, which is present by construction, deterministic,
  and not the network. No fixture fakes a process.
- **CLAUDE.md, "say exactly what each async operation survives"** — the
  docstring states the posture.
- **CLAUDE.md, "a registry… earns its cost only once a second real member
  exists"** — one local runner, no seam.
- **CLAUDE.md, "a caller-supplied string passed to a CLI tool as a bare
  positional is a command-injection vector"** — argv is a sequence and there
  is no shell, so the `--` rule's failure mode cannot arise here; it remains
  the *caller's* obligation when the program itself takes option-shaped
  arguments, and the docstring says so.
- **CLAUDE.md, "use config for behaviours"** — both limits are config keys.

### State inventory

| state | stored or derived | why |
| ----- | ----------------- | --- |
| the child's environment | derived per call | built from an allowlist plus the caller's own values; nothing is remembered between calls |
| the limits | derived, read per call | config keys, resolved fresh like every other in this project |
| captured output | a temporary file, deleted on close | the alternative is an unbounded string |

Nothing persists. No process outlives the call — `start_new_session` puts the
child in its own group and a timeout kills the group, so what it spawned goes
too. *(Corrected at Deploy: this originally relied on `subprocess.run`'s own
timeout, which kills one pid and leaves grandchildren reparented and running.)*

## Interface

```python
@dataclass(frozen=True)
class ProgramRequest:
    argv: tuple[str, ...]              # program first; never a string
    cwd: Path | None = None
    env: Mapping[str, str] = ...       # merged over the allowlist
    timeout_s: int | None = None       # None -> the config default

@dataclass(frozen=True)
class Ran:
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool

ProgramOutcome = Ran | Failure
```

- `run_program(request: ProgramRequest) -> ProgramOutcome`. Never raises.
- A non-zero exit is a `Ran`, not a `Failure`: the program ran and said no.
  `Failure` is for a program that could not be run at all, or was stopped.
- Config: `SADANA_EXECUTION_PROGRAM_TIMEOUT_S` (default 120),
  `SADANA_EXECUTION_PROGRAM_OUTPUT_BYTES` (default 1 MiB per stream).

## Acceptance criteria

- [ ] A program that succeeds returns `Ran` with its exit code, its stdout and
      its stderr.
- [ ] A program that exits non-zero returns `Ran`, not `Failure`, and carries
      the exit code.
- [ ] `argv=()` and a program that does not exist both return `Failure`
      naming the problem, not a raised exception.
- [ ] A directory that does not exist as `cwd` returns `Failure`.
- [ ] Nothing is interpreted: `argv=("echo", "a; rm -rf /")` passes the
      semicolon through as one literal argument.
- [ ] The child's environment contains none of `SADANA_PLUGIN__*`,
      `OPENROUTER_API_KEY`, or any other inherited variable — asserted by
      running a program that prints its own environment, with such variables
      set in the parent.
- [ ] `request.env` reaches the child, and wins over the allowlist.
- [ ] A program that outlives the timeout returns `Failure` saying so, and no
      process is left behind — including one the program itself spawned.
- [ ] A program that writes without end is stopped rather than filling the
      disk with output nobody will read.
- [ ] A program printing more than the cap returns `Ran` with `truncated`
      true and output at the cap; one printing exactly the cap returns
      `truncated` false.
- [ ] Both limits follow their config keys.
- [ ] `run_http` behaves exactly as before — its existing tests pass unedited.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Any isolation, containment or sandboxing. Stated, not implied.
- A remote or containerised runner, or a seam for one.
- Streaming output, or watching a program as it runs.
- A program that outlives the call. Both are `intent.md`'s narrowed scope and
  both need somewhere to keep a running thing between calls.
- Feeding a program anything on standard input. No caller needs it; the moment
  one does it is a field and a test.
- Deciding whether to run software this project did not write.

## Open questions

**Should `PATH` be the person's, or a fixed one?** Inheriting `PATH` means a
plugin asking for `ffmpeg` gets whatever is first on it, which is how a person
expects their machine to work and also how a shadowed binary wins. A fixed
`PATH` is more predictable and breaks every plugin on a machine that puts
tools somewhere unusual. Inheriting, for now, because the alternative fails
confusingly on exactly this project's WSL setup — but it is the one part of
the environment allowlist that is a real choice rather than an obvious one.

## Rejected alternatives

**`setrlimit` via `preexec_fn`.** The obvious hardening, and unsafe in a
thread — argued in Design. This is the finding of the item. Note the contrast
with `start_new_session`, which looks like the same class of thing and is not:
subprocess implements it inside the child it forks rather than through
`preexec_fn`, so it carries none of the threading hazard.

**`subprocess.run(capture_output=True)`.** Unbounded read, which is the defect
found in `run_http` one commit ago. Temporary files instead.

**Bounding pipes by hand.** `Popen` plus bounded reads on both streams is the
textbook way to deadlock on a full pipe buffer.

**A denylist of secret-looking variables instead of an allowlist.** It has to
keep knowing what a secret looks like, forever, and `PLUGIN-CONFIG-01` adds a
new shape of secret every time somebody declares a setting.

**Reusing `Success` for a program's result.** An HTTP reply and a process
result share no field; one dataclass over both is a union pretending to be a
record.

**A `TerminalEnvironment` seam with `local` as the first backend.** What the
reference does — `agent/terminal_env_provider.py` defines an abstract provider
and registers seven (local, docker, singularity, modal, daytona,
vercel_sandbox, ssh). It is the single likeliest failure the capability
blueprint names (§9 Risk 1, "the registry reflex"), and the one the
blueprint's §6 Rule 1 refuses by name. One member, called directly.

**Confining `cwd` to a known root.** Implies a boundary this does not have.

## Concerns

**A primitive that says it is not a boundary will be used as one anyway.**
That is the honest risk of shipping this. Somebody — possibly me, later in
this same plan — will reach for `run_program` to run browser-use and reason
that the limits make it "safe enough". The only defence built here is that the
docstring says otherwise in plain words and this spec says it twice. That is
weaker than a mechanism and it is what is available.

**Declining `setrlimit` is right and leaves a real gap.** A program that
allocates until the machine swaps is not stopped by a timeout in any useful
sense. The write ceiling added at Deploy closes the disk half of that; memory
is still uncovered. The reasoning — that `preexec_fn` can deadlock in a threaded caller, and
this caller is always threaded — is sound, and the outcome is still that the
accidental case this most wants to catch is the one least covered. If that
bites, the fix is `posix_spawn`-based limits or real isolation, not
`preexec_fn`.

**`PATH` inheritance is the soft spot in requirement 3.** Everything else
about the environment is built; `PATH` is taken from the parent, so what
`ffmpeg` resolves to is whatever the person's shell would have run. That is
usually what they want and is also the one lever an attacker with write access
to a directory on `PATH` already has. Named in Open questions rather than
silently accepted.

**The deliberate look at `execution.py` concluded "keep it together", and I am
about 70% confident.** The stated trigger is a third *kind* of outward reach
rather than a line count, which is a judgement a later reader may reasonably
overrule — and the line count it was first justified against was wrong, which
is not an encouraging sign for the judgement.

**The safe primitive was built beside the unsafe call site, not under it.**
`plugin_install._git` does `env = dict(os.environ)` and hands the whole parent
environment to `git`, fetching from a `repo_url` a marketplace submitter chose.
That is verbatim what `intent.md` names as the reason this work item exists.
It is not the live exfiltration it first appears, and the reason needs stating
carefully because my first version of this paragraph overstated it:

- **Load-bearing and true:** `git` does not transmit its environment to a
  remote. There is no path by which fetching a hostile repository mails the
  keys anywhere.
- **Overstated, and corrected:** "this git refuses the `ext::` transport". It
  refuses it *by default* — `protocol.ext.allow` is a config key, and
  `git -c protocol.ext.allow=always ls-remote -- "ext::sh -c …"` runs the
  command. Verified both ways. `_git` reads the person's own gitconfig and
  pins no protocol policy, so the refusal belongs to their configuration
  rather than to this code.

Which makes the owed work item larger than "convert `_git` to `run_program`".
Nothing validates `repo_url`'s scheme anywhere — not at `register`, not at
`submit`, not at `install` — so the follow-up needs to pin `protocol.allow`
or check the scheme as well as scrub the environment. Recording that here
because "a net deletion of about eight lines" was the wrong size for it. `gateway_service._run_systemctl` is correctly *not* a candidate: it
streams to the terminal rather than capturing, which `run_program` cannot do.
