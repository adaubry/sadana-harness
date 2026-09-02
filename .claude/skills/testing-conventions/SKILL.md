---
name: testing-conventions
description: How tests are written in sadana-harness. Use whenever writing, modifying, reviewing or reasoning about a test, choosing where a test file goes, deciding what to assert, or judging whether an existing test earns its place.
---

# Testing conventions

## Where tests live

The tests live in sadana-harness's `tests/` directory. Each subdirectory is a test _tier_ (unit, integration, eval, contract).

## Naming

The tests are named after the module they test, with a `test_` prefix. For example, `foo.py` has its tests in `tests/unit/test_foo.py`. If a module is large enough to have multiple test files, use a suffix: `test_foo_bar.py`, `test_foo_baz.py`.

## What a unit test may touch

A unit test only touches the module under test and its dependencies. It may not touch the network, the wall clock, or the real filesystem outside a tmp directory. It may not call the model API.

## Fixtures —

Fixtures are used to set up and tear down test state. Use `pytest` fixtures for common setup code, and mark them as `autouse` if they should run for every test in a module or class.

---

## Tests must never write to the real state directory

Every test runs against a temporary home. An autouse fixture redirects the
state directory to a tmp path; no test hardcodes the real one.

This is the single highest-value fixture in the suite. Without it a failing
test can corrupt the developer's actual working state, and tests silently
start depending on whatever happens to be on the machine.

## Assert invariants, never snapshots

A test is a **change-detector** if it fails whenever data that is _expected to
change_ gets updated — model catalogues, config version literals, counts of
things. Change-detector tests add no behavioural coverage; they guarantee that
routine updates break CI.

Do not write:

```python
assert "claude-sonnet-4" in PROVIDER_MODELS["anthropic"]   # breaks every release
assert DEFAULT_CONFIG["_version"] == 3                     # breaks every bump
assert len(TOOLSETS) == 12                                 # breaks on every add
```

Do write:

```python
assert "anthropic" in PROVIDER_MODELS
assert len(PROVIDER_MODELS["anthropic"]) >= 1              # plumbing works
assert raw["_version"] == DEFAULT_CONFIG["_version"]       # migration reaches current
for m in PROVIDER_MODELS["anthropic"]:                     # relationship holds
    assert m in CONTEXT_LENGTHS
```

The rule: **if it reads like a snapshot of current data, delete it. If it reads
like a contract about how two pieces of data must relate, keep it.**

## Never read source code in a test

A test that reads a source file's text is testing the _shape of the source_,
not its behaviour. Banned outright. Any test that opens a `.py` file and
matches against its contents is a defect.

It fails in both directions: it passes when the implementation is subtly
broken (the pattern matches a call site that exists but is wired wrong), and
it fails when a correct refactor changes formatting with identical runtime
behaviour. It also blocks refactors and gives false confidence — a green
suite full of source-regex tests has never executed the path it claims to
guard.

If the logic you want to assert about lives inline in a large file and
extracting it feels disruptive, **that is the signal to do the extraction**,
not to regex around it. Pull it into a small pure function and call it.

## Never fake the environment to make a test pass

If a test needs the interpreter to believe it is somewhere it isn't in order
to pass, it belongs in that environment — as a marked, separately-run test.

Pure functions that take environment as _data_ are fine:
`resolve_path(spec, is_windows=True)` is input→output, not a fake. Patching a
module-level `IS_WINDOWS` flag and then calling something that reads it is a
fake.

For sadana specifically: this is WSL-only by constraint, so the temptation is
low — but the same rule covers faking the clock, faking a provider response
shape you have never observed, and faking a filesystem layout.

## One runner, and it enforces parity with CI

Never invoke the test framework directly. Go through the project runner, which
pins the environment: timezone, locale, hash seed, and a blanked credential
environment.

A developer machine with API keys set and a different locale diverges from CI
in ways that produce both "works locally, fails in CI" and the reverse. The
runner exists to make that class of bug impossible, and it only works if it is
the only door.

the runner is located in scripts/run_tests.sh

## A flaky test is a bug, not noise

If the suite retries, a pass-on-retry is reported, not swallowed. Timing
tests must not assume a quiet machine: loose wall-clock bounds, event-based
synchronisation, no assertions that depend on something _not_ happening within
a short window.

---

## Anti-patterns to reject in review

- Snapshot assertions over data that is expected to change.
- Any test that reads source text.
- Tests that write outside a tmp directory.
- Environment faking to avoid running somewhere.
- Direct framework invocation that bypasses the runner.
