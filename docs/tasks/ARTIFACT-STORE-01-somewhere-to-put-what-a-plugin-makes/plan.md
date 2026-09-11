# Plan: Somewhere to put what a plugin makes (from intent.md 2026-09-11)

## Files that change

    src/sadana/artifact_store.py   (new)   run_dir_name, run_dir, for_run,
                                           activate, output_dir, contains,
                                           contains_active, NoOutputDirectory
                                           (`for_run` and `contains_active`
                                           added during implementation — see
                                           the amendment below)
    src/sadana/plugin_manifest.py          run_graph's `output_dir` keyword, the
                                           activate() wrapper around the walk,
                                           and the containment check on a
                                           returned kind="file" Artifact
    src/sadana/conversation_store.py       turn_seq / seq_in_turn on
                                           plugin_pauses via the guarded ALTER
                                           already in this file; Pause gains the
                                           two fields; save_pause and
                                           save_pause_from_result carry them
    src/sadana/plugin_dispatch.py          dispatch() computes the directory
                                           from turn_key + seq_in_turn and
                                           passes it; resume_paused_run() passes
                                           the one the pause row remembers
    src/sadana/gateway_dispatch.py (added) its persist_pause closure gains the
                                           two arguments dispatch now passes —
                                           see the amendment below
    src/sadana/plugins.py                  one docstring line on Artifact.ref,
                                           which has described a thing that did
                                           not exist since G2
    tests/unit/test_artifact_store.py (new) naming, encoding, collision,
                                           lazy creation, activate nesting,
                                           containment, NoOutputDirectory
    tests/unit/test_plugin_manifest.py     output_dir reachable from a call body
                                           at the second node; the escaping ref
                                           failing its node; link artifacts
                                           unaffected; no directory when nothing
                                           asks
    tests/unit/test_conversation_store.py  the two columns round-tripping, and a
                                           pause row written without them still
                                           loading
    tests/unit/test_plugin_dispatch.py     a dispatched run gets a directory
                                           named from its own turn key

## Order of work

1. **`artifact_store.py`, alone and with no callers.** Every function, plus
   `NoOutputDirectory`. Nothing imports it yet, so the suite must be exactly as
   green as before this step. Tests: `tests/unit/test_artifact_store.py`.

2. **`run_graph`'s wiring, riskiest assumption first.** Before the containment
   check, before anything else in this step: a test that a `call` body — which
   `run_graph` runs through `asyncio.to_thread` — can read the contextvar that
   `activate()` set on the event loop. The whole design is void if it cannot.
   (Checked by hand before this plan was written and it does propagate; the
   test is what keeps it true.) Then the `output_dir` keyword, the `activate()`
   wrapper, and the containment check on the existing `isinstance(value,
   plugins.Artifact)` arm.

3. **`conversation_store`'s two columns.** `_MIGRATED_COLUMNS` and
   `_migrate_columns` already exist in this file for `conversations`; this adds
   the same shape for `plugin_pauses` rather than inventing a second one. No
   backfill: a legacy row reads back `None` for both and a run resumed from it
   gets no output directory, which is exactly its pre-fix behaviour.

4. **`plugin_dispatch`, both halves.** `dispatch()` has `turn_key` and the
   closure-local `seq_in_turn` already; `resume_paused_run()` reads the two new
   columns. Last of the four because it is the only step that needs all three
   before it.

5. **The `Artifact.ref` docstring**, now that the sentence is true.

6. **Self-check, then verify.** `/ponytail-review` and `/simplify` over the
   diff, act on what is worth taking now, then `make verify`.

## Risks

**What this could break, by name.**

`run_graph` has two callers in `src/` — `plugin_dispatch.dispatch()` and
`plugin_dispatch.resume_paused_run()` — plus a large number of tests that call
it directly. A defaulted `output_dir=None` keeps all of them compiling.

**This paragraph originally continued "and the containment check cannot fire
when there is nothing to be contained by", which the implementation
falsified** — see the amendment below. Behaviour is identical for every run
that does not emit a `kind="file"` artifact, which is every run that exists
today; one that does now fails, where before it was recorded.

`conversation_store.Pause` gains two fields, so every construction of it —
in `src/` and in the suite — must still work. They go last and default to
`None`, the same trailing-defaulted-field discipline `Manifest.settings` needed
in the previous work item, and for the same reason.

The `plugin_pauses` migration runs against a store that may already hold rows.
The guarded `PRAGMA table_info` check makes a second `open_store()` a no-op,
and the existing `_migrate_columns` proves the shape works here; the new risk
is only that `plugin_pauses` gets its own call rather than being folded into a
function whose docstring says `conversations`.

**Contextvar leakage between tests is the quiet one.** A test that calls
`activate()` without unwinding leaves the next test running inside a stale
directory, and because `output_dir()` would then *succeed* rather than raise,
the symptom is a passing test writing somewhere unexpected — not a failure.
Mitigated by making `activate()` a context manager with no non-contextual entry
point, so a caller has to work at leaking it.

**The most risky step is 2, and specifically its first half.** Everything else
in this plan is arithmetic on paths or a schema column; step 2 rests on
`asyncio.to_thread` propagating context into the thread that runs a `call`
body. That was verified by hand before this plan was written, which is why it
is second rather than first: step 1 has no dependency on it, so landing the
module first means a failure in step 2 can only be about the wiring.

**Where this could drift back into something `spec.md` rejected.** Two places.

Step 2 will make the reserved-key approach look attractive again, because
`dispatch()` is already merging `_sadana_session_key` into `arguments` two
lines from where the directory is computed, and adding a third key there is a
one-line change. `spec.md` § Rejected alternatives settles it: `arguments`
reaches the entry node only, and the artifact-emitting `call` is rarely the
entry node precisely because a node returns one value.

Step 1 will make a module-level global look sufficient, because every test runs
one run at a time and a global would pass all of them. It is wrong under the
gateway running two conversations concurrently, which no test in this suite
exercises — so the suite cannot catch this drift and review has to.

**What has no mitigation.** Nothing in this work item creates the situation the
concern section calls most likely to bite — a disk that only grows. The first
plugin that fills it is the next blueprint item.

## Proof

`tests/unit/test_artifact_store.py` covers: `run_dir_name` flattening a key
with a slash, a dot-dot and a leading dash into something path-safe;
`run_dir_name("a/b") != run_dir_name("a-b")`, which is the whole reason a
digest is appended; `run_dir` returning a path under the given state directory
and creating nothing; `output_dir()` creating on first call and being
idempotent on the second; `output_dir()` raising `NoOutputDirectory` outside
any `activate()` and inside `activate(None)`; `activate()` restoring the outer
value after a nested `activate()` exits, including when the body raises;
`contains()` answering false for a sibling directory, for an escaping
`../` path, and for a symlink inside the directory pointing out of it.

`tests/unit/test_plugin_manifest.py` covers: a `call` body at the *second*
node calling `output_dir()` and writing a file that is really there afterwards
— the case that proves both the depth requirement and the `asyncio.to_thread`
propagation; a run whose bodies never call it leaving `state_dir/artifacts`
non-existent; a `call` body returning `Artifact(kind="file",
ref="/etc/passwd")` failing that node with `failed_node` set, nothing raised,
and the real `/etc/passwd` still present; a `kind="link"` artifact with an
`https` ref passing through untouched; and a `run_graph` called with
`output_dir=None` refusing a `kind="file"` artifact — **this last sentence
originally promised the opposite** and is corrected with the amendment
below.

`tests/unit/test_conversation_store.py` covers: saving and loading a pause
round-tripping `turn_seq` and `seq_in_turn`; and a store whose `plugin_pauses`
table is built by hand *without* those columns, which is what every store
already on disk looks like — opening it runs the `ALTER`, the legacy row still
loads with both fields `None`, and a second open is a no-op. Writing the
pre-migration table by hand is the only way to reach that branch: `open_store`
on a fresh file creates the table from `_SCHEMA`, which already has both
columns, so a row that merely omits them proves the `NULL` read and nothing
about the migration. The first version of this test did exactly that and
claimed to be "the migration's whole safety claim"; the cold review caught it.

`tests/unit/test_plugin_dispatch.py` covers: a plugin dispatched through
`build_dispatch` receiving exactly `artifact_store.for_run(key, turn, seq)`,
asserted against the real encoding rather than against the shape of the path —
the first version asserted `tmp_path in directory.parents`, which the autouse
state-directory fixture makes true regardless, and `directory.parts[-3:-2] !=
()`, which is a tautology. It also covers the invariant those tests exist to
protect: that the `(turn_key, seq_in_turn)` a pause records is the pair its
directory was named from, proved by moving `seq_in_turn += 1` back above the
pause branch and watching it go red.

`make verify` ends `VERIFY OK`, pasted in full.


## Amended during implementation

**`src/sadana/gateway_dispatch.py` was touched and this plan did not name it.**
Step 4 has `dispatch()` tell the pause which run it belonged to, and the only
way out of `dispatch()` is the injected `persist_pause` callback, so its
signature had to widen from `(result)` to `(result, turn_seq, seq_in_turn)`.
That callback has exactly one real implementation, a closure in
`gateway_dispatch.handle_inbound()`, plus the module-level no-op default. Both
had to change. The plan traced the data as far as `conversation_store` and
stopped one call short of the caller that supplies it.

**`run_graph` was split into a wrapper and `_run_graph`, which the plan did not
anticipate.** The activation has to wrap the whole walk, and wrapping a
function body in a `with` means either re-indenting it entirely or delegating.
The first attempt passed the walk's locals as parameters and was wrong in a way
worth recording: `result()` and `failed()` close over `trace`, and the resume
branch *rebinds* `trace` rather than mutating it, so the closures would have
kept reporting the pre-resume list — a paused run's history would have silently
vanished from its own result. Caught before it ran by reading, not by a test.
The shape that landed moves every local into `_run_graph` so nothing is
captured across the boundary, and `run_graph` is the activation and nothing
else.


**The `output_dir=None` behaviour change was not recorded here when it was
made.** `spec.md`'s Interface section was corrected during Build to say that a
`kind="file"` artifact is refused when a run has no output directory; this
plan's § Risks and § Proof still asserted the opposite, and the cold review
found the contradiction. Both are corrected above, in place, with the original
wording quoted. The change itself is deliberate and is argued in `spec.md`:
`G2-artifacts` declined to validate file artifacts on the explicit grounds
that "the later file-writing item is free to add real handling when the
underlying concept exists", and this is that item.

**Two functions entered `artifact_store.py` that this plan did not name.**
`for_run()` resolves the live state directory and is what both `plugin_dispatch`
call sites actually use; `contains_active()` was added during the self-check,
when it turned out `output_dir` was travelling two channels at once — the
contextvar and an explicit parameter carrying the same value by construction.
Folding the predicate into the module that owns the ambient state let
`_run_graph` drop the parameter entirely, which is what leaves `run_graph` as
nothing but its signature, a docstring and the `with`.
