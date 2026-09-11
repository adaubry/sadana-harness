"""Tests for sadana.client_surface."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from conftest import make_runtime, never_send, open_conn, plain_response, tool_then_text
from conftest import wait_then_summarize_installed as _wait_then_summarize_installed
from sadana import client_surface, memory_store, model_access, plugin_dispatch, plugins
from sadana.client_surface import open_conversation, open_runtime, take_turn
from sadana.conversation import (
    ConversationTemplate,
    ExitReason,
    IterationBudget,
    TemplateRecipe,
    create_conversation,
)
from sadana.conversation_store import ConversationAlreadyExists, ConversationNotFound, create, load, load_pause

# ── open_runtime: the assembly the three call sites used to each own ────────


@pytest.mark.unit
def test_open_runtime_returns_a_runtime_a_turn_can_actually_run_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """One assertion over the whole assembly: a turn on an `open_runtime()`
    result proves the store opened, the memory schema exists (creating a
    conversation reads those tables), the persona seeded, and the plugin scan
    survived an empty plugins directory."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))

    runtime = open_runtime(provider="p", model="m")
    outcome = asyncio.run(take_turn(runtime, account="a", conversation="c1", text="hi", create_as="chat"))

    assert outcome.ok is True
    assert outcome.answer == "reply"


@pytest.mark.unit
def test_open_runtime_resolves_provider_and_model_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_MODEL_ACCESS_PROVIDER", "env-provider")
    monkeypatch.setenv("SADANA_MODEL_ACCESS_MODEL", "env-model")

    runtime = open_runtime()

    assert (runtime.provider, runtime.model) == ("env-provider", "env-model")


@pytest.mark.unit
def test_open_runtime_lets_an_explicit_override_win_over_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sadana chat --provider/--model` is a per-run override of the same
    config key, so the argument has to beat the environment."""
    monkeypatch.setenv("SADANA_MODEL_ACCESS_PROVIDER", "env-provider")
    monkeypatch.setenv("SADANA_MODEL_ACCESS_MODEL", "env-model")

    runtime = open_runtime(provider="flag-provider", model="flag-model")

    assert (runtime.provider, runtime.model) == ("flag-provider", "flag-model")


# ── create_as: the one thing the two clients disagreed about ────────────────


@pytest.mark.unit
def test_take_turn_creates_the_conversation_under_the_template_name_it_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`create_as` is the template name, not a boolean, because the name is
    observable — `sadana conversations` prints it and searches it — and there
    is no way to ask for creation without saying what to call the thing."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    conn = open_conn()

    asyncio.run(take_turn(make_runtime(conn), account="a", conversation="k-new", text="hi", create_as="chat"))

    assert load(conn, "k-new", now=0.0).template_name == "chat"


@pytest.mark.unit
def test_take_turn_refuses_an_unknown_conversation_when_create_as_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sadana chat --resume KEY` means "this one already exists". A caller
    error, so it raises — the only thing this function raises — while every
    *turn* outcome comes back as a value."""

    monkeypatch.setattr(model_access, "send", never_send)
    conn = open_conn()
    runtime = make_runtime(conn)

    with pytest.raises(ConversationNotFound):
        asyncio.run(take_turn(runtime, account="a", conversation="k-missing", text="hi", create_as=None))

    with pytest.raises(ConversationNotFound):
        load(conn, "k-missing", now=0.0)


# ── open_conversation: settling whether it may exist, before anyone speaks ─


@pytest.mark.unit
def test_open_conversation_with_a_template_name_creates_it_before_any_turn() -> None:
    """A terminal shows a prompt before it has a line to send, so the
    conversation exists from the moment the client starts — with no messages
    and no turns taken."""
    conn = open_conn()

    open_conversation(make_runtime(conn), account="a", conversation="k-started", template_name="chat")

    started = load(conn, "k-started", now=0.0)
    assert (started.template_name, started.messages, started.next_turn_seq) == ("chat", (), 0)


@pytest.mark.unit
def test_open_conversation_refuses_a_name_that_is_already_taken() -> None:
    """`sadana chat --key NAME` means "a new one, called this". The door says
    so rather than silently continuing someone's existing conversation."""
    conn = open_conn()
    runtime = make_runtime(conn)
    open_conversation(runtime, account="a", conversation="k-taken", template_name="chat")

    with pytest.raises(ConversationAlreadyExists):
        open_conversation(runtime, account="a", conversation="k-taken", template_name="chat")


@pytest.mark.unit
def test_open_conversation_with_no_template_name_requires_it_to_exist() -> None:
    """`--resume KEY` means "this one already exists", and has to fail before
    a prompt appears rather than after the person has typed a line."""
    conn = open_conn()
    runtime = make_runtime(conn)

    with pytest.raises(ConversationNotFound):
        open_conversation(runtime, account="a", conversation="k-absent", template_name=None)

    open_conversation(runtime, account="a", conversation="k-absent", template_name="chat")
    open_conversation(runtime, account="a", conversation="k-absent", template_name=None)  # now it does


# ── the three fields, and why neither recovers the other ───────────────────


@pytest.mark.unit
def test_a_completed_turn_reports_ok_with_its_text_as_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    conn = open_conn()

    outcome = asyncio.run(take_turn(make_runtime(conn), account="a", conversation="k-ok", text="hi", create_as="chat"))

    assert outcome.ok is True
    assert outcome.answer == "reply"
    assert outcome.diagnostic.startswith(f"[{ExitReason.COMPLETED.value}]")


@pytest.mark.unit
def test_a_non_completed_turn_still_carries_its_own_text_in_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The distinction the terminal's stdout depends on. An exhausted
    iteration budget still produces a real summary (`conversation.py`'s
    EPILOGUE), so `ok` cannot be recovered from the text and the text cannot
    be recovered from `ok` — which is why `TurnOutcome` has three fields
    rather than a single string.

    Also the `create_as=None` path over a conversation that does exist.
    """
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("here is what we did"))
    conn = open_conn()
    exhausted, _template = create_conversation(
        ConversationTemplate(name="chat", recipe=TemplateRecipe(stable_prompt="p", catalog=(), tool_specs=())),
        "k-exhausted",
        system_message="",
        iteration_budget=IterationBudget(max_total=1, used=1),
    )
    create(conn, exhausted, now=0.0)

    outcome = asyncio.run(
        take_turn(make_runtime(conn), account="a", conversation="k-exhausted", text="hi", create_as=None)
    )

    assert outcome.ok is False
    assert outcome.answer == "here is what we did"
    assert outcome.diagnostic.startswith(f"[{ExitReason.BUDGET_EXHAUSTED.value}]")


# ── identity is the caller's, never this module's ──────────────────────────


@pytest.mark.unit
def test_take_turn_recalls_the_account_it_was_given_and_no_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 3 as code: the account is stated by the caller and this
    module resolves none of its own. `SADANA_MEMORY_ACCOUNT` is the terminal's
    *client-side* default — set here to a different account with its own
    memories, which must not leak into a turn that named someone else."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    monkeypatch.setenv("SADANA_MEMORY_ACCOUNT", "someone-else")
    conn = open_conn()
    memory_store.ensure_schema(conn)
    memory_store.write_entry(conn, "stated", "dog_name", "Their dog is named Buddy.", now=0.0)
    memory_store.write_entry(conn, "someone-else", "cat_name", "Their cat is named Smudge.", now=0.0)

    asyncio.run(take_turn(make_runtime(conn), account="stated", conversation="k-acct", text="hi", create_as="chat"))

    system_prompt = load(conn, "k-acct", now=0.0).system_prompt
    assert "Their dog is named Buddy." in system_prompt
    assert "Their cat is named Smudge." not in system_prompt


# ── the pause loop: the defect this work item exists to fix ────────────────


@pytest.mark.unit
def test_a_paused_run_is_persisted_and_the_next_turn_resumes_it_without_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole loop in one test, because a client only ever meets it as a
    loop: a `wait` node reached during a turn writes a `plugin_pauses` row,
    and the next call for that conversation resumes it with no model call at
    all. `subcommands/chat.py`'s own copy of this body never passed
    `persist_pause`, so the row was never written and the resume was
    unreachable from the terminal — that is the defect in `intent.md`, and
    this is the behaviour that replaces it for every client at once."""
    installed = _wait_then_summarize_installed(tmp_path)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: tmp_path)
    conn = open_conn()
    runtime = make_runtime(conn, plugin_set=plugin_dispatch.build_plugin_set((installed,)))

    monkeypatch.setattr(model_access, "send", tool_then_text())
    first = asyncio.run(take_turn(runtime, account="a", conversation="k-pause", text="start it", create_as="chat"))

    assert first.answer == "okay, I'll wait for it."
    pause = load_pause(conn, conversation_key="k-pause")
    assert pause is not None
    assert (pause.plugin, pause.node) == ("p", "future")

    monkeypatch.setattr(model_access, "send", never_send)
    second = asyncio.run(take_turn(runtime, account="a", conversation="k-pause", text="42", create_as=None))

    assert second.ok is True
    assert second.answer == "answered: 42"
    assert second.diagnostic == ""
    assert load_pause(conn, conversation_key="k-pause") is None
    last = load(conn, "k-pause", now=0.0).messages[-1]
    assert (last.role, last.content) == ("assistant", "answered: 42")


# ── the connection lock ────────────────────────────────────────────────────


class _RecordingLock:
    """A real `threading.Lock`, wrapped to count acquisitions and to notice if
    two holders were ever inside it at once. Same shape `test_scheduling.py`
    already uses for `tick()`'s own lock discipline."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.acquisitions = 0
        self.max_held_at_once = 0
        self._held = 0

    def __enter__(self) -> None:
        self._lock.acquire()
        self.acquisitions += 1
        self._held += 1
        self.max_held_at_once = max(self.max_held_at_once, self._held)

    def __exit__(self, *_exc: object) -> None:
        self._held -= 1
        self._lock.release()


@pytest.mark.unit
def test_two_threads_taking_a_turn_on_one_runtime_serialize(monkeypatch: pytest.MonkeyPatch) -> None:
    """`conversation_store.open_store()` passes `check_same_thread=False` so one
    connection can be used from more than one thread, and its own docstring is
    explicit that it is still only safe "by one thread at a time". A webhook
    served by `ThreadingHTTPServer` gives every request its own thread, so this
    is the real condition, not a hypothetical.

    The two threads are released together by a `threading.Barrier` and the model
    stub holds the critical section open long enough that an unserialized pair
    would overlap. `max_held_at_once == 1` is an invariant, not a timing
    assumption: it cannot fail unless an overlap genuinely happened.
    """
    recording_lock = _RecordingLock()
    monkeypatch.setattr(client_surface, "conn_lock", recording_lock)
    conn = open_conn()
    runtime = make_runtime(conn)
    start = threading.Barrier(2)

    def _slow_send(_request: object) -> model_access.Response:
        time.sleep(0.05)  # wide enough that two unserialized turns would overlap
        return plain_response("reply")

    monkeypatch.setattr(model_access, "send", _slow_send)
    outcomes: dict[str, client_surface.TurnOutcome] = {}

    def _turn(key: str) -> None:
        start.wait(timeout=5)
        outcomes[key] = asyncio.run(take_turn(runtime, account="a", conversation=key, text="hi", create_as="chat"))

    threads = [threading.Thread(target=_turn, args=(key,)) for key in ("k-thread-a", "k-thread-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a turn never finished — the lock is not being released"

    assert recording_lock.acquisitions == 2
    assert recording_lock.max_held_at_once == 1
    assert [o.ok for o in outcomes.values()] == [True, True]
    assert load(conn, "k-thread-a", now=0.0).next_turn_seq == 1
    assert load(conn, "k-thread-b", now=0.0).next_turn_seq == 1


@pytest.mark.unit
def test_a_turn_holds_the_connection_lock_while_it_runs_and_releases_it_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`conversation_store.open_store()`'s connection is safe for one thread
    at a time and `channel_webhook.py` hands every request its own thread, so
    the whole turn is serialized. Proven from inside the turn rather than with
    a substituted lock: the model call happens mid-turn, so the lock must be
    held at that moment and free once the call returns. This proves the
    wiring, not real concurrency — the same thing `test_scheduling.py`'s own
    lock test says about itself."""
    held: list[bool] = []

    def _send(_request: object) -> model_access.Response:
        held.append(client_surface.conn_lock.locked())
        return plain_response("reply")

    monkeypatch.setattr(model_access, "send", _send)
    conn = open_conn()

    asyncio.run(take_turn(make_runtime(conn), account="a", conversation="k-lock", text="hi", create_as="chat"))

    assert held == [True]
    assert client_surface.conn_lock.locked() is False
