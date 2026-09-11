"""One door in: every client takes a turn through here.

`docs/tasks/CLIENT-SURFACE-01-one-door-in/spec.md`. I/O: opens the SQLite
store, reads the selected character's file, scans the plugins directory — its
own file per CLAUDE.md's rule that a module touching real I/O is separate from
a block's pure ones.

Three calls. `open_runtime()` once per process, assembling everything a
turn needs that does not differ turn to turn. `open_conversation()` for a
client that must settle whether a conversation may exist before anybody has
spoken. `take_turn()` once per turn, taking
only what one client genuinely knows and another would answer differently:
who the person is, which conversation this is, what they said, and whether
that conversation may be created.

This is not a new layer. It is the body `gateway_dispatch.handle_inbound()`
already ran, with the channel envelope taken out of its mouth so a terminal
can call it too — and `subcommands/chat.py`'s copy of that body deleted. The
copy is why this module exists: it never passed `persist_pause`, so
`build_dispatch` fell back to `_noop_persist_pause` and a `wait` node reached
from the terminal was discarded with no row written and nothing shown, while
the same plugin over the same store was resumable from a webhook.

Imports no channel and no client — not `gateway`, `channel_webhook`,
`scheduling`, `editor_server`, nor anything under `subcommands/`. Channel
vocabulary points at this module, never the reverse; that direction is what
makes a further client a new file rather than an edit here (spec.md
requirement 8).

Named `Runtime`, not `Surface`: `surface` is already taken in this codebase
and means the *tool* surface (`conversation.ToolSurface`, `build_surface`,
`Conversation.tool_surface`).
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from dataclasses import dataclass, replace

from sadana import (
    config,
    conversation_store,
    memory,
    memory_store,
    model_access,
    observability,
    persona_store,
    plugin_dispatch,
    plugin_manifest,
    plugins,
)
from sadana.conversation import (
    Conversation,
    ConversationKey,
    ConversationTemplate,
    ExitReason,
    Message,
    TemplateRecipe,
    append,
    create_conversation,
    iteration_budget_from_config,
    wall_clock_budget_from_config,
)

# Serializes every `take_turn` call against every other, and against a
# caller's own direct `conn` touches (`scheduling.tick`).
# `conversation_store.open_store()`'s docstring states its connection is safe
# only "by one thread at a time, different call to call," and a channel
# adapter built on `ThreadingHTTPServer` (`channel_webhook.py`) hands each
# inbound request its own thread. Moved here from `gateway_dispatch.py`, where
# it lived while that module was the only bridge.
#
# What a turn survives: two OS threads calling `take_turn` on the same
# runtime (they serialize), and a scheduler tick landing mid-turn (same lock).
# The model round trip is bounded (`SADANA_MODEL_ACCESS_TIMEOUT_S`), so it is
# not an unbounded hold.
#
# What it does not survive, and a client author has to know both:
#  1. Two turns awaited concurrently on one event loop — this is a
#     `threading.Lock`, so the second blocks the loop instead of yielding.
#     Every caller today runs its own `asyncio.run`, and the terminal's loop
#     has exactly one caller.
#  2. A `call` node reaching its approval gate. `plugin_manifest`'s default
#     `approve` blocks on `input()` with no timeout, and neither this module
#     nor `gateway_dispatch` overrides it — so the lock is held until stdin
#     answers. Harmless for a terminal (one caller, a real person at the
#     prompt); in `cmd_gateway_run` one such message stalls every other
#     webhook thread and the scheduler tick until it is answered, which in a
#     service is never. Inherited from the bridge this module replaced, not
#     introduced here, and not fixed here either: a non-interactive client
#     needs a way to fail closed instead, which is a parameter this door does
#     not have yet. Recorded in
#     `docs/tasks/CLIENT-SURFACE-01-one-door-in/review.md`.
#
# ponytail: one module-level lock for the whole process, correct while there
# is one store path per process. A second store path, or a caller that wants
# two turns on one loop, is the signal for CLAUDE.md's own named upgrade —
# refcounted per-path connections, the shape hermes-agent arrived at after
# 11+ production incidents — not for a second lock beside this one.
# Non-reentrant: a caller holding it must not call `take_turn` inside the
# `with` block (`scheduling.tick` documents the same).
conn_lock = threading.Lock()


@dataclass(frozen=True)
class Runtime:
    """What a client holds for the life of its process: the open store, the
    scan of installed plugins, the provider and model names, and the
    observability recorder bound to that same connection.

    No persona. A character belongs to an account
    (`docs/tasks/PERSONA-01-characters-you-write/spec.md`), and one process
    serves whichever accounts arrive — `cmd_gateway_run` opens exactly one
    `Runtime` and answers everybody through it — so a process-lifetime voice
    was a per-account value held for the wrong lifetime. It is resolved in
    `_create()`, from the account, at the only moment it can still matter.

    Everything here is what `cmd_chat` and `cmd_gateway_run` each already
    built once per process, in their own copy of the same eight lines. What a
    *turn* needs and this does not hold — the dispatch closure, the tracker,
    `bind_persist`, `now` — is rebuilt inside every `take_turn`, because
    `bind_persist` snapshots budgets and `next_turn_seq` at bind time and
    `build_dispatch` closes over the conversation
    (`docs/reference/dispatch_closure_state_bug.md`).

    Holds the connection and the plugin scan, never a conversation: a
    conversation is named on each call and loaded fresh, which is this
    project's names-not-pointers rule and also what makes two clients on one
    store safe.
    """

    conn: sqlite3.Connection
    plugin_set: plugin_dispatch.PluginSet
    provider: str
    model: str
    recorder: observability.Recorder


def open_runtime(*, provider: str | None = None, model: str | None = None) -> Runtime:
    """Open the door for this process.

    `provider`/`model` are per-run overrides for a client that has its own
    flags for them (`sadana chat --provider/--model`); omitted, both come from
    config the way every other block resolves its own behaviour.

    Calls `config.load_dotenv()` itself. `cli.main()` already does, but a
    client that is not the CLI would otherwise have to know to — and it is
    idempotent and never overrides a live environment variable
    (`config.py:31`), so calling it twice costs nothing.

    The returned `Runtime` owns an open connection. A client that should not
    hold it for the life of the process closes it with
    `contextlib.closing(runtime.conn)`; a daemon that runs until killed
    (`cmd_gateway_run`) does not, exactly as before this module existed.
    """
    config.load_dotenv()
    memory_store.ensure_plugin_seeded(plugins._plugins_root())
    plugin_set = plugin_dispatch.build_plugin_set(plugin_manifest.discover_plugins())
    conn = conversation_store.open_store(conversation_store.store_path_from_config())
    memory_store.ensure_schema(conn)
    persona_store.ensure_schema(conn)
    return Runtime(
        conn=conn,
        plugin_set=plugin_set,
        provider=provider or config.env("SADANA_MODEL_ACCESS_PROVIDER", model_access.DEFAULT_PROVIDER),
        model=model or config.env("SADANA_MODEL_ACCESS_MODEL", model_access.DEFAULT_MODEL),
        recorder=observability.make_recorder(conn),
    )


@dataclass(frozen=True)
class TurnOutcome:
    """What one `take_turn` did, in the three cuts its clients need.

    `ok` — the turn completed (`ExitReason.COMPLETED`), or the resumed
    procedure reached its end without a failed node.

    `answer` — the words to show the person: the turn's own `final_text`, or a
    resumed procedure's text. `None` when the turn produced no text at all.

    `diagnostic` — never empty when something went wrong, on any path: the
    rendered `[exit_reason] detail` line for a turn, or `[failed_node] <name>`
    for a resumed procedure that died in a node. A *completed* turn populates
    it too (`[completed]`), which a successful resume does not, because
    `answer or diagnostic` is the reply expression the webhook channel already
    used and a completed turn carrying no text must keep producing a reply
    rather than an empty one.

    Three fields rather than one because neither value recovers the other: a
    `BUDGET_EXHAUSTED` turn carries a real epilogue in `answer` while `ok` is
    `False` (`conversation.py`'s EPILOGUE), so a client cannot infer the text
    from the flag, and cannot infer the flag from the text.

    Not a closed set of outcome types: the branches differ by which fields are
    populated, not by carrying different data — CLAUDE.md's own carve-out, the
    one `plugins.DagResult` already takes.
    """

    ok: bool
    answer: str | None
    diagnostic: str


def _create(
    runtime: Runtime,
    *,
    account: memory.AccountKey,
    conversation: ConversationKey,
    template_name: str,
    now: float,
) -> Conversation:
    """Build and insert one new conversation. Lock-free: both callers already
    hold `conn_lock`, which is non-reentrant.

    The system message is composed from what is remembered about `account`
    right now (MEMORY-01), the recipe's voice from the character that same
    account has selected (PERSONA-01), and the rest of the recipe from the
    runtime's plugin scan — which is the assembly `cmd_chat` and
    `handle_inbound` each used to own a copy of.

    This is the only place a persona is resolved, which is what makes the
    intent's constraint structural rather than maintained: a conversation
    takes its voice once, at birth, and every later turn reads that voice off
    the conversation itself.

    Raises `conversation_store.ConversationAlreadyExists` if the name is
    taken; callers that mean get-or-create reach here only after a failed
    load."""
    entries = memory_store.list_entries(runtime.conn, account)
    override = memory_store.get_rubric_override(runtime.conn, account)
    convo, _template = create_conversation(
        ConversationTemplate(
            name=template_name,
            recipe=TemplateRecipe(
                stable_prompt=persona_store.resolve_voice(
                    runtime.conn, account, persona_store.characters_dir_from_config()
                ),
                catalog=runtime.plugin_set.catalog,
                tool_specs=runtime.plugin_set.tool_specs,
            ),
        ),
        conversation,
        system_message=memory.system_message_for(entries, memory.default_rubric(), override),
        iteration_budget=iteration_budget_from_config(),
        wall_clock_budget=wall_clock_budget_from_config(now),
    )
    conversation_store.create(runtime.conn, convo, now=now)
    return convo


def open_conversation(
    runtime: Runtime,
    *,
    account: memory.AccountKey,
    conversation: ConversationKey,
    template_name: str | None,
) -> None:
    """Open one conversation before anybody has said anything, and settle
    whether it is allowed to exist yet.

    `template_name` names the template to create it under, or is `None` to
    require that it already exists — the same convention `take_turn`'s
    `create_as` uses, spelled the same way, so there is one rule about
    missing conversations in this module rather than one per verb. A name
    raises `conversation_store.ConversationAlreadyExists` if the name is
    taken; `None` raises `conversation_store.ConversationNotFound` if it is
    free.

    For a client that shows a prompt before it has a first line to send. A
    terminal exists from the moment it starts, so a conversation the person
    abandons at the very first prompt is still one they started
    (`test_subcommands_chat.py`'s immediate-EOF case), and `sadana chat
    --resume` has to fail on an unknown name before a prompt appears rather
    than after the person has typed. A channel needs neither: a webhook
    message is the first thing that happens, so it passes `create_as` to
    `take_turn` and lets the turn create it.

    This is the call that keeps the existence decision inside the door. The
    first cut of this work item left it in `subcommands/chat.py`, which
    probed the store directly — a client reaching around the door, outside
    `conn_lock`, for the next client to copy.
    """
    with conn_lock:
        if template_name is None:
            if not conversation_store.exists(runtime.conn, conversation):
                raise conversation_store.ConversationNotFound(conversation)
            return
        _create(
            runtime,
            account=account,
            conversation=conversation,
            template_name=template_name,
            now=time.monotonic(),
        )


async def take_turn(
    runtime: Runtime,
    *,
    account: memory.AccountKey,
    conversation: ConversationKey,
    text: str,
    create_as: str | None,
) -> TurnOutcome:
    """Run one turn for one person in one conversation, and say what happened.

    `account` names the person this conversation remembers things about, and
    `conversation` names the conversation. Both are required and neither has a
    default: this module resolves no identity of its own, from config, from
    the environment, or from a fallback, so a client can be read on its own to
    tell whose saved conversations and whose remembered facts it touches
    (spec.md requirement 3). A client that wants a default — the terminal's
    `local` — holds it itself and states it here.

    `create_as` is the template name to create the conversation under when it
    does not exist yet (`"chat"`, `"webhook"`), or `None` to require that it
    already exists. It carries both halves of that decision in one value
    because they are never useful apart: there is no way to ask for creation
    without saying what the conversation is called, and the name is observable
    — `sadana conversations` prints it and searches it.

    Raises `conversation_store.ConversationNotFound`, and nothing else, when
    `create_as is None` and the name is unknown. That is a caller error, not a
    turn outcome. Every *turn* outcome returns normally, including a provider
    failure, an exhausted budget, a failed persistence and a failed plugin
    node — a client never has to catch anything to render an answer.

    A conversation with an outstanding pause takes a different path entirely
    (GATEWAY-DAEMON-02): `text` resumes the paused plugin run, the model is
    never called, no turn happens, and the resumed text is appended as one new
    `assistant` message. This is the path `subcommands/chat.py` never had.

    Serialized by this module's `conn_lock` for the whole turn — see its
    comment for exactly what that survives. Assumes the `memory_store` schema
    is already present on `runtime.conn`; `open_runtime()` ensures it once,
    and a caller that hand-builds a `Runtime` (this module's own tests) owns
    that setup itself.
    """
    with conn_lock:
        conn = runtime.conn
        now = time.monotonic()

        pause = conversation_store.load_pause(conn, conversation_key=conversation)
        if pause is not None:
            resumed = await plugin_dispatch.resume_paused_run(conn, conversation, text)
            convo = conversation_store.load(conn, conversation, now=now)
            messages, _msg_key = append(conversation, convo.messages, Message(role="assistant", content=resumed.text))
            await asyncio.to_thread(conversation_store.save, conn, replace(convo, messages=messages), now=now)
            return TurnOutcome(
                ok=resumed.failed_node is None,
                answer=resumed.text,
                diagnostic="" if resumed.failed_node is None else f"[failed_node] {resumed.failed_node}",
            )

        try:
            convo = conversation_store.load(conn, conversation, now=now)
        except conversation_store.ConversationNotFound:
            if create_as is None:
                raise
            convo = _create(runtime, account=account, conversation=conversation, template_name=create_as, now=now)

        async def persist_pause(result: plugins.DagResult) -> None:
            conversation_store.save_pause_from_result(conn, conversation_key=conversation, result=result)

        dispatch, tracker = plugin_dispatch.build_dispatch(
            convo,
            runtime.plugin_set,
            provider=runtime.provider,
            model=runtime.model,
            now=now,
            record_turn=runtime.recorder.record_turn,
            record_plugin_run=runtime.recorder.record_plugin_run,
            memory_context=memory_store.DispatchContext(account_key=account, conn=conn),
            persist_pause=persist_pause,
        )
        result, convo = await plugin_dispatch.take_turn_and_reconcile(
            convo,
            dispatch,
            tracker,
            user_input=text,
            provider=runtime.provider,
            model=runtime.model,
            now=now,
            persist=conversation_store.bind_persist(conn, convo, now=now),
            record_turn=runtime.recorder.record_turn,
        )
        await asyncio.to_thread(conversation_store.save, conn, convo, now=now)
        return TurnOutcome(
            ok=result.exit_reason == ExitReason.COMPLETED,
            answer=result.final_text,
            diagnostic=f"[{result.exit_reason.value}] {result.detail or ''}",
        )
