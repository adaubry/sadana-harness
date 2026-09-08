# Review: Asking for a service that isn't there fails the same way everything else does (from plan.md 2026-09-08)

Reviewed: HEAD..working tree (uncommitted) — 4 files, +35/-20 at review
time; the one Important finding below was then fixed in this same branch
(§ Findings) before a decision was requested — `make verify` re-run after,
same 380-passed total (the fix strengthened existing assertions, no test
count change).
Reviewer context: fresh session
Second opinion: none — ran during build (self-check per plan.md § Order of
work step 5: `/ponytail-review` + `/simplify`), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/MODEL-ACCESS-01-unwired-provider-outcome: all present artifacts valid
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
Success: no issues found in 16 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 18%]
........................................................................ [ 37%]
........................................................................ [ 56%]
........................................................................ [ 75%]
........................................................................ [ 94%]
....................                                                     [100%]
380 passed in 4.49s
TESTS OK
VERIFY OK
```

## Scope

`git diff --stat HEAD`:

```
 src/sadana/model_access.py          | 15 ++++++++++++++-
 src/sadana/subcommands/chat.py      | 11 +----------
 tests/unit/test_model_access.py     | 10 ++++++++++
 tests/unit/test_subcommands_chat.py | 19 ++++++++++---------
 4 files changed, 35 insertions(+), 20 deletions(-)
```

Matches plan.md § Files that change exactly — no file touched that the plan
didn't name, and no named file left untouched.

## Findings

Ran all three passes (Bugs, Security, Compliance) against the working-tree
diff for the four files above, and reconciled it against `plan.md` §
Proof, `spec.md` § Acceptance criteria, `spec.md` § Rejected alternatives,
the five design principles, and CLAUDE.md's two accumulated CLI-SHELL
rules. Bugs pass: one Important finding below, on the new failure's
message content, not on control flow — the `try/except`-vs-returned-value
question plan.md itself flagged as riskiest was traced directly against
the test that exercises it and holds. Security pass: clean — no secrets,
no new I/O, no widened process reach; the change is entirely
exception-classification inside an existing function. Detail below.

### Compliance pass — what was checked

- **Plan.md § Proof, item by item.**
  - "`test_model_access.py`'s new parametrized test covers `resolve()`
    returning `NeedsCredentialOrProviderChange` for both an unregistered
    and a registered-but-unwired provider, with no mocking" — discharged:
    `test_resolve_returns_needs_credential_for_a_bad_provider`
    (`tests/unit/test_model_access.py:480-487`), parametrized over
    `"definitely-not-a-real-provider"` and `"anthropic"`, calls `resolve()`
    directly with no `monkeypatch`.
  - "`test_subcommands_chat.py`'s replacement test covers `cmd_chat`
    failing cleanly (exit `1`) ... and confirms the conversation row is
    still created" — discharged:
    `test_cmd_chat_unknown_provider_fails_via_the_normal_turn_loop`
    (`tests/unit/test_subcommands_chat.py:90-101`) asserts `== 1` and calls
    `_load("bad-provider-test")`, which raises `ConversationNotFound` if the
    row weren't there.
  - "Every existing test named in § Risks passes unmodified" — confirmed:
    `test_send_raises_unknown_provider`,
    `test_send_raises_provider_not_wired_for_registered_but_unwired_provider`
    (`tests/unit/test_model_access.py:265-275`),
    `test_get_provider_raises_unknown_provider_for_unregistered_name`
    (`:161-163`), `test_resolve_retries_transparently_and_returns_final_response`,
    `test_resolve_returns_each_non_retry_outcome_unchanged`, and
    `test_resolve_is_cancellable_between_attempts` all appear in the diff's
    unified context only, never in a `-`/`+` line — byte-identical to
    `HEAD`. All ran in the 380-pass evidence above.
  - "`make verify` ends `VERIFY OK`" — see Evidence.

- **Spec.md § Acceptance criteria, one by one.**
  - resolve() on unregistered name → `NeedsCredentialOrProviderChange`:
    `test_resolve_returns_needs_credential_for_a_bad_provider[definitely-not-a-real-provider]`.
  - resolve() on registered-but-unwired (`"anthropic"`) →
    `NeedsCredentialOrProviderChange`: same test,
    `[anthropic]` case.
  - `send()`/`get_provider()` tests pass unmodified: confirmed above.
  - `resolve()`'s other existing tests pass unmodified: confirmed above.
  - End-to-end `take_turn`/`sadana chat` ends `PROVIDER_FAILED` with a real
    `.detail`, not a raised exception reaching the caller: satisfied by
    composition, not one new test — `resolve()` now returns
    `NeedsCredentialOrProviderChange` (new test above);
    `test_complete_raises_provider_failure_for_needs_credential`
    (`tests/unit/test_conversation.py:591-594`, untouched by this diff)
    already proves `complete()` turns that outcome into `ProviderFailure`;
    `conversation.py:837` (untouched) already turns `ProviderFailure` into
    `ExitReason.PROVIDER_FAILED`. Traced the chain rather than assuming it.
  - `cmd_chat`'s proactive check gone from source, exits `1` not `2`:
    confirmed — `git diff` shows the whole `try/except UnknownProvider`
    block and `model_access` import removed
    (`src/sadana/subcommands/chat.py`), and
    `test_cmd_chat_unknown_provider_fails_via_the_normal_turn_loop` asserts
    `== 1`.
  - `make verify` ends `VERIFY OK`: see Evidence.

- **Spec.md § Rejected alternatives — checked for drift.** Fix lands in
  `resolve()` (`src/sadana/model_access.py:294-297`), not `complete()` or
  `send()` — followed. Reuses `NeedsCredentialOrProviderChange`, no new
  outcome type introduced — followed. `cmd_chat`'s check is deleted
  outright, not kept alongside as a duplicate — followed, confirmed by
  `grep -n model_access src/sadana/subcommands/chat.py` returning nothing.

- **Five design principles.**
  1. *Learn from the reference first* — spec.md's own Design section
     checked hermes's `ProviderStreamError` pattern and explicitly declined
     it with reasoning (streaming-only, not a closed-outcome member); not
     ignored.
  2. *Reduce the number of bets* — a five-line `try/except` around one
     existing call site, cheaply reversible; no new type, no new public
     interface.
  3. *More plugins, not more core* — n/a; this is MODEL-ACCESS's own
     internal retry/dispatch function, not plugin-shaped behavior.
  4. *Catch the scenario at the least step-cost* — placed at the one layer
     every caller already funnels through (`resolve()`), not duplicated
     into `conversation.complete()` or the eval harness; the common
     (working-request) path through the loop is unchanged.
  5. *Minimise mutable state* — no new state introduced.

- **CLAUDE.md's two accumulated CLI-SHELL rules.**
  - Exit-code convention ("a handler returns an int for its own exit
    code... argparse's own exits are never wrapped or re-raised"):
    `cmd_chat` still returns a plain `int` (now `1` via the normal
    `_chat_loop` path instead of a hand-rolled `2`); `--version`/`--help`/
    parse-error paths are untouched by this diff.
  - `bind_persist()` rebuilt fresh per turn: untouched by this diff —
    `conversation_store.bind_persist(conn, conversation, now=now)`
    (`src/sadana/subcommands/chat.py:80`) remains inside `_chat_loop`'s
    `while True:` body, one call per turn, same as before.

- **Traced the riskiest step named in plan.md § Risks directly**, rather
  than trusting the plan's own reasoning: `test_resolve_returns_each_non_retry_outcome_unchanged`
  with `final_outcome=NeedsCredentialOrProviderChange("no key")`
  (`tests/unit/test_model_access.py:462-469`) monkeypatches `send` to a
  lambda that *returns* that outcome, never raises it. The new
  `try/except (UnknownProvider, ProviderNotWired)` in `resolve()` only
  intercepts a raised exception; a returned value is untouched by a
  `try/except` regardless of its type. Confirmed by reading `resolve()`'s
  body (`src/sadana/model_access.py:293-300`): the `except` clause returns
  before the `isinstance(outcome, Retry)` check would even see a raised
  case, and the returned-outcome path never enters the `except` branch at
  all. This test is part of the 380 passing in the evidence above.

### Important

- **[Compliance/Bugs] The new failure's `.detail` is a bare provider name
  with no explanation of what's wrong, falling short of intent.md's own
  "a clear, specific reason" and reading as materially less informative
  than both the check it replaces and its own sibling outcome.**
  `UnknownProvider` and `ProviderNotWired` (`src/sadana/model_access.py:97,101`)
  are raised as `raise UnknownProvider(name) from None` (`:240`) and `raise
  ProviderNotWired(request.provider)` (`:255`) — bare `Exception`
  subclasses with no message formatting. `str(exc)` on either therefore
  returns *only* the provider name itself: verified directly,
  `str(UnknownProvider("not-a-real-provider"))` is
  `'not-a-real-provider'`, and `str(ProviderNotWired("anthropic"))` is
  `'anthropic'` — not "unknown provider" or "not wired," just the name
  echoed back. Because `resolve()`'s new `except` clause does
  `NeedsCredentialOrProviderChange(str(exc))`
  (`src/sadana/model_access.py:296-297`), and `_chat_loop` prints
  `f"[{result.exit_reason.value}] {result.detail or ''}"`
  (`src/sadana/subcommands/chat.py:99`), the actual user-facing output for
  `sadana chat --provider not-a-real-provider` becomes
  `[provider_failed] not-a-real-provider` — no word saying it's unknown,
  unwired, or a provider problem at all. Compare: the deleted `cmd_chat`
  check printed `sadana chat: unknown provider 'not-a-real-provider'`, and
  the sibling outcome this fix deliberately reuses —
  `send()`'s own missing-env-var case (`model_access.py:257-260`) —
  constructs a full sentence, `"missing env var(s): X"`. spec.md's
  Interface section states "`detail` carries the original exception's own
  message — no new wording invented" as a deliberate choice, but that
  choice appears to have been made without checking what these two
  exceptions' messages actually contain; the result satisfies "never a
  raw crash" but not the "clear, specific reason" intent.md's own
  Proposed outcome asks for. A one-line fix (e.g.
  `NeedsCredentialOrProviderChange(f"unknown provider: {exc}")` /
  `f"provider not wired: {exc}"`, distinguished per except-arm) would
  close this without touching `send()`/`get_provider()` or their tests.
  **Outcome: fixed**, exactly as suggested — `resolve()` now has two
  separate `except` arms (`UnknownProvider`, `ProviderNotWired`), each
  prefixing its own label before the original message. `sadana chat
  --provider not-a-real-provider` now prints
  `[provider_failed] unknown provider: not-a-real-provider`. The
  parametrized test in `tests/unit/test_model_access.py` was
  strengthened to assert `outcome.detail.startswith(expected_prefix)`
  per case, not just the outcome type, so this can't silently regress.
  `spec.md`'s Interface section corrected to describe the actual
  wording and why, instead of the "no new wording invented" claim this
  finding disproved.

## Decision

Approved by Adam Aubry, 2026-09-08, with the Important finding fixed in
this branch (see § Findings for what changed) before approval.
