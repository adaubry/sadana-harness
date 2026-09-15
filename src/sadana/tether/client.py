"""The tether's connection loop: one outbound WebSocket, on its own
`daemon=True` thread with its own `asyncio` event loop — the same shape
`scheduling.run_tick_loop` already established for a background loop that
needs no coordination with `gateway_daemon.run()`'s own shutdown sequence.

`docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md`. `docs/console/
wire.md` §5 is the protocol implemented here, verbatim.

**What this loop survives**: the relay restarting, the network dropping,
and a handler that raises while answering a `request` (the frame gets
`INTERNAL`, the loop lives — `router.handle()` already never raises).
**What it does not survive, and does not try to**: two tethers running in
one process (nothing here enforces that; the daemon's own single-instance
lock, `gateway_daemon.py`, already forbids two processes) — or, by design,
telling the console its own connection is "degraded"/"offline". That
judgment is the console's, watching the heartbeats it receives; this loop
only ever reports `disconnected | connecting | connected`.

`hello`'s `capabilities` field is computed fresh, at connect time, from
`capabilities.declared()` — never a value fixed at process start. This is
the property the console's own capability gating rests on: a box that
reconnects after a later work item (H14) lands advertises the new names
with no code change here.

The outbound task drains two sources under two different rules. The ledger
tail is ordered and must never skip a cursor within one connection — it
starts fresh at this connection's own `ledger_head` every time (never a
value remembered across a reconnect: the console's own `GET /changes?since=`
catches the gap, seeded by `hello`'s `ledger_head`, per spec.md § Design).
The ephemeral queue is lossy by design — a `message.delta` that arrives
after the turn it belonged to has finished is worse than one that never
arrives — so it is drained and *discarded*, not sent, at the moment a
connection becomes `connected`, before anything queued during the gap
before this connection existed can be delivered late.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import json
import logging
import queue
import random
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Protocol

import websockets

from sadana import __version__, config, ledger
from sadana.door import capabilities, grammar
from sadana.door.events import ephemeral_queue
from sadana.door.router import DoorContext, handle
from sadana.tether import backoff, frames
from sadana.tether.identity import Identity
from sadana.tether.keys import KeyPair

logger = logging.getLogger("sadana.tether")

SendFn = Callable[[dict], Awaitable[None]]

#: How long a connection must stay up before a subsequent drop resets the
#: backoff attempt counter to zero.
_RESET_AFTER_CONNECTED_SECONDS = 60.0
_HEARTBEAT_INTERVAL_SECONDS = 15.0
_OUTBOUND_POLL_SECONDS = 0.25
_LEDGER_PAGE_SIZE = 200


# ── connection state, read by door/nouns/harness.py::get_harness() ─────────


@dataclass(frozen=True)
class TetherState:
    status: str  # "disconnected" | "connecting" | "connected"
    connected_at: float | None = None
    last_heartbeat_at: float | None = None
    attempt: int = 0


_state_lock = threading.Lock()
#: Mirrors `operations.py`'s own `_executor`/`_executor_lock` pattern: one
#: process-lifetime value, meaningless until `start()` is ever called, never
#: a registry — the daemon's own single-instance lock already forbids a
#: second tether in this process.
_state = TetherState(status="disconnected")


def state() -> TetherState:
    with _state_lock:
        return _state


def _set_state(**changes: object) -> None:
    global _state
    with _state_lock:
        _state = replace(_state, **changes)  # type: ignore[arg-type]


# ── stopping the loop (H30's `deregister`) ─────────────────────────────────

_stop_event = threading.Event()
_lifecycle_lock = threading.Lock()
_active_loop: asyncio.AbstractEventLoop | None = None
#: The live `websockets` connection, typed loosely (`object`, not
#: `_WireSocket`) because `stop()` needs `.close()`, which that narrower
#: handshake-only protocol does not declare.
_active_ws: object | None = None


def stop() -> None:
    """Signals the loop to stop reconnecting, and closes a live connection
    immediately rather than waiting for it to drop on its own. Idempotent —
    safe on a tether that was never started, or one already stopped.

    One documented gap: if the loop is currently backed off, *waiting* to
    reconnect (no live connection to close), `run_forever` only notices the
    stop request the next time that wait ends — up to 60s later, the
    backoff cap. Deregistering does not need to be instantaneous, only for
    the box to eventually stop trying; closing an active connection
    immediately is what the common case (an actually-connected tether)
    gets, and it is the case that matters."""
    _stop_event.set()
    with _lifecycle_lock:
        loop, ws = _active_loop, _active_ws
    if loop is not None and ws is not None:

        def _close_it() -> None:
            asyncio.ensure_future(ws.close(), loop=loop)  # type: ignore[attr-defined]

        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(_close_it)


# ── the ledger tail ──────────────────────────────────────────────────────


def _change_to_event(change: ledger.Change, *, harness_id: str) -> dict[str, object]:
    """`docs/console/wire.md` §3's `ResourceChangedEvent`, without
    `tenant_id` (the console's own ingest resolves that from the harness
    that delivered the event). `search_doc` is omitted here — deriving it
    needs the noun registry `door/nouns/harness.py::get_changes()` already
    has for `GET /v1/changes`; nothing in this item's own acceptance
    criteria requires the live push to carry it, and the console's own
    `GET /changes` catch-up (which does render it) covers the gap."""
    event: dict[str, object] = {
        "kind": "deleted" if change.kind == "deleted" else "changed",
        "harness_id": harness_id,
        "noun": change.noun,
        "id": change.id,
        "updated_at": grammar.render_ts(change.at),
    }
    if change.state is not None:
        event["state"] = change.state
    if change.version is not None:
        event["version"] = change.version
    return event


async def drain_ledger_tail(
    ctx: DoorContext, cursor: int, *, harness_id: str, send: SendFn, limit: int = _LEDGER_PAGE_SIZE
) -> int:
    """One pass: every change after `cursor`, oldest first, sent exactly
    once as an `event` frame. Returns the cursor to resume from next time —
    never skipping one, never repeating one already sent."""
    changes = ledger.changes_since(ctx.conns.reader(), cursor, limit)
    for change in changes:
        event_frame = frames.Event(cursor=change.cursor, event=_change_to_event(change, harness_id=harness_id))
        await send(frames.encode(event_frame))
        cursor = change.cursor
    return cursor


# ── the ephemeral queue ──────────────────────────────────────────────────


def discard_ephemeral_backlog() -> int:
    """Empties `ephemeral_queue` without sending anything. Called once, on
    every transition into `connected` — before anything queued during the
    gap before this connection existed can be delivered late. Returns how
    many frames were discarded, for a caller that wants to log it."""
    discarded = 0
    with contextlib.suppress(queue.Empty):
        while True:
            ephemeral_queue.get_nowait()
            discarded += 1
    return discarded


async def drain_ephemeral_queue_live(send: SendFn) -> None:
    """Sends whatever is queued right now, live. The queued value is
    already the full wire frame (`door/nouns/messages.py`'s own
    `events.push()` call already builds `{"type": "ephemeral", ...}`), so
    this never re-wraps it — H21's own payload rides unchanged."""
    with contextlib.suppress(queue.Empty):
        while True:
            frame = ephemeral_queue.get_nowait()
            await send(frame)


# ── inbound: a request frame answered by a response frame, by id ──────────


async def dispatch_request(
    ctx: DoorContext, door_pool: ThreadPoolExecutor, request: frames.Request, send: SendFn
) -> None:
    """`request` -> `loop.run_in_executor(door_pool, door.handle, ...)` ->
    `response`, by id. Off the tether's own event loop, so one slow turn's
    dispatch never delays a concurrent fast list's. `router.handle()` never
    raises (its own docstring), so nothing here needs a `try`/`except` of
    its own around it."""
    door_request = frames.request_to_door_request(request)
    loop = asyncio.get_running_loop()
    door_response = await loop.run_in_executor(door_pool, functools.partial(handle, door_request, ctx=ctx))
    response = frames.door_response_to_response(request.id, door_response)
    await send(frames.encode(response))


# ── the handshake ────────────────────────────────────────────────────────


def build_hello(harness_id: str, ledger_head: int) -> frames.Hello:
    """`capabilities.declared()` is called here, fresh, every time — never
    cached — which is the property that makes a reconnect after a later
    capability lands advertise it with no code change."""
    return frames.Hello(
        harness_id=harness_id, version=__version__, capabilities=capabilities.declared(), ledger_head=ledger_head
    )


class _WireSocket(Protocol):
    """The minimal async duck-type `perform_handshake` needs from a
    WebSocket connection — `recv() -> str` and `send(str) -> None` — so a
    unit test can hand in a fake without a real socket."""

    async def recv(self, decode: bool | None = None) -> str | bytes: ...

    async def send(self, message: str) -> None: ...


async def perform_handshake(ws: _WireSocket, key: KeyPair, *, harness_id: str, ledger_head: int) -> bool:
    """Receive `challenge`, sign it, send `challenge_response`, require
    `welcome` (anything else: `False` — the caller closes and backs off),
    then send `hello`. Returns whether the handshake succeeded."""
    raw = await ws.recv()
    challenge = frames.decode(json.loads(raw))
    if not isinstance(challenge, frames.Challenge):
        return False

    signature = key.sign(challenge.nonce.encode("utf-8"))
    response = frames.ChallengeResponse(harness_id=harness_id, signature=base64.b64encode(signature).decode("ascii"))
    await ws.send(json.dumps(frames.encode(response)))

    raw = await ws.recv()
    reply = frames.decode(json.loads(raw))
    if not isinstance(reply, frames.Welcome):
        return False

    await ws.send(json.dumps(frames.encode(build_hello(harness_id, ledger_head))))
    return True


# ── the connection loop proper — not unit-tested; proven by
#    scripts/prove_tether_e2e.py against a real (local) socket, per
#    CLAUDE.md's own rule that a first real external round trip is proven
#    outside `make test` ──────────────────────────────────────────────────


_WS_SCHEMES = {"http": "ws", "https": "wss"}


def _connect_url(relay_url: str) -> str:
    """`<relay>/enroll` is a plain HTTP(S) POST; `<relay>/connect` is the
    same base with its scheme swapped for WebSocket's — one `relay_url`
    serves both, the way a real relay behind a reverse proxy would put both
    on the same host:port. `scripts/test_relay.py` answers both from the
    exact same listening socket, so this is provable today, not aspirational."""
    scheme, sep, rest = relay_url.partition("://")
    return f"{_WS_SCHEMES.get(scheme, scheme)}{sep}{rest}".rstrip("/") + "/connect"


async def _run_connection(
    ctx: DoorContext, identity: Identity, key: KeyPair, door_pool: ThreadPoolExecutor
) -> tuple[bool, float | None]:
    """One connection attempt, start to finish. Returns `(connected_at_least_once,
    retry_after)` — `retry_after` is set only when the relay answered `/connect`
    with `429`, in which case the caller sleeps exactly that and nothing else."""
    _set_state(status="connecting")
    url = _connect_url(identity.relay_url)
    headers = {"X-Sadana-Harness": identity.harness_id}
    connected_at: float | None = None

    try:
        async with websockets.connect(url, additional_headers=headers) as ws:
            send_lock = asyncio.Lock()

            async def send(frame: dict[str, object]) -> None:
                async with send_lock:
                    await ws.send(json.dumps(frame))

            ledger_head_now = ledger.ledger_head(ctx.conns.reader())
            if not await perform_handshake(ws, key, harness_id=identity.harness_id, ledger_head=ledger_head_now):
                return False, None

            discard_ephemeral_backlog()
            connected_at = time.time()
            _set_state(status="connected", connected_at=connected_at, attempt=0)
            global _active_loop, _active_ws
            with _lifecycle_lock:
                _active_loop, _active_ws = asyncio.get_running_loop(), ws

            async def heartbeat_task() -> None:
                while True:
                    await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
                    await send(frames.encode(frames.Heartbeat(at=time.time())))
                    _set_state(last_heartbeat_at=time.time())

            async def inbound_task() -> None:
                async for raw in ws:
                    decoded = frames.decode(json.loads(raw))
                    if isinstance(decoded, frames.Request):
                        asyncio.ensure_future(dispatch_request(ctx, door_pool, decoded, send))
                    else:
                        reason = decoded.reason if isinstance(decoded, frames.UnknownFrame) else "unexpected frame"
                        await send(frames.encode(frames.Error(code="VALIDATION", detail=reason)))

            async def outbound_task() -> None:
                cursor = ledger_head_now
                while True:
                    try:
                        cursor = await drain_ledger_tail(ctx, cursor, harness_id=identity.harness_id, send=send)
                        await drain_ephemeral_queue_live(send)
                    except Exception:  # noqa: BLE001 -- a send failing here never kills the loop
                        logger.warning("tether outbound drain failed; retrying next pass", exc_info=True)
                    await asyncio.sleep(_OUTBOUND_POLL_SECONDS)

            async with asyncio.TaskGroup() as tg:
                tg.create_task(heartbeat_task())
                tg.create_task(inbound_task())
                tg.create_task(outbound_task())
    finally:
        # Any exception here — `ConnectionClosed`, the `ExceptionGroup`
        # `TaskGroup` raises when one of the three tasks above fails, an
        # `InvalidStatus` from `websockets.connect` itself — propagates to
        # `run_forever`'s own `try`/`except`, which is the one place that
        # decides backoff. Nothing here needs its own `except`.
        with _lifecycle_lock:
            _active_loop, _active_ws = None, None
        _set_state(status="disconnected")

    lasted = (time.time() - connected_at) if connected_at is not None else 0.0
    return lasted >= _RESET_AFTER_CONNECTED_SECONDS, None


async def run_forever(ctx: DoorContext, identity: Identity, key: KeyPair) -> None:
    """Blocks until `stop()` is called, reconnecting with full-jitter
    backoff. Meant to run on its own thread — see `start()`."""
    door_pool = ThreadPoolExecutor(
        max_workers=config.env_int("SADANA_TETHER_DOOR_WORKERS", 8), thread_name_prefix="tether-door"
    )
    attempt = 0
    rng = random.Random()
    while not _stop_event.is_set():
        try:
            reset, retry_after = await _run_connection(ctx, identity, key, door_pool)
        except websockets.exceptions.InvalidStatus as exc:
            if exc.response.status_code == 429:
                retry_after_header = exc.response.headers.get("Retry-After")
                retry_after = float(retry_after_header) if retry_after_header else 1.0
                reset = False
            else:
                reset, retry_after = False, None
        except Exception:  # noqa: BLE001 -- the network dropping is survived, never a crash
            logger.warning("tether connection attempt failed", exc_info=True)
            reset, retry_after = False, None

        attempt = 0 if reset else attempt + 1
        _set_state(attempt=attempt)
        if retry_after is not None:
            delay = retry_after  # obeyed exactly, never blended with backoff
            logger.info("tether: relay answered 429, sleeping exactly %.1fs (Retry-After)", delay)
        else:
            delay = backoff.next_delay(attempt, rng=rng)
            logger.info("tether: reconnecting in %.2fs (attempt %d, full jitter)", delay, attempt)
        await asyncio.sleep(delay)


def start(ctx: DoorContext, identity: Identity, key: KeyPair) -> threading.Thread:
    """Starts the tether on its own `daemon=True` thread with its own event
    loop — the `scheduling.run_tick_loop` precedent. Clears a prior `stop()`
    first: the daemon's own single-instance lock forbids two *processes*
    tethering at once, but nothing stops a single process (a test, mainly)
    from stopping and restarting its own within one lifetime."""
    _stop_event.clear()

    def _run() -> None:
        asyncio.run(run_forever(ctx, identity, key))

    thread = threading.Thread(target=_run, daemon=True, name="sadana-tether")
    thread.start()
    return thread
