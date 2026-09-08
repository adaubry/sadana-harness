# Spec: Asking for a service that isn't there fails the same way everything else does

Intent: docs/tasks/MODEL-ACCESS-01-unwired-provider-outcome/intent.md

## Requirements

1. Asking for a provider that doesn't exist, or one that exists but
   isn't wired, produces the same kind of clean, named failure every
   other model-access failure already produces — never a raw crash.
   (Intent §Proposed outcome.)
2. Every current and future caller gets this for free; none has to
   write its own check. (Intent §Affected users and systems.)
3. `send()`'s own already-tested behavior (it raises `UnknownProvider`/
   `ProviderNotWired` directly) is untouched. (Intent §Constraints.)
4. Nothing here chooses between providers or falls back automatically.
   (Intent §Constraints.)
5. `sadana chat`'s own hand-written check for this is removed once the
   real fix is in place. (Intent §Constraints.)

## Design

**Reference corpus, checked and declined.** hermes's own provider-error
handling (`agent/chat_completion_helpers.py`'s `ProviderStreamError`,
raised during streaming, never a member of a closed outcome set) has no
analogous pattern to adopt here — `model_access.py`'s own closed,
named-outcome contract is already sadana's own design, explicitly
narrower than hermes's ~15-field `ProviderProfile` (its own module
docstring says so), not derived from how hermes handles this specific
failure. Nothing to adopt; this fix stays entirely within the contract
C1 already built.

**Where this actually goes, and why not the two other candidates.**
Three functions sit between a bad provider name and a caller:
`get_provider()` (raises `UnknownProvider`), `send()` (calls
`get_provider()`, and raises `ProviderNotWired` itself for a
registered-but-unwired one), and `resolve()` (loops `send()` for
retries, the function every real caller — `conversation.complete()`,
and through it `run_turn`/`take_turn` — actually goes through).

`get_provider()` and `send()` are ruled out by their own existing
tests: `test_get_provider_raises_unknown_provider_for_unregistered_name`,
`test_send_raises_unknown_provider`, and
`test_send_raises_provider_not_wired_for_registered_but_unwired_provider`
(`tests/unit/test_model_access.py:161-163,266-275`) already pin exactly
today's raise-based behavior at both layers. Changing either breaks an
already-closed work item's own tests — precisely what intent.md's
constraint about `send()` rules out, and by the same reasoning,
`get_provider()` too.

`resolve()` (`model_access.py:267-282`) has no such test. It already
wraps every `send()` attempt in `asyncio.to_thread` inside a retry loop,
and — checked directly — `asyncio.to_thread` re-raises whatever the
wrapped call raised in the awaiting coroutine, so today `resolve()`
already lets `UnknownProvider`/`ProviderNotWired` escape uncaught; that
escape is simply never exercised by a test. Adding a `try/except`
around the one `send()` call inside `resolve()`'s loop, catching exactly
those two names, is the whole fix:

```python
try:
    outcome = await asyncio.to_thread(send, replace(request, attempt=attempt))
except (UnknownProvider, ProviderNotWired) as exc:
    return NeedsCredentialOrProviderChange(str(exc))
```

**Reusing the outcome already closest in shape, not inventing one.**
`send()` already returns `NeedsCredentialOrProviderChange` for the one
closely analogous case in its own body — a registered provider missing
required env vars (`model_access.py:257-260`, "the caller shouldn't
have to make a doomed request to learn a credential is simply absent").
An unregistered name and a registered-but-unwired one are the same
shape of problem — "this request cannot proceed because of what
provider was asked for" — so they become the same outcome, at the one
layer already responsible for turning `send()`'s attempts into a
caller-facing result. Because `resolve()`'s own return type annotation
already includes `NeedsCredentialOrProviderChange` in its union
(`model_access.py:267-268`), this is not even a signature change —
`conversation.complete()` (`conversation.py:531-565`) already turns
that exact outcome into `ProviderFailure`, which `run_turn` already
turns into `ExitReason.PROVIDER_FAILED`. **No change is needed anywhere
above `resolve()`.**

**`sadana chat` gives its own check up.** `cmd_chat`'s proactive
`try: model_access.get_provider(provider) except UnknownProvider` /
`manifest.request_fn is None` block (`chat.py:98-105` as shipped) is
deleted outright. A bad provider now surfaces the same way any other
first-turn failure already does: `_chat_loop` receives a `TurnResult`
with `exit_reason == PROVIDER_FAILED`, prints the detail, and returns
`1` — not the proactive check's `2`. This is a real, intentional
observable change to already-shipped CLI-SHELL-04 behavior, made
because intent.md's own constraint asked for it ("one place decides
this, not two"), not an accident.

**A consequence, not a new problem.** Removing the proactive check
means a `sadana chat` invocation with a bad provider and no `--resume`
now creates its conversation row before failing on the first turn,
where the old check failed before opening the store at all. This is
exactly how `sadana chat` already behaves for *every other* first-turn
failure (a real network outage, an exhausted budget) — the proactive
check was a special case for exactly one failure reason among many;
removing it makes this reason consistent with the rest, not worse than
them.

## Interface

```python
# src/sadana/model_access.py — resolve()'s body gains a try/except;
# its signature, and every type in its existing return annotation, is
# unchanged.
```

- `resolve(request)`: for a request naming an unregistered or unwired
  provider, returns `NeedsCredentialOrProviderChange` instead of
  letting `UnknownProvider`/`ProviderNotWired` escape. `detail` is
  `f"unknown provider: {exc}"` or `f"provider not wired: {exc}"`
  respectively — a deploy-stage cold review caught that both
  exceptions carry only the bare provider name as their own message
  (`str(UnknownProvider("x")) == "x"`, no word saying what's wrong), so
  reusing `str(exc)` alone, unprefixed, would have produced a
  `.detail` that reads as an unlabeled name rather than a reason —
  contradicting requirement 1's "clear, specific reason." Fixed before
  approval; see `review.md`.
- `send(request)` and `get_provider(name)`: unchanged. Both still raise
  exactly as their own existing tests already require.
- `src/sadana/subcommands/chat.py`'s `cmd_chat`: loses its proactive
  provider-validation block. No new public interface.

## Acceptance criteria

- [ ] `resolve()` called with an unregistered provider name returns
      `NeedsCredentialOrProviderChange`, not a raised exception.
- [ ] `resolve()` called with a registered-but-unwired provider
      (`"anthropic"`, matching the existing `send()` test's own setup)
      returns `NeedsCredentialOrProviderChange`, not a raised exception.
- [ ] `send()`'s and `get_provider()`'s own existing tests
      (`test_send_raises_unknown_provider`,
      `test_send_raises_provider_not_wired_for_registered_but_unwired_provider`,
      `test_get_provider_raises_unknown_provider_for_unregistered_name`)
      pass unmodified.
- [ ] `resolve()`'s other existing tests (retry-transparency,
      pass-through of every other outcome, cancellability) pass
      unmodified.
- [ ] An end-to-end `take_turn`/`sadana chat` call with a bad provider
      now ends with `ExitReason.PROVIDER_FAILED` and a real `.detail`,
      not a raised exception reaching the caller.
- [ ] `cmd_chat`'s proactive provider check no longer exists in the
      source; `sadana chat` with a bad provider now exits `1` (via the
      normal turn-failure path), not `2`.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Multi-provider fallback, resolution, or selection logic — declined
  by intent.md's own constraint, and by `cli_shell_blueprint.md`'s
  earlier ruling that this stays MODEL-ACCESS's job to *not* build yet.
- Changing `send()`'s or `get_provider()`'s own behavior or tests.
- Any change to how a *working* provider/model request is sent,
  retried, or classified.
- Avoiding the "conversation row created before the first-turn failure"
  consequence of removing `cmd_chat`'s proactive check — accepted
  explicitly (§Design), not solved here.

## Rejected alternatives

- **Translating in `conversation.complete()` instead of
  `model_access.resolve()`** — the deploy review's own first suggestion.
  Workable, but `resolve()` is the more precise layer: it is
  MODEL-ACCESS's own function, already responsible for turning a raw
  `send()` attempt into one of MODEL-ACCESS's own named outcomes, and
  already declares the return type this fix needs in its existing
  signature. Fixing it in `complete()` would mean CONVERSATION reaching
  past `resolve()`'s own contract to catch a MODEL-ACCESS-internal
  exception type directly — the "provider-specific... knowledge lives
  in MODEL-ACCESS, never in CONTEXT or CONVERSATION" rule, applied one
  layer more precisely than the review's own suggestion.
- **Changing `send()` itself to return `NeedsCredentialOrProviderChange`**
  — rejected outright once its own existing tests were found; would
  break `test_send_raises_unknown_provider` and its sibling, reopening
  an already-closed, already-tested contract for no requirement that
  asks for it.
- **A new, dedicated outcome type** (e.g. `UnknownOrUnwiredProvider`)
  instead of reusing `NeedsCredentialOrProviderChange` — declined; the
  existing type already means exactly this shape of problem
  ("something about which provider/credential was asked for is wrong"),
  and CLAUDE.md's own rule keeps a classified outcome's branches to a
  named, closed set only when they carry meaningfully different data —
  a `detail: str` message is all either case needs, so they're the same
  branch, not two.
- **Keeping `cmd_chat`'s proactive check anyway**, alongside the real
  fix, as a fast-fail nicety — this was a real option put to the
  maintainer during planning and explicitly declined: one place should
  decide this, not two, and the "fails before creating a conversation
  row" behavior it bought is worth less than the duplication it cost.

## Concerns

- **Exception-shadowing risk, checked and judged negligible.** `resolve()`'s
  new `except (UnknownProvider, ProviderNotWired)` wraps the entire
  `send()` call, which does eventually invoke a provider's own
  `request_fn`. If some future provider implementation ever raised
  either of these two specific, narrowly-scoped exception classes for
  an unrelated reason, `resolve()` would misclassify it as a
  provider/credential problem. Both classes exist solely in
  `model_access.py` for exactly the precondition checks this fix
  targets, and no provider implementation has any reason to import or
  raise them — judged negligible, not zero, and easy to revisit if a
  real provider implementation ever wants its own exception hierarchy.
- **`sadana chat`'s exit code for this one case changes from `2` to
  `1`**, a real, deliberate, user-visible behavior change to
  already-shipped CLI-SHELL-04 code, made because intent.md's own
  interview asked for it. Called out plainly here and in §Design so a
  future reader doesn't mistake it for an accidental regression.
- **`testing-conventions` is applied**: the new `resolve()` tests need
  no mocking at all — an unregistered provider name and an
  `"anthropic"`-style registered-but-unwired one both fail inside
  `get_provider()`/`send()`'s own synchronous precondition checks,
  before any network I/O could occur, matching the existing
  `test_send_raises_unknown_provider` test's own precedent of calling
  `send()`/`resolve()` for real rather than faking a response.
- No policy conflict found. The one real tension — the deploy review's
  own suggested fix location versus a more precise one found by
  checking the existing test suite first — is resolved in §Rejected
  alternatives, not silently picked.
