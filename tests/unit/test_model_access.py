"""Tests for sadana.model_access: classify(), send() orchestration, registry."""

from __future__ import annotations

import asyncio

import pytest

from sadana import model_access
from sadana.context import CacheHint
from sadana.model_access import (
    Abort,
    Degenerate,
    NeedsContextCompression,
    NeedsCredentialOrProviderChange,
    ProviderManifest,
    ProviderNotWired,
    Request,
    Response,
    Retry,
    UnknownProvider,
    classify,
    context_window,
    get_provider,
    list_providers,
    mark_cache_boundary,
    register_provider,
    resolve,
    send,
)

# ── classify() ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("status", [401, 403])
def test_classify_auth_failure_needs_credential_or_provider_change(status: int) -> None:
    outcome = classify(status, {"error": {"message": "bad key"}}, attempt=0, max_retries=3)
    assert isinstance(outcome, NeedsCredentialOrProviderChange)


@pytest.mark.unit
@pytest.mark.parametrize("status", [429, 500, 503, None])
def test_classify_transient_failure_retries_below_cap(status: int | None) -> None:
    outcome = classify(status, {"error": {"message": "overloaded"}}, attempt=1, max_retries=3)
    assert outcome == Retry(next_attempt=2)


@pytest.mark.unit
def test_classify_transient_failure_aborts_at_cap() -> None:
    outcome = classify(500, {"error": {"message": "overloaded"}}, attempt=3, max_retries=3)
    assert isinstance(outcome, Abort)


@pytest.mark.unit
def test_classify_413_needs_context_compression() -> None:
    outcome = classify(413, {"error": {"message": "payload too large"}}, attempt=0, max_retries=3)
    assert isinstance(outcome, NeedsContextCompression)


@pytest.mark.unit
def test_classify_context_length_message_needs_context_compression() -> None:
    body = {"error": {"message": "This model's maximum context length is 4096 tokens."}}
    outcome = classify(400, body, attempt=0, max_retries=3)
    assert isinstance(outcome, NeedsContextCompression)


@pytest.mark.unit
def test_classify_normal_response() -> None:
    body = {
        "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }
    outcome = classify(200, body, attempt=0, max_retries=3)
    assert isinstance(outcome, Response)
    assert outcome.content == "hi there"
    assert outcome.usage.completion_tokens == 3


@pytest.mark.unit
def test_classify_empty_completion_is_degenerate() -> None:
    body = {
        "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
    }
    outcome = classify(200, body, attempt=0, max_retries=3)
    assert isinstance(outcome, Degenerate)


@pytest.mark.unit
def test_classify_content_filter_refusal_is_not_degenerate() -> None:
    """A signaled refusal is a complete, terminal response — not empty. Matches
    hermes's own empty_response_guard.py distinction (spec.md)."""
    body = {
        "choices": [{"message": {"content": None}, "finish_reason": "content_filter"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
    }
    outcome = classify(200, body, attempt=0, max_retries=3)
    assert isinstance(outcome, Response)
    assert outcome.finish_reason == "content_filter"


@pytest.mark.unit
def test_classify_unclassifiable_answered_status_aborts() -> None:
    outcome = classify(400, {"error": {"message": "unknown model 'bogus'"}}, attempt=0, max_retries=3)
    assert isinstance(outcome, Abort)


# ── _discover() ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_discover_scans_a_fixture_directory(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Exercises _discover()'s own scan/import logic against a throwaway
    directory — never the real model_providers/ tree — per testing-
    conventions (real filesystem only under tmp_path)."""
    wired = tmp_path / "fixture-provider"
    wired.mkdir()
    (wired / "provider.py").write_text(
        "from sadana.model_access import ProviderManifest, register_provider\n"
        "register_provider(ProviderManifest(name='fixture-provider'))\n"
    )
    (tmp_path / "_hidden").mkdir()  # underscore-prefixed: must be skipped, not imported
    (tmp_path / "_hidden" / "provider.py").write_text("raise RuntimeError('must not be imported')\n")
    (tmp_path / "no-manifest").mkdir()  # no provider.py inside: must be skipped without error

    monkeypatch.setattr(model_access, "_PROVIDERS_DIR", tmp_path)
    monkeypatch.setattr(model_access, "_REGISTRY", {})
    monkeypatch.setattr(model_access, "_discovered", False)

    model_access._discover()

    assert set(model_access._REGISTRY) == {"fixture-provider"}


# ── context_window() ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_context_window_known_model() -> None:
    assert context_window("openrouter", "deepseek/deepseek-v4-flash-0731") == 1_310_720


@pytest.mark.unit
def test_context_window_raises_for_unknown_model() -> None:
    with pytest.raises(KeyError):
        context_window("openrouter", "no-such-model")


# ── registry ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_register_and_get_provider_round_trips() -> None:
    manifest = ProviderManifest(name="test-fake-provider-registry")
    register_provider(manifest)
    assert get_provider("test-fake-provider-registry") is manifest


@pytest.mark.unit
def test_get_provider_raises_unknown_provider_for_unregistered_name() -> None:
    with pytest.raises(UnknownProvider):
        get_provider("definitely-not-a-real-provider")


@pytest.mark.unit
def test_all_39_hermes_providers_are_registered() -> None:
    names = {p.name for p in list_providers()}
    expected = {
        "actual",
        "ai-gateway",
        "alibaba",
        "alibaba-coding-plan",
        "anthropic",
        "arcee",
        "azure-foundry",
        "bedrock",
        "commandcode",
        "copilot",
        "copilot-acp",
        "custom",
        "deepinfra",
        "deepseek",
        "fireworks",
        "gemini",
        "gmi",
        "huggingface",
        "kilocode",
        "kimi-coding",
        "meta-ai",
        "minimax",
        "nebius-token-factory",
        "nous",
        "novita",
        "nvidia",
        "ollama-cloud",
        "openai-codex",
        "opencode-free",
        "opencode-zen",
        "openrouter",
        "qwen-oauth",
        "router",
        "stepfun",
        "upstage",
        "vertex",
        "xai",
        "xiaomi",
        "zai",
    }
    assert expected <= names


@pytest.mark.unit
def test_only_openrouter_is_wired() -> None:
    for manifest in list_providers():
        if manifest.name == "openrouter":
            assert manifest.request_fn is not None
        elif manifest.name in {
            "actual",
            "ai-gateway",
            "alibaba",
            "alibaba-coding-plan",
            "anthropic",
            "arcee",
            "azure-foundry",
            "bedrock",
            "commandcode",
            "copilot",
            "copilot-acp",
            "custom",
            "deepinfra",
            "deepseek",
            "fireworks",
            "gemini",
            "gmi",
            "huggingface",
            "kilocode",
            "kimi-coding",
            "meta-ai",
            "minimax",
            "nebius-token-factory",
            "nous",
            "novita",
            "nvidia",
            "ollama-cloud",
            "openai-codex",
            "opencode-free",
            "opencode-zen",
            "qwen-oauth",
            "router",
            "stepfun",
            "upstage",
            "vertex",
            "xai",
            "xiaomi",
            "zai",
        }:
            assert manifest.request_fn is None


# ── send() ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_send_raises_unknown_provider() -> None:
    request = Request(messages=(), provider="definitely-not-a-real-provider", model="x")
    with pytest.raises(UnknownProvider):
        send(request)


@pytest.mark.unit
def test_send_raises_provider_not_wired_for_registered_but_unwired_provider() -> None:
    request = Request(messages=(), provider="anthropic", model="x")
    with pytest.raises(ProviderNotWired):
        send(request)


@pytest.mark.unit
def test_send_returns_needs_credential_when_env_var_missing_and_never_calls_request_fn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    register_provider(
        ProviderManifest(
            name="test-fake-provider-missing-cred",
            env_vars=("SADANA_TEST_FAKE_KEY",),
            request_fn=lambda req: (calls.append(req) or (200, {})),
        )
    )
    monkeypatch.delenv("SADANA_TEST_FAKE_KEY", raising=False)
    request = Request(messages=(), provider="test-fake-provider-missing-cred", model="x")
    outcome = send(request)
    assert isinstance(outcome, NeedsCredentialOrProviderChange)
    assert calls == []


@pytest.mark.unit
def test_send_calls_request_fn_and_classifies_its_result(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    register_provider(
        ProviderManifest(
            name="test-fake-provider-wired",
            env_vars=("SADANA_TEST_FAKE_KEY",),
            request_fn=lambda req: (200, body),
        )
    )
    monkeypatch.setenv("SADANA_TEST_FAKE_KEY", "present")
    request = Request(messages=({"role": "user", "content": "hi"},), provider="test-fake-provider-wired", model="x")
    outcome = send(request)
    assert isinstance(outcome, Response)
    assert outcome.content == "ok"


# ── mark_cache_boundary ───────────────────────────────────────────────────


@pytest.mark.unit
def test_mark_cache_boundary_splits_system_prefix_with_nonempty_suffix() -> None:
    messages = ({"role": "system", "content": "stable part" + "volatile tail"},)
    hint = CacheHint(stable_prefix_len=len("stable part"), trailing_marks=0)

    marked = mark_cache_boundary(messages, hint)

    assert marked[0]["content"] == [
        {"type": "text", "text": "stable part", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "volatile tail"},
    ]


@pytest.mark.unit
def test_mark_cache_boundary_marks_whole_system_message_when_prefix_is_everything() -> None:
    messages = ({"role": "system", "content": "all stable"},)
    hint = CacheHint(stable_prefix_len=len("all stable"), trailing_marks=0)

    marked = mark_cache_boundary(messages, hint)

    # No empty-suffix part on the wire (the API rejects an empty text block).
    assert marked[0]["content"] == [{"type": "text", "text": "all stable", "cache_control": {"type": "ephemeral"}}]


@pytest.mark.unit
def test_mark_cache_boundary_marks_trailing_eligible_messages() -> None:
    messages = (
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "one"},
        {"role": "user", "content": "two"},
        {"role": "user", "content": "three"},
    )
    hint = CacheHint(stable_prefix_len=0, trailing_marks=2)

    marked = mark_cache_boundary(messages, hint)

    assert marked[1]["content"] == "one"  # untouched — outside the trailing window
    assert marked[2]["content"] == [{"type": "text", "text": "two", "cache_control": {"type": "ephemeral"}}]
    assert marked[3]["content"] == [{"type": "text", "text": "three", "cache_control": {"type": "ephemeral"}}]


@pytest.mark.unit
def test_mark_cache_boundary_skips_empty_content_message() -> None:
    messages = (
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": None, "tool_calls": ({"function": {"name": "f", "arguments": "{}"}},)},
        {"role": "user", "content": "real content"},
    )
    hint = CacheHint(stable_prefix_len=0, trailing_marks=2)

    marked = mark_cache_boundary(messages, hint)

    # Only one eligible message exists; the empty-content assistant turn
    # never receives a marker, even though the budget would allow two.
    assert marked[1] == messages[1]
    assert marked[2]["content"] == [{"type": "text", "text": "real content", "cache_control": {"type": "ephemeral"}}]


@pytest.mark.unit
def test_mark_cache_boundary_never_mutates_input() -> None:
    original = ({"role": "system", "content": "stable"}, {"role": "user", "content": "hi"})
    snapshot = ({"role": "system", "content": "stable"}, {"role": "user", "content": "hi"})

    mark_cache_boundary(original, CacheHint(stable_prefix_len=6, trailing_marks=1))

    assert original == snapshot


@pytest.mark.unit
def test_mark_cache_boundary_empty_messages_is_a_no_op() -> None:
    assert mark_cache_boundary((), CacheHint(stable_prefix_len=0, trailing_marks=1)) == ()


@pytest.mark.unit
def test_mark_cache_boundary_finds_system_message_not_at_index_zero() -> None:
    messages = (
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "stable part" + "volatile"},
    )
    hint = CacheHint(stable_prefix_len=len("stable part"), trailing_marks=0)

    marked = mark_cache_boundary(messages, hint)

    assert marked[0] == messages[0]
    assert marked[1]["content"] == [
        {"type": "text", "text": "stable part", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "volatile"},
    ]


@pytest.mark.unit
def test_mark_cache_boundary_excludes_system_message_from_trailing_marks() -> None:
    messages = (
        {"role": "user", "content": "one"},
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "two"},
    )
    hint = CacheHint(stable_prefix_len=0, trailing_marks=5)

    marked = mark_cache_boundary(messages, hint)

    # The system message never receives a trailing mark, however large the
    # budget — only the two real non-system messages do.
    assert marked[0]["content"] == [{"type": "text", "text": "one", "cache_control": {"type": "ephemeral"}}]
    assert marked[1]["content"] == "sys"
    assert marked[2]["content"] == [{"type": "text", "text": "two", "cache_control": {"type": "ephemeral"}}]


@pytest.mark.unit
def test_mark_cache_boundary_with_no_system_message_marks_only_trailing() -> None:
    messages = ({"role": "user", "content": "one"}, {"role": "user", "content": "two"})
    hint = CacheHint(stable_prefix_len=5, trailing_marks=1)

    marked = mark_cache_boundary(messages, hint)

    assert marked[0] == messages[0]
    assert marked[1]["content"] == [{"type": "text", "text": "two", "cache_control": {"type": "ephemeral"}}]


# ── resolve() ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_resolve_retries_transparently_and_returns_final_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_send(request: Request) -> model_access.Outcome:
        calls.append(request.attempt)
        if len(calls) < 3:
            return Retry(next_attempt=len(calls))
        return Response(content="ok", tool_calls=(), finish_reason="stop", usage=model_access.Usage())

    monkeypatch.setattr(model_access, "send", fake_send)
    request = Request(messages=(), provider="p", model="m")

    outcome = asyncio.run(resolve(request))

    assert isinstance(outcome, Response)
    assert outcome.content == "ok"
    assert calls == [0, 1, 2]


@pytest.mark.unit
@pytest.mark.parametrize(
    "final_outcome",
    [
        NeedsCredentialOrProviderChange("no key"),
        NeedsContextCompression("too big"),
        Degenerate("empty"),
        Abort("gave up"),
    ],
)
def test_resolve_returns_each_non_retry_outcome_unchanged(
    monkeypatch: pytest.MonkeyPatch, final_outcome: model_access.Outcome
) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: final_outcome)
    outcome = asyncio.run(resolve(Request(messages=(), provider="p", model="m")))
    assert outcome is final_outcome


@pytest.mark.unit
def test_resolve_is_cancellable_between_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cold review caught that collapsing the retry loop into one
    asyncio.to_thread call around the whole sequence (instead of one per
    attempt) would make a mid-turn cancellation wait out every remaining
    attempt before it could be delivered, since the background thread
    running the whole loop can't be stopped once dispatched. Proof: after
    cancellation, no further attempt is ever dispatched — not just that
    the coroutine eventually raises CancelledError, which a single
    giant to_thread call would also do without actually stopping."""
    import time

    attempts_started: list[int] = []

    def fake_send(request: Request) -> model_access.Outcome:
        attempts_started.append(request.attempt)
        time.sleep(0.02)  # real, measurable per-attempt cost
        return Retry(next_attempt=request.attempt + 1)  # retries forever unless cancelled

    monkeypatch.setattr(model_access, "send", fake_send)

    async def scenario() -> None:
        task = asyncio.ensure_future(resolve(Request(messages=(), provider="p", model="m")))
        await asyncio.sleep(0.05)  # real, short sleep: lets a couple of attempts run
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())

    count_at_cancel = len(attempts_started)
    assert count_at_cancel > 0  # at least one real attempt happened before cancellation
    time.sleep(0.1)  # plenty of time for a leaked background loop to keep going, if one existed
    assert len(attempts_started) == count_at_cancel  # no further attempt was ever dispatched
