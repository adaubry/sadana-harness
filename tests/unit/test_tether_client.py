"""`tether/client.py` — the pieces that don't need a real socket: state,
the ledger tail, the ephemeral queue's discard-on-reconnect asymmetry,
inbound dispatch through a real (in-process) door, `hello`'s freshness, and
the handshake against a fake wire. The connection loop itself
(`run_forever`/`_run_connection`) is proven by `scripts/prove_tether_e2e.py`
against a real socket, per this project's own rule that a first real
external round trip is proven outside `make test`.

No `pytest-asyncio` here — this project's existing convention for testing
`async def` code (`test_door_operations.py`) is a plain test function
wrapping the call in `asyncio.run(...)`, not a second test-time dependency.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sadana import ids, ledger, stores
from sadana.conversation_store import write_txn
from sadana.door import auth, capabilities
from sadana.door.events import ephemeral_queue
from sadana.door.nouns import harness as harness_noun
from sadana.door.router import DoorContext
from sadana.tether import client, frames
from sadana.tether.keys import generate as generate_key
from sadana.tether.keys import verify as verify_signature

_HARNESS_ID = "hrn_test"


@pytest.fixture(autouse=True)
def _clean_ephemeral_queue():  # type: ignore[no-untyped-def]
    """`door.events.ephemeral_queue` is process-global (H21). Every test in
    this file starts and ends with it empty, so nothing here leaks into —
    or is polluted by — any other test that touches the same queue."""
    client.discard_ephemeral_backlog()
    yield
    client.discard_ephemeral_backlog()


def _conns(tmp_path: Path) -> stores.Connections:
    return stores.Connections(tmp_path / "t.db")


def _ctx(conns: stores.Connections, tmp_path: Path) -> tuple[DoorContext, str]:
    """Returns the context and a real bearer token good against it —
    `dispatch_request`'s own test needs a request a real door will actually
    answer 200 to, not just route."""
    private_pem, jwk = auth.generate_dev_keypair("k1")
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps(auth.jwks_document(jwk)))
    ctx = DoorContext(
        conns=conns,
        runtime=auth.BoxIdentity(harness_id=_HARNESS_ID, org=None),
        verifier=auth.Verifier(auth.JwksSource(path=jwks_path)),
        capabilities=capabilities.declared(),
        nouns={"harness": harness_noun},
        clock=time.time,
    )
    token = auth.mint_token(private_pem, "k1", sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["harness:read"])
    return ctx, token


class _Collector:
    """A fake async `send`: collects every frame it is given, in order."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def __call__(self, frame: dict) -> None:
        self.sent.append(frame)


# ── state ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_state_defaults_to_disconnected() -> None:
    assert client.state().status in ("disconnected", "connecting", "connected")


@pytest.mark.unit
def test_stop_is_safe_with_nothing_active() -> None:
    client.stop()  # must not raise when never started
    client.stop()  # or when called twice
    client._stop_event.clear()  # leave the module-global flag as this test found it


# ── the ledger tail ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_change_to_event_folds_created_into_changed() -> None:
    row = ledger.Change(cursor=1, noun="harness", id="hrn_x", kind="created", state="running", version=1, at=5.0)
    event = client._change_to_event(row, harness_id="hrn_box")
    assert event["kind"] == "changed"
    assert event["harness_id"] == "hrn_box"
    assert event["state"] == "running"
    assert event["version"] == 1


@pytest.mark.unit
def test_change_to_event_keeps_deleted_and_omits_absent_fields() -> None:
    row = ledger.Change(cursor=2, noun="harness", id="hrn_x", kind="deleted", state=None, version=None, at=5.0)
    event = client._change_to_event(row, harness_id="hrn_box")
    assert event["kind"] == "deleted"
    assert "state" not in event
    assert "version" not in event


@pytest.mark.unit
def test_drain_ledger_tail_sends_every_row_once_in_order(tmp_path: Path) -> None:
    conns = _conns(tmp_path)
    ctx, _token = _ctx(conns, tmp_path)
    with write_txn(conns.writer) as c:
        for i in range(5):
            ledger.record_change(
                c, noun="harness", id=ids.make_id("hrn"), kind="changed", state=None, version=i, at=float(i)
            )

    collector = _Collector()
    cursor = asyncio.run(client.drain_ledger_tail(ctx, 0, harness_id=_HARNESS_ID, send=collector))

    assert cursor == 5
    versions = [f["event"]["version"] for f in collector.sent]
    assert versions == [0, 1, 2, 3, 4]
    assert all(f["type"] == "event" for f in collector.sent)


@pytest.mark.unit
def test_drain_ledger_tail_resumes_from_the_given_cursor_never_repeating(tmp_path: Path) -> None:
    conns = _conns(tmp_path)
    ctx, _token = _ctx(conns, tmp_path)
    with write_txn(conns.writer) as c:
        for i in range(3):
            ledger.record_change(
                c, noun="harness", id=ids.make_id("hrn"), kind="changed", state=None, version=i, at=float(i)
            )

    collector = _Collector()
    cursor = asyncio.run(client.drain_ledger_tail(ctx, 2, harness_id=_HARNESS_ID, send=collector))

    assert cursor == 3
    assert len(collector.sent) == 1
    assert collector.sent[0]["event"]["version"] == 2


# ── the ephemeral queue's discard-on-reconnect asymmetry ────────────────


@pytest.mark.unit
def test_discard_ephemeral_backlog_empties_the_queue() -> None:
    ephemeral_queue.put_nowait({"type": "ephemeral", "name": "message.delta", "harness_id": "hrn_box", "data": {}})
    ephemeral_queue.put_nowait({"type": "ephemeral", "name": "message.delta", "harness_id": "hrn_box", "data": {}})

    discarded = client.discard_ephemeral_backlog()

    assert discarded == 2
    assert ephemeral_queue.empty()


@pytest.mark.unit
def test_drain_ephemeral_queue_live_sends_the_raw_frame_unwrapped() -> None:
    payload = {"type": "ephemeral", "name": "message.delta", "harness_id": "hrn_box", "data": {"seq": 1}}
    ephemeral_queue.put_nowait(payload)

    collector = _Collector()
    asyncio.run(client.drain_ephemeral_queue_live(collector))

    assert collector.sent == [payload]


@pytest.mark.unit
def test_disconnect_then_reconnect_discards_the_stale_backlog_not_the_live_one() -> None:
    """The scenario the asymmetry exists for: a delta queued while
    disconnected must never reach the console after reconnect, but one
    queued after reconnect must still be delivered live."""
    stale = {"type": "ephemeral", "name": "message.delta", "harness_id": "hrn_box", "data": {"seq": "stale"}}
    ephemeral_queue.put_nowait(stale)

    # Reconnect: the transition into `connected` discards the backlog.
    client.discard_ephemeral_backlog()

    fresh = {"type": "ephemeral", "name": "message.delta", "harness_id": "hrn_box", "data": {"seq": "fresh"}}
    ephemeral_queue.put_nowait(fresh)

    collector = _Collector()
    asyncio.run(client.drain_ephemeral_queue_live(collector))

    assert collector.sent == [fresh]


# ── inbound dispatch, through a real (in-process) door ──────────────────


@pytest.mark.unit
def test_dispatch_request_answers_by_id_through_a_real_door(tmp_path: Path) -> None:
    conns = _conns(tmp_path)
    ctx, token = _ctx(conns, tmp_path)
    headers = {"Authorization": f"Bearer {token}", "X-Sadana-Harness": _HARNESS_ID}
    request = frames.Request(id="r1", method="GET", path="/v1/harness", query="", headers=headers, body=None)

    collector = _Collector()
    with ThreadPoolExecutor(max_workers=1) as pool:
        asyncio.run(client.dispatch_request(ctx, pool, request, collector))

    assert len(collector.sent) == 1
    response = collector.sent[0]
    assert response["type"] == "response"
    assert response["id"] == "r1"
    assert response["status"] == 200
    assert response["body"]["id"] == _HARNESS_ID


# ── hello freshness ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_build_hello_reflects_declared_capabilities_freshly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capabilities, "DECLARED", ("only.this.one",))
    hello = client.build_hello("hrn_box", 42)
    assert hello.capabilities == ("only.this.one",)
    assert hello.ledger_head == 42
    assert hello.harness_id == "hrn_box"


# ── the handshake, against a fake wire ───────────────────────────────────


class _FakeWire:
    def __init__(self, inbound: list[dict]) -> None:
        self._inbound = list(inbound)
        self.sent: list[dict] = []

    async def recv(self, decode: bool | None = None) -> str:
        return json.dumps(self._inbound.pop(0))

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


@pytest.mark.unit
def test_perform_handshake_signs_the_challenge_and_sends_hello() -> None:
    key = generate_key()
    wire = _FakeWire([{"type": "challenge", "nonce": "n1"}, {"type": "welcome"}])

    ok = asyncio.run(client.perform_handshake(wire, key, harness_id="hrn_box", ledger_head=3))

    assert ok is True
    assert len(wire.sent) == 2
    challenge_response = frames.decode(wire.sent[0])
    assert isinstance(challenge_response, frames.ChallengeResponse)
    assert challenge_response.harness_id == "hrn_box"
    signature = base64.b64decode(challenge_response.signature)
    assert verify_signature(key.public_pem(), b"n1", signature)

    hello = frames.decode(wire.sent[1])
    assert isinstance(hello, frames.Hello)
    assert hello.ledger_head == 3


@pytest.mark.unit
def test_perform_handshake_fails_closed_on_any_reply_other_than_welcome() -> None:
    key = generate_key()
    wire = _FakeWire([{"type": "challenge", "nonce": "n1"}, {"type": "error", "code": "INTERNAL", "detail": "no"}])

    ok = asyncio.run(client.perform_handshake(wire, key, harness_id="hrn_box", ledger_head=0))

    assert ok is False


@pytest.mark.unit
def test_perform_handshake_fails_closed_when_first_frame_isnt_a_challenge() -> None:
    key = generate_key()
    wire = _FakeWire([{"type": "welcome"}])

    ok = asyncio.run(client.perform_handshake(wire, key, harness_id="hrn_box", ledger_head=0))

    assert ok is False
