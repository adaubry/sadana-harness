"""Tests for sadana.client_surface."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from conftest import (
    make_runtime,
    never_send,
    open_connections,
    plain_response,
    tool_call_response,
    tool_then_text,
    write_character,
    write_skill,
)
from conftest import wait_then_summarize_installed as _wait_then_summarize_installed
from sadana import (
    client_surface,
    conversation_store,
    memory,
    memory_store,
    model_access,
    persona,
    persona_store,
    plugin_dispatch,
    plugin_install,
    plugins,
    stores,
)
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
    conversation reads those tables), the persona schema exists (creating one
    resolves the account's character), and the plugin scan survived an empty
    plugins directory."""
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


@pytest.mark.unit
def test_open_runtime_migrates_legacy_scheduled_triggers_into_schedules() -> None:
    """H27: a `scheduled_triggers` row already on disk before `open_runtime`
    is ever called is copied into `schedules` under the owner's account, the
    same "adopt on open" shape `_adopt_scheduled_memories` already proves
    for memory entries."""
    path = conversation_store.store_path_from_config()
    seed_conn = conversation_store.open_store(path)
    conversation_store.upsert_scheduled_trigger(
        seed_conn, name="legacy", trigger_text="go", next_run_at=42.0, interval_seconds=30.0
    )

    runtime = open_runtime(provider="p", model="m")

    rows = conversation_store.list_schedules(runtime.connections.reader(), account_key=memory.owner_account())
    assert [r.name for r in rows] == ["legacy"]


# ── create_as: the one thing the two clients disagreed about ────────────────


@pytest.mark.unit
def test_take_turn_creates_the_conversation_under_the_template_name_it_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`create_as` is the template name, not a boolean, because the name is
    observable — `sadana conversations` prints it and searches it — and there
    is no way to ask for creation without saying what to call the thing."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    connections = open_connections()
    conn = connections.writer

    asyncio.run(take_turn(make_runtime(connections), account="a", conversation="k-new", text="hi", create_as="chat"))

    assert load(conn, "k-new", now=0.0).template_name == "chat"


@pytest.mark.unit
def test_take_turn_refuses_an_unknown_conversation_when_create_as_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sadana chat --resume KEY` means "this one already exists". A caller
    error, so it raises — the only thing this function raises — while every
    *turn* outcome comes back as a value."""

    monkeypatch.setattr(model_access, "send", never_send)
    connections = open_connections()
    conn = connections.writer
    runtime = make_runtime(connections)

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
    connections = open_connections()
    conn = connections.writer

    open_conversation(make_runtime(connections), account="a", conversation="k-started", template_name="chat")

    started = load(conn, "k-started", now=0.0)
    assert (started.template_name, started.messages, started.next_turn_seq) == ("chat", (), 0)


@pytest.mark.unit
def test_open_conversation_refuses_a_name_that_is_already_taken() -> None:
    """`sadana chat --key NAME` means "a new one, called this". The door says
    so rather than silently continuing someone's existing conversation."""
    connections = open_connections()
    runtime = make_runtime(connections)
    open_conversation(runtime, account="a", conversation="k-taken", template_name="chat")

    with pytest.raises(ConversationAlreadyExists):
        open_conversation(runtime, account="a", conversation="k-taken", template_name="chat")


@pytest.mark.unit
def test_open_conversation_with_no_template_name_requires_it_to_exist() -> None:
    """`--resume KEY` means "this one already exists", and has to fail before
    a prompt appears rather than after the person has typed a line."""
    connections = open_connections()
    runtime = make_runtime(connections)

    with pytest.raises(ConversationNotFound):
        open_conversation(runtime, account="a", conversation="k-absent", template_name=None)

    open_conversation(runtime, account="a", conversation="k-absent", template_name="chat")
    open_conversation(runtime, account="a", conversation="k-absent", template_name=None)  # now it does


# ── the three fields, and why neither recovers the other ───────────────────


@pytest.mark.unit
def test_a_completed_turn_reports_ok_with_its_text_as_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    connections = open_connections()

    outcome = asyncio.run(
        take_turn(make_runtime(connections), account="a", conversation="k-ok", text="hi", create_as="chat")
    )

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
    connections = open_connections()
    conn = connections.writer
    exhausted, _template = create_conversation(
        ConversationTemplate(name="chat", recipe=TemplateRecipe(stable_prompt="p", catalog=(), tool_specs=())),
        "k-exhausted",
        system_message="",
        iteration_budget=IterationBudget(max_total=1, used=1),
    )
    create(conn, exhausted, now=0.0, account_key="a1")  # pragma: allowlist secret

    outcome = asyncio.run(
        take_turn(make_runtime(connections), account="a", conversation="k-exhausted", text="hi", create_as=None)
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
    connections = open_connections()
    conn = connections.writer
    memory_store.ensure_schema(conn)
    memory_store.write_entry(conn, "stated", "dog_name", "Their dog is named Buddy.", now=0.0)
    memory_store.write_entry(conn, "someone-else", "cat_name", "Their cat is named Smudge.", now=0.0)

    asyncio.run(
        take_turn(make_runtime(connections), account="stated", conversation="k-acct", text="hi", create_as="chat")
    )

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
    connections = open_connections()
    conn = connections.writer
    runtime = make_runtime(connections, plugin_set=plugin_dispatch.build_plugin_set((installed,)))

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


# ── H18: a call-parked conversation ──────────────────────────────────────


def _call_then_stop_installed(tmp_path: Path) -> plugins.InstalledPlugin:
    """A single-`call`-node plugin, reachable as tool `do_it` — the same
    on-disk-fixture shape `wait_then_summarize_installed` uses, with a
    `call` node instead of a `wait` one."""
    plugin_dir = tmp_path / "p"
    plugin_dir.mkdir()
    plugin_dir.joinpath("init.py").write_text("def do_call(value):\n    return f'called: {value}'\n")
    plugin_dir.joinpath("s.json").write_text('{"type": "object"}')
    plugin_dir.joinpath("plugin.toml").write_text(
        '[plugin]\nname = "p"\nversion = "0.1.0"\ndescription = "d"\n\n'
        '[[entry]]\ntool = "do_it"\npurpose = "p"\nparameters = "s.json"\nstart = "reach_out"\n\n'
        '[[node]]\nname = "reach_out"\nkind = "call"\nbody = "init:do_call"\n'
    )
    manifest = plugins.Manifest(
        name="p",
        version="0.1.0",
        description="d",
        entries=(plugins.Entry(tool="do_it", purpose="p", parameters="s.json", start="reach_out"),),
        nodes=(plugins.Node(name="reach_out", kind="call", body="init:do_call"),),
    )
    return plugins.InstalledPlugin(name="p", directory=plugin_dir, manifest=manifest)


@pytest.mark.unit
def test_a_call_parked_conversation_stores_the_message_and_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`take_turn`'s own default (no `approve` given, the webhook/scheduler
    shape) parks a `call` node instead of blocking — and a later text
    message to that conversation is stored, not run."""
    installed = _call_then_stop_installed(tmp_path)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: tmp_path)
    connections = open_connections()
    conn = connections.writer
    runtime = make_runtime(connections, plugin_set=plugin_dispatch.build_plugin_set((installed,)))

    monkeypatch.setattr(model_access, "send", tool_then_text("do_it", "okay, I'll ask."))
    first = asyncio.run(take_turn(runtime, account="a", conversation="k-call", text="go", create_as="chat"))
    assert first.answer == "okay, I'll ask."

    pause = load_pause(conn, conversation_key="k-call")
    assert pause is not None
    assert pause.kind == "call"

    monkeypatch.setattr(model_access, "send", never_send)
    second = asyncio.run(take_turn(runtime, account="a", conversation="k-call", text="hello?", create_as=None))

    assert second.ok is False
    assert second.answer is None
    assert second.diagnostic.startswith("[waiting_for_approval] ")
    last = load(conn, "k-call", now=0.0).messages[-1]
    assert (last.role, last.content) == ("user", "hello?")


@pytest.mark.unit
def test_take_turn_with_an_explicit_approve_reaches_the_call_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interactive path stays reachable: an `approve` a caller actually
    passes (what `subcommands/chat.py` does for a TTY) is awaited for real,
    not parked."""
    installed = _call_then_stop_installed(tmp_path)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: tmp_path)
    connections = open_connections()
    runtime = make_runtime(connections, plugin_set=plugin_dispatch.build_plugin_set((installed,)))

    seen: list[tuple[str, str, object]] = []

    async def _fake_approve(plugin: str, node: str, value: object) -> bool:
        seen.append((plugin, node, value))
        return True

    monkeypatch.setattr(model_access, "send", tool_then_text("do_it", "called and done."))
    asyncio.run(
        take_turn(runtime, account="a", conversation="k-tty", text="go", create_as="chat", approve=_fake_approve)
    )

    assert seen and seen[0][0] == "p" and seen[0][1] == "reach_out"
    assert load_pause(connections.writer, conversation_key="k-tty") is None


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
def test_two_threads_taking_a_turn_on_one_conversation_serialize(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two turns on **one** conversation still take it in turns, and must.

    A conversation is a sequence. Two turns run into it at once would
    interleave a history rather than share it, so this is the half of the old
    process-wide lock that H16 keeps — narrowed from the whole box to the one
    conversation being written.

    The two threads are released together by a `threading.Barrier` and the
    model stub holds the critical section open long enough that an
    unserialized pair would overlap. `max_held_at_once == 1` is an invariant,
    not a timing assumption: it cannot fail unless an overlap genuinely
    happened.
    """
    recording_lock = _RecordingLock()
    # Module state, so it is put back: `_conversation_locks` is deliberately
    # never evicted, and a test that seeded it would otherwise leak a lock into
    # every test that runs after it in this process.
    monkeypatch.setitem(stores._conversation_locks, "k-shared", recording_lock)  # type: ignore[arg-type]
    connections = open_connections()
    conn = connections.writer
    runtime = make_runtime(connections)
    start_barrier = threading.Barrier(2)

    def _slow_send(_request: object) -> model_access.Response:
        time.sleep(0.05)  # wide enough that two unserialized turns would overlap
        return plain_response("reply")

    monkeypatch.setattr(model_access, "send", _slow_send)
    outcomes: list[client_surface.TurnOutcome] = []

    def _turn() -> None:
        start_barrier.wait(timeout=5)
        outcomes.append(
            asyncio.run(take_turn(runtime, account="a", conversation="k-shared", text="hi", create_as="chat"))
        )

    threads = [threading.Thread(target=_turn) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a turn never finished — the lock is not being released"

    assert recording_lock.acquisitions == 2
    assert recording_lock.max_held_at_once == 1
    assert [o.ok for o in outcomes] == [True, True]
    assert load(conn, "k-shared", now=0.0).next_turn_seq == 2


@pytest.mark.unit
def test_a_turn_holds_only_its_own_conversations_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole of H16's concurrency change, stated in one assertion.

    Proven from inside the turn, at the moment the model is being called —
    which is the moment that used to hold the entire process still. This
    conversation's lock is held; another conversation's is free, so a second
    person's turn could start right then. `test_store_concurrency.py` proves
    that one actually does.
    """
    observed: list[tuple[bool, bool]] = []

    def _send(_request: object) -> model_access.Response:
        observed.append((stores.conversation_lock("k-lock").locked(), stores.conversation_lock("k-other").locked()))
        return plain_response("reply")

    monkeypatch.setattr(model_access, "send", _send)
    connections = open_connections()

    asyncio.run(take_turn(make_runtime(connections), account="a", conversation="k-lock", text="hi", create_as="chat"))

    assert observed == [(True, False)]
    assert stores.conversation_lock("k-lock").locked() is False


@pytest.mark.unit
def test_the_same_conversation_name_always_gets_the_same_lock() -> None:
    """Addressed by name, never by holding the object: a second caller asking
    for the same conversation must get the lock the first one is holding, or
    they are not excluding each other at all."""
    assert stores.conversation_lock("k-a") is stores.conversation_lock("k-a")
    assert stores.conversation_lock("k-a") is not stores.conversation_lock("k-b")


# ── PERSONA-01: the voice belongs to the account, the conversation keeps it ──


@pytest.mark.unit
def test_a_conversation_is_created_in_the_voice_its_account_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    write_character("working")
    runtime = open_runtime(provider="p", model="m")
    persona_store.set_selection(runtime.conn, "a1", "working", now=0.0)

    asyncio.run(take_turn(runtime, account="a1", conversation="c1", text="hi", create_as="chat"))

    convo = load(runtime.conn, "c1", now=0.0)
    assert convo.stable_prompt == persona.render(
        persona_store.load_character(persona_store.characters_dir_from_config(), "working")
    )


@pytest.mark.unit
def test_two_accounts_on_one_runtime_get_their_own_voices(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gateway opens one `Runtime` and answers everybody through it, which
    is why the voice cannot live on the runtime."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    write_character("working")
    write_character("terse", body="You answer in one line.")
    runtime = open_runtime(provider="p", model="m")
    persona_store.set_selection(runtime.conn, "a1", "working", now=0.0)
    persona_store.set_selection(runtime.conn, "a2", "terse", now=0.0)

    asyncio.run(take_turn(runtime, account="a1", conversation="c1", text="hi", create_as="chat"))
    asyncio.run(take_turn(runtime, account="a2", conversation="c2", text="hi", create_as="chat"))

    assert "You read the code first." in load(runtime.conn, "c1", now=0.0).stable_prompt
    assert "You answer in one line." in load(runtime.conn, "c2", now=0.0).stable_prompt


@pytest.mark.unit
def test_a_child_turn_speaks_the_conversations_voice_not_the_current_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 9. The selection changes underneath a live conversation;
    the `ask` node's child must still be built from the voice that
    conversation was created with."""
    plugins_root = write_skill(tmp_path, plugin="p", skill="s1", body="Do the thing, then stop.")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(plugins_root))
    (plugins_root / "p" / "s.json").write_text('{"type": "object"}', encoding="utf-8")
    # A `compute` step ahead of the `ask`, because an `ask` reached with the
    # raw tool-call arguments cannot run at all through this door today —
    # `build_dispatch` injects a live `DispatchContext` under
    # `_sadana_memory_ctx` and `_coerce_text` JSON-dumps it. Reported as its
    # own finding; not this work item's to fix.
    (plugins_root / "p" / "init.py").write_text("def prepare(value):\n    return 'summarize this'\n", encoding="utf-8")
    write_character("working")
    write_character("pirate", body="You are now a pirate.")

    seen: list[str] = []

    def _system_text(request: object) -> str:
        """The system message as the provider receives it — one or more text
        blocks, since `context.before_send` marks the stable prefix."""
        first = request.messages[0]  # type: ignore[attr-defined]
        if first.get("role") != "system":
            return ""
        content = first["content"]
        if isinstance(content, str):
            return content
        return "".join(str(block.get("text", "")) for block in content)

    def _send(request: object) -> model_access.Response:
        system = _system_text(request)
        seen.append(system)
        if "Do the thing, then stop." in system:
            return plain_response("child done")
        if any(m.get("role") == "tool" for m in request.messages):  # type: ignore[attr-defined]
            return plain_response("parent done")
        return tool_call_response("do_it")

    monkeypatch.setattr(model_access, "send", _send)

    installed = plugins.InstalledPlugin(
        name="p",
        directory=plugins_root / "p",
        manifest=plugins.Manifest(
            name="p",
            version="0.1.0",
            description="d",
            entries=(plugins.Entry(tool="do_it", purpose="p", parameters="s.json", start="prepare"),),
            nodes=(
                plugins.Node(name="prepare", kind="compute", body="init:prepare", next="ask_step"),
                plugins.Node(name="ask_step", kind="ask", skill="s1"),
            ),
        ),
    )
    connections = open_connections()
    conn = connections.writer
    runtime = make_runtime(connections, plugin_set=plugin_dispatch.build_plugin_set((installed,)))
    persona_store.set_selection(conn, "a1", "working", now=0.0)

    asyncio.run(take_turn(runtime, account="a1", conversation="c1", text="hi", create_as="chat"))
    persona_store.set_selection(conn, "a1", "pirate", now=1.0)
    asyncio.run(take_turn(runtime, account="a1", conversation="c1", text="again", create_as=None))

    child_prompts = [system for system in seen if "Do the thing, then stop." in system]
    assert child_prompts, "the ask node never spawned a child"
    for prompt in child_prompts:
        assert prompt.startswith("You read the code first.")
        assert "You are now a pirate." not in prompt


@pytest.mark.unit
def test_a_conversation_row_written_before_stable_prompt_len_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance criterion 10. A row from before C12 added the column loads
    with `stable_prompt_len` defaulted to the whole prompt's length
    (`conversation_store.py:335`), which makes `stable_prompt` wider than the
    voice — documented in plan.md § Risks. What must not happen is that such
    a conversation stops answering."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    runtime = open_runtime(provider="p", model="m")
    asyncio.run(take_turn(runtime, account="a1", conversation="legacy", text="hi", create_as="chat"))
    runtime.conn.execute("UPDATE conversations SET stable_prompt_len = NULL WHERE key = 'legacy'")
    runtime.conn.commit()

    outcome = asyncio.run(take_turn(runtime, account="a1", conversation="legacy", text="still there?", create_as=None))

    assert outcome.ok is True
    assert outcome.answer == "reply"


@pytest.mark.unit
def test_a_disabled_plugin_is_not_offered_to_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The production path, not a composition a test wrote for itself.

    H16's deploy review found the disabled-plugin exclusion covered only by a
    test that re-implemented the filter inline, leaving
    `enabled_plugin_set` — the one caller that actually runs — untested. This
    drives that function and asserts the plugin reaches neither the catalog
    the model reads nor the tools it may call.
    """
    plugins_root = tmp_path / "plugins"
    for name in ("keeper", "switched-off"):
        directory = plugins_root / name
        directory.mkdir(parents=True)
        (directory / "s.json").write_text('{"type": "object"}')
        (directory / "plugin.toml").write_text(
            f'[plugin]\nname = "{name}"\nversion = "0.1.0"\ndescription = "d"\n\n'
            f'[[entry]]\ntool = "{name.replace("-", "_")}_tool"\npurpose = "p"\n'
            'parameters = "s.json"\nstart = "a"\n\n'
            '[[node]]\nname = "a"\nkind = "stop"\n'
        )
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    connections = open_connections()
    plugin_install.reconcile_plugin_state(connections.writer, plugins_root)
    with conversation_store.write_txn(connections.writer) as c:
        c.execute("UPDATE plugin_state SET state = 'disabled' WHERE name = 'switched-off'")

    plugin_set = client_surface.enabled_plugin_set(connections.writer)

    assert [entry.name for entry in plugin_set.catalog] == ["keeper"]
    assert [spec.name for spec in plugin_set.tool_specs] == ["keeper_tool"]
    assert "switched_off_tool" not in plugin_set.by_tool


@pytest.mark.unit
def test_a_disabled_plugin_is_excluded_even_when_its_directory_name_differs_from_its_manifest_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H24's own design-stage finding: `plugin_state` (and `disabled_names()`,
    which reads it) is keyed by a plugin's *directory* name, while
    `discover_plugins()` returns each plugin's *declared* manifest name.
    `install()` forces the two equal, but `reconcile_plugin_state` does not —
    so a directory hand-placed (or renamed) with a folder name unequal to its
    `plugin.toml` name must still be excludable by disabling it. The
    predecessor test above can't catch this: its two fixture plugins keep
    folder name and manifest name identical throughout.
    """
    plugins_root = tmp_path / "plugins"
    directory = plugins_root / "folder-name"
    directory.mkdir(parents=True)
    (directory / "s.json").write_text('{"type": "object"}')
    (directory / "plugin.toml").write_text(
        '[plugin]\nname = "declared-name"\nversion = "0.1.0"\ndescription = "d"\n\n'
        '[[entry]]\ntool = "declared_name_tool"\npurpose = "p"\n'
        'parameters = "s.json"\nstart = "a"\n\n'
        '[[node]]\nname = "a"\nkind = "stop"\n'
    )
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    connections = open_connections()
    plugin_install.reconcile_plugin_state(connections.writer, plugins_root)
    with conversation_store.write_txn(connections.writer) as c:
        c.execute("UPDATE plugin_state SET state = 'disabled' WHERE name = 'folder-name'")

    plugin_set = client_surface.enabled_plugin_set(connections.writer)

    assert [entry.name for entry in plugin_set.catalog] == []
    assert "declared_name_tool" not in plugin_set.by_tool
