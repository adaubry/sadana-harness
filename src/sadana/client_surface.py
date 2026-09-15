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
import logging
import sqlite3
import time
from dataclasses import dataclass, replace

from sadana import (
    builtin_seed,
    config,
    conversation_store,
    memory,
    memory_store,
    model_access,
    observability,
    persona_store,
    plugin_dispatch,
    plugin_install,
    plugin_manifest,
    plugins,
    stores,
)
from sadana.conversation import (
    Conversation,
    ConversationKey,
    ConversationTemplate,
    ExitReason,
    Message,
    TemplateRecipe,
    TurnKey,
    TurnObserver,
    append,
    create_conversation,
    iteration_budget_from_config,
    wall_clock_budget_from_config,
)

logger = logging.getLogger(__name__)

# H16 replaced this module's one process-wide lock with a lock per
# conversation (`stores.conversation_lock`) and a write lock held only inside a
# transaction (`conversation_store.write_txn`). The old one was held for a
# whole turn — including the model round trip — so a second person's message,
# and even a plain listing, waited behind somebody else's sentence. That is
# correct for one person at a terminal and wrong for an organisation sharing a
# box, which is what `docs/reference/console_fit_plan.md` §2.1 reverses.
#
# What this survives: two turns on two conversations in two threads, and a
# reader listing conversations while a turn runs. What it does not: two turns
# on one conversation on one event loop; a call node at its approval gate (H18
# removes this).
#
# The second of those is worth spelling out, because it is unchanged and still
# bites: `plugin_manifest`'s default `approve` blocks on `input()` with no
# timeout, so a `call` node reaching its gate holds its conversation's lock
# until stdin answers. That is now one conversation rather than the whole
# process — every other conversation keeps working — but for the conversation
# itself, in a service, it is still forever. A non-interactive client needs a
# way to fail closed, which is a parameter this door does not have yet.


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

    connections: stores.Connections
    plugin_set: plugin_dispatch.PluginSet
    provider: str
    model: str
    recorder: observability.Recorder

    @property
    def conn(self) -> sqlite3.Connection:
        """The process's one writer.

        A property rather than a field since H16, so that every caller written
        against the old shape keeps working while the reader/writer split
        arrives underneath them. A caller that only reads should ask for
        `connections.reader()` instead and not queue behind a turn; a caller
        inside a turn uses this, as before.
        """
        return self.connections.writer


def open_runtime(*, provider: str | None = None, model: str | None = None) -> Runtime:
    """Open the door for this process.

    `provider`/`model` are per-run overrides for a client that has its own
    flags for them (`sadana chat --provider/--model`); omitted, both come from
    config the way every other block resolves its own behaviour.

    Calls `config.load_dotenv()` itself. `cli.main()` already does, but a
    client that is not the CLI would otherwise have to know to — and it is
    idempotent and never overrides a live environment variable
    (`config.py:31`), so calling it twice costs nothing.

    The returned `Runtime` owns open connections. A client that should not
    hold them for the life of the process closes the writer with
    `contextlib.closing(runtime.conn)`; a daemon that runs until killed
    (`cmd_gateway_run`) does not, exactly as before this module existed. The
    thread-local readers are deliberately not closeable as a set — see
    `stores.Connections.reader`.
    """
    config.load_dotenv()
    builtin_seed.seed_all(plugins._plugins_root())
    # Connections first: its constructor ensures every schema and reconciles
    # `plugin_state` against the directory that was just seeded, and
    # `_enabled_plugin_set` reads that table to know what is switched off.
    connections = stores.Connections(conversation_store.store_path_from_config())
    _adopt_scheduled_memories(connections.writer)
    _adopt_scheduled_triggers(connections.writer)
    return Runtime(
        connections=connections,
        plugin_set=_enabled_plugin_set(connections.writer),
        provider=provider or config.env("SADANA_MODEL_ACCESS_PROVIDER", model_access.DEFAULT_PROVIDER),
        model=model or config.env("SADANA_MODEL_ACCESS_MODEL", model_access.DEFAULT_MODEL),
        recorder=observability.make_recorder(connections.writer),
    )


def _enabled_plugin_set(conn: sqlite3.Connection) -> plugin_dispatch.PluginSet:
    """Every installed plugin except the ones somebody switched off.

    The filter is here, at the one caller that has a store, rather than inside
    `build_plugin_set` — which stays a function of its arguments and needs no
    database. `plugin_install` owns `plugin_state` and answers which names are
    disabled; this is the only place the two meet.
    """
    disabled = plugin_install.disabled_names(conn)
    return plugin_dispatch.build_plugin_set(
        p for p in plugin_manifest.discover_plugins() if p.manifest.name not in disabled
    )


def _adopt_scheduled_memories(conn: sqlite3.Connection) -> None:
    """Move what scheduled runs remembered under their own names onto the
    owner, once (PERSONA-02 requirement 4). A no-op on every start after the
    first, which is why it can live on the open path instead of in a command
    somebody has to remember to run.

    What it survives: its own failure. A store that cannot be migrated — a
    locked database, a shape nobody anticipated — logs and lets the process
    start anyway. The alternative, refusing to open, turns a data-shape
    surprise into an outage on every surface at once, and this is not
    something a turn depends on. The cost is that a partial move can go
    unnoticed until somebody looks for a fact that is in neither place
    (spec.md § Concerns).
    """
    try:
        moved = memory_store.adopt_scheduled_memories(conn, memory.owner_account())
    except sqlite3.Error:
        logger.warning("could not adopt scheduled memories into the owner's account", exc_info=True)
        return
    if moved:
        logger.info("adopted %d remembered entries from scheduled runs into the owner's account", moved)


def _adopt_scheduled_triggers(conn: sqlite3.Connection) -> None:
    """H27's own `_adopt_scheduled_memories`: move every legacy
    `scheduled_triggers` row into `schedules`, once, under the owner's
    account. Idempotent (a second call finds nothing left to move) and
    best-effort — a store this can't migrate still opens, on the same
    reasoning `_adopt_scheduled_memories` already gives: a data-shape
    surprise here should not turn into an outage on every surface at once."""
    try:
        moved = conversation_store.adopt_scheduled_triggers(conn, owner=memory.owner_account(), now=time.time())
    except sqlite3.Error:
        logger.warning("could not adopt scheduled triggers into the owner's account", exc_info=True)
        return
    if moved:
        logger.info("adopted %d scheduled trigger(s) into the schedules table", moved)


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
    id: str | None = None,
) -> Conversation:
    """Build and insert one new conversation. Lock-free: both callers already
    hold the conversation's own lock, which is non-reentrant.

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
    load.

    `id` defaults to `None` (a freshly minted one, every existing caller).
    The door (H20) is the only caller that ever passes one — its own
    pre-minted `conversation`, so the created row's `id` equals its `key`
    (spec.md's "Decisions already made": "key == id for console-created
    conversations")."""
    # Every read here goes through this thread's own read-only connection, not
    # the writer. Sharing the writer for reads was H16's own defect, caught by
    # its deploy review: a SQLite transaction belongs to a *connection*, so a
    # read issued on the writer while another thread sits inside `write_txn`
    # runs inside that thread's transaction and sees rows it may still roll
    # back. The per-conversation lock does not help — it partitions
    # `conversations`, and these tables are shared across every conversation an
    # account has. A fact that never existed would otherwise be composed into a
    # system prompt that is then byte-stable for the life of the conversation.
    reader = runtime.connections.reader()
    entries = memory_store.list_entries(reader, account)
    override = memory_store.get_rubric_override(reader, account)
    convo, _template = create_conversation(
        ConversationTemplate(
            name=template_name,
            recipe=TemplateRecipe(
                stable_prompt=persona_store.resolve_voice(reader, account, persona_store.characters_dir_from_config()),
                catalog=runtime.plugin_set.catalog,
                tool_specs=runtime.plugin_set.tool_specs,
            ),
        ),
        conversation,
        system_message=memory.system_message_for(entries, memory.default_rubric(), override),
        iteration_budget=iteration_budget_from_config(),
        wall_clock_budget=wall_clock_budget_from_config(now),
    )
    # The character's *name*, recorded beside the conversation at the one
    # moment it is resolved (H16 requirement 6). `resolve_voice` already read
    # the selection to render the text; this reads it again to store what it
    # was called, because the rendered text is not something to store and the
    # name is. Two indexed lookups where one would do, and the alternative is a
    # function returning both halves for one caller.
    conversation_store.create(
        runtime.conn,
        convo,
        now=now,
        account_key=account,
        agent=persona_store.get_selection(reader, account),
        id=id,
    )
    return convo


def open_conversation(
    runtime: Runtime,
    *,
    account: memory.AccountKey,
    conversation: ConversationKey,
    template_name: str | None,
    id: str | None = None,
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
    any lock, for the next client to copy.

    `id`, like `_create`'s own, defaults to `None` and is passed straight
    through — see `_create`'s docstring.
    """
    with stores.conversation_lock(conversation):
        if template_name is None:
            if not conversation_store.exists(runtime.connections.reader(), conversation):
                raise conversation_store.ConversationNotFound(conversation)
            return
        _create(
            runtime,
            account=account,
            conversation=conversation,
            template_name=template_name,
            now=time.monotonic(),
            id=id,
        )


async def take_turn(
    runtime: Runtime,
    *,
    account: memory.AccountKey,
    conversation: ConversationKey,
    text: str,
    create_as: str | None,
    approve: plugins.ApproveFn | None = None,
    observer: TurnObserver | None = None,
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

    Serialized for the whole turn by *this conversation's* lock — see this
    module's own comment above for exactly what that survives. Another
    conversation's turn runs at the same time; a reader waits for neither.

    Reads go through this thread's own read-only connection and writes through
    the process's one writer. That split is not an optimisation: a SQLite
    transaction belongs to a connection, so reading on the writer while another
    thread is inside `write_txn` reads *that thread's uncommitted rows*. See
    `_create` for the case that made it concrete. Assumes the `memory_store` schema
    is already present on `runtime.conn`; `open_runtime()` ensures it once,
    and a caller that hand-builds a `Runtime` (this module's own tests) owns
    that setup itself.

    `observer` (H21, default `None`): a one-line passthrough to
    `plugin_dispatch.take_turn_and_reconcile`/`conversation.run_turn` — no
    logic of its own here. Every existing caller (`subcommands/chat.py`,
    `gateway_dispatch.handle_inbound`, `eval_harness.py`'s indirect path)
    keeps compiling and passing with no change, since it is a new,
    defaulted, trailing parameter. `runtime.recorder`'s three H21 fields
    (`record_turn_started`, `record_plugin_run_started`, `record_node`) are
    threaded into `build_dispatch` unconditionally, alongside the two
    OBSERVABILITY-01 already passed — a caller that built its own
    `Runtime` with `observability.make_recorder`'s real recorder gets live
    records with no opt-in of its own, the same way it already gets
    `record_turn`/`record_plugin_run`.
    """
    with stores.conversation_lock(conversation):
        conn = runtime.conn
        now = time.monotonic()
        # H18. The client's choice, resolved once: an explicit `approve`
        # (a TTY) is used as-is; no `approve` at all (everything else —
        # a webhook, a scheduled trigger, anything that never heard of
        # approval) gets the parking sentinel, never the interactive
        # default — a non-interactive caller must never block on `input()`.
        resolved_approve = approve if approve is not None else plugin_manifest.parking_approve

        pause = conversation_store.load_pause(runtime.connections.reader(), conversation_key=conversation)
        if pause is not None:
            if pause.kind == "call":
                # A call pause is answered through the door's actions, not
                # by texting the conversation — the message is stored, and
                # no run happens.
                convo = conversation_store.load(runtime.connections.reader(), conversation, now=now)
                messages, _msg_key = append(conversation, convo.messages, Message(role="user", content=text))
                await asyncio.to_thread(conversation_store.save, conn, replace(convo, messages=messages), now=now)
                approval = conversation_store.load_waiting_approval(
                    runtime.connections.reader(), conversation_key=conversation
                )
                assert approval is not None, "a call-kind pause with no waiting approval row"
                return TurnOutcome(ok=False, answer=None, diagnostic=f"[waiting_for_approval] {approval.id}")
            resumed = await plugin_dispatch.resume_paused_run(
                conn, conversation, decision="answer", state="answered", payload=text, approve=resolved_approve
            )
            convo = conversation_store.load(runtime.connections.reader(), conversation, now=now)
            messages, _msg_key = append(conversation, convo.messages, Message(role="assistant", content=resumed.text))
            await asyncio.to_thread(conversation_store.save, conn, replace(convo, messages=messages), now=now)
            return TurnOutcome(
                ok=resumed.failed_node is None,
                answer=resumed.text,
                diagnostic="" if resumed.failed_node is None else f"[failed_node] {resumed.failed_node}",
            )

        try:
            convo = conversation_store.load(runtime.connections.reader(), conversation, now=now)
        except conversation_store.ConversationNotFound:
            if create_as is None:
                raise
            convo = _create(runtime, account=account, conversation=conversation, template_name=create_as, now=now)

        async def persist_pause(turn_key: TurnKey, seq_in_turn: int, result: plugins.DagResult) -> None:
            # The pause records the run it belonged to, so the resumed half
            # writes into the same output directory the paused half used
            # (`docs/tasks/ARTIFACT-STORE-01-somewhere-to-put-what-a-plugin
            # -makes/spec.md`). `build_dispatch` hands over the same pair it
            # just gave `record_plugin_run`.
            conversation_store.save_pause_from_result(
                conn,
                conversation_key=conversation,
                result=result,
                turn_seq=turn_key.turn_seq,
                seq_in_turn=seq_in_turn,
            )

        dispatch, tracker = plugin_dispatch.build_dispatch(
            convo,
            runtime.plugin_set,
            provider=runtime.provider,
            model=runtime.model,
            now=now,
            record_turn=runtime.recorder.record_turn,
            record_plugin_run=runtime.recorder.record_plugin_run,
            memory_context=memory_store.DispatchContext(account_key=account, conn=conn, conversation_key=conversation),
            record_turn_started=runtime.recorder.record_turn_started,
            record_plugin_run_started=runtime.recorder.record_plugin_run_started,
            record_node=runtime.recorder.record_node,
            persist_pause=persist_pause,
            approve=resolved_approve,
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
            observer=observer,
        )
        await asyncio.to_thread(conversation_store.save, conn, convo, now=now)
        return TurnOutcome(
            ok=result.exit_reason == ExitReason.COMPLETED,
            answer=result.final_text,
            diagnostic=f"[{result.exit_reason.value}] {result.detail or ''}",
        )
