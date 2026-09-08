# Plan: Asking for a service that isn't there fails the same way everything else does (from intent.md 2026-09-08)

## Files that change

- `src/sadana/model_access.py` — `resolve()` gains a `try/except`
  around its one `send()` call; docstring updated.
- `tests/unit/test_model_access.py` — one new parametrized test for
  `resolve()`'s new behavior (a build-time `/simplify` self-check found
  the initial two-tests draft was copy-paste with a one-token
  variation; collapsed into one, matching this file's own existing
  `test_resolve_returns_each_non_retry_outcome_unchanged` parametrize
  style right above it).
- `src/sadana/subcommands/chat.py` — `cmd_chat`'s proactive
  provider-validation block deleted; the now-unused `model_access`
  import removed with it.
- `tests/unit/test_subcommands_chat.py` — the test proving the old
  "exits 2 before touching the store" behavior is replaced with one
  proving the new "fails via the normal turn loop" behavior.

## Order of work

1. **`src/sadana/model_access.py`**, `resolve()`:

   ```python
   async def resolve(
       request: Request,
   ) -> Response | NeedsCredentialOrProviderChange | NeedsContextCompression | Degenerate | Abort:
       """Call `send()` for `request`, looping while it returns `Retry`, and
       return the first non-`Retry` outcome. The shared mechanical retry loop
       every caller of this module needs — CLAUDE.md: a retry loop over a
       provider's `Retry` outcome lives once, here, never duplicated per
       caller. Stays unaware of any caller's own exception types: each caller
       translates the returned outcome itself.

       `send()`'s own two precondition failures — `UnknownProvider`,
       `ProviderNotWired` — are folded into `NeedsCredentialOrProviderChange`
       here rather than left to escape uncaught: the same outcome `send()`
       already returns for a missing credential, since all three are the
       same shape of problem ("this request cannot proceed because of what
       provider was asked for"). `send()` and `get_provider()` themselves
       keep raising exactly as their own tests already require — this is
       the one layer between them and every real caller, so it is the one
       place that needs to know.

       `async`, wrapping each individual attempt in its own
       `asyncio.to_thread` — not one call around the whole loop — so a
       pending cancellation is still observable between attempts, the same
       property the loop this replaced already had. Collapsing the bounded
       retry sequence into a single thread-pool call would make a mid-turn
       cancellation wait out every remaining attempt (each a real network
       round trip) before it could be delivered."""
       attempt = request.attempt
       while True:
           try:
               outcome = await asyncio.to_thread(send, replace(request, attempt=attempt))
           except (UnknownProvider, ProviderNotWired) as exc:
               return NeedsCredentialOrProviderChange(str(exc))
           if isinstance(outcome, Retry):
               attempt = outcome.next_attempt
               continue
           return outcome
   ```

2. **`tests/unit/test_model_access.py`**: add, right after
   `test_resolve_returns_each_non_retry_outcome_unchanged` (no
   monkeypatching needed — both failures happen inside `get_provider()`/
   `send()`'s own synchronous precondition checks, before any network
   I/O, matching `test_send_raises_unknown_provider`'s own precedent of
   calling the real function):

   ```python
   @pytest.mark.unit
   def test_resolve_returns_needs_credential_for_unknown_provider() -> None:
       outcome = asyncio.run(resolve(Request(messages=(), provider="definitely-not-a-real-provider", model="x")))
       assert isinstance(outcome, NeedsCredentialOrProviderChange)


   @pytest.mark.unit
   def test_resolve_returns_needs_credential_for_unwired_provider() -> None:
       outcome = asyncio.run(resolve(Request(messages=(), provider="anthropic", model="x")))
       assert isinstance(outcome, NeedsCredentialOrProviderChange)
   ```

3. **`src/sadana/subcommands/chat.py`**: delete `cmd_chat`'s proactive
   check and the now-unused `model_access` import.

   ```python
   from sadana import config, conversation_store, plugin_dispatch, plugin_manifest
   ```

   ```python
   def cmd_chat(args: argparse.Namespace) -> int:
       provider = args.provider or config.env("SADANA_MODEL_ACCESS_PROVIDER", "openrouter")
       model = args.model or config.env("SADANA_MODEL_ACCESS_MODEL", "deepseek/deepseek-v4-flash-0731")

       persona = load_or_seed_persona(persona_path_from_config())
       plugin_set = plugin_dispatch.build_plugin_set(plugin_manifest.discover_plugins())
       with closing(conversation_store.open_store(conversation_store.store_path_from_config())) as conn:
           ...  # unchanged from here down
   ```

4. **`tests/unit/test_subcommands_chat.py`**: replace
   `test_cmd_chat_unknown_provider_exits_two_before_touching_store`
   with:

   ```python
   @pytest.mark.unit
   def test_cmd_chat_unknown_provider_fails_via_the_normal_turn_loop(monkeypatch: pytest.MonkeyPatch) -> None:
       _feed(monkeypatch, "hello")

       assert cmd_chat(_args(key="bad-provider-test", provider="not-a-real-provider")) == 1

       # unlike before this fix, the conversation row already exists — the
       # failure is discovered on the first turn, not before the store is
       # touched. _load would raise ConversationNotFound if it weren't.
       _load("bad-provider-test")
   ```

   No `model_access.send` monkeypatching needed — the same real,
   no-network-I/O failure path as step 2's new `resolve()` tests.

5. Run `scripts/run_tests.sh tests/unit/test_model_access.py
   tests/unit/test_subcommands_chat.py tests/unit/test_cli.py
   tests/unit/test_conversation_store.py
   tests/unit/test_subcommands_conversations.py`, then
   `/ponytail-review` and `/simplify` against the diff, then
   `make verify`.

## Risks

- **What could this break?** `send()`/`get_provider()` are read, never
  modified — their own existing tests
  (`test_send_raises_unknown_provider`,
  `test_send_raises_provider_not_wired_for_registered_but_unwired_provider`,
  `test_get_provider_raises_unknown_provider_for_unregistered_name`)
  are run unmodified in step 5 to confirm. `resolve()`'s other existing
  tests (retry-transparency, outcome-pass-through, cancellability) are
  also run unmodified — the new `try/except` wraps only the one
  `send()` call already inside the loop; every other code path through
  `resolve()` is untouched. Every other CLI-SHELL test file is run in
  step 5 to confirm removing `cmd_chat`'s check didn't disturb anything
  else in that module.
- **Riskiest step**: confirming `resolve()`'s `except` clause doesn't
  also swallow the pass-through `NeedsCredentialOrProviderChange` case
  `test_resolve_returns_each_non_retry_outcome_unchanged` already
  covers (that one is a *returned* outcome from a mocked `send`, not a
  *raised* exception — the `try/except` only ever intercepts an actual
  raise, so a mocked `send` that returns `NeedsCredentialOrProviderChange("no key")`
  is never touched by the new code path). Confirmed by tracing the
  parametrized test's own mechanism before writing the fix, not
  assumed; that exact test runs unmodified in step 5 as the check.
- **Drift check against spec.md's Rejected alternatives**: fix lands in
  `resolve()`, not `complete()` or `send()` — followed. Reuses
  `NeedsCredentialOrProviderChange`, no new outcome type — followed.
  `cmd_chat`'s check is deleted outright, not kept alongside — followed.

## Proof

- `tests/unit/test_model_access.py`'s new parametrized test covers
  `resolve()` returning `NeedsCredentialOrProviderChange` for both an
  unregistered and a registered-but-unwired provider, with no mocking.
- `tests/unit/test_subcommands_chat.py`'s replacement test covers
  `cmd_chat` failing cleanly (exit `1`) through the normal turn loop for
  a bad provider, and confirms the conversation row is still created
  (the accepted, documented consequence of removing the proactive
  check).
- Every existing test named in § Risks passes unmodified.
- `make verify` ends `VERIFY OK`.

## Post-review fix

The deploy stage's cold review (see `review.md`) found that
`NeedsCredentialOrProviderChange(str(exc))` — this plan's own code
sample above — produces an unhelpful `.detail`: both `UnknownProvider`
and `ProviderNotWired` carry only the bare provider name as their
exception message (`str(UnknownProvider("x")) == "x"`), so the
resulting user-facing output (`sadana chat`'s
`[provider_failed] not-a-real-provider`) never said what was actually
wrong. Fixed: two separate `except` arms, each prefixing its own short
label (`"unknown provider: "`, `"provider not wired: "`) before the
original message. The parametrized test in
`tests/unit/test_model_access.py` was strengthened to assert on the
resulting prefix, not just the outcome type, so this can't silently
regress.
