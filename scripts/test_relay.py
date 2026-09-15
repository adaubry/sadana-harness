#!/usr/bin/env python3
"""A minimal console relay, speaking `docs/console/wire.md` §5 verbatim.

`docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md`. Fixture, not
product — the console's own relay replaces this once it exists, and the
box (`sadana.tether.client`, `sadana.enroll`) does not change when that
happens. ~300 lines, on purpose: enough protocol to prove the box's own
half, no more.

One TCP port serves both the plain-HTTP endpoints (`/enroll`, `/forward`,
`/token`, `/.well-known/jwks.json`, `/events`) and the WebSocket `/connect`
endpoint — matching `<relay>/enroll` and `<relay>/connect` sharing one base
URL, per the wire contract. `websockets`'s own `serve()` cannot do this (its
`process_request` hook never reads a request body — by design, a WebSocket
handshake never has one), so this hand-rolls the minimum: read the request
line and headers with `asyncio` streams, and for an `Upgrade: websocket`
request only, hand the raw bytes to `websockets.server.ServerProtocol` (the
library's own sans-io state machine) to compute the handshake response and
frame the WS traffic that follows. Everything protocol-meaningful — JWT
minting/verification, the raw r‖s signature check — reuses this project's
own `door.auth`/`tether.keys`, never reimplemented here.

This fixture also plays the console's own role for minting tokens
(`POST /token`) and serving their JWKS, so `sadana enroll`'s `iss`-driven
JWKS verification has something real to check against without a second
process.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import jwt as pyjwt
from websockets.frames import Frame, Opcode
from websockets.http11 import Request
from websockets.server import ServerProtocol

from sadana import gateway
from sadana.door import auth
from sadana.tether import frames, keys

logger = logging.getLogger("test_relay")


# ── the hand-rolled HTTP/WS multiplexer ─────────────────────────────────


class _WsConnection:
    """A WebSocket connection accepted by this module's own upgrade,
    exposing `recv()`/`send()` like a normal `websockets` connection —
    everything past the handshake is ordinary sans-io frame handling."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, proto: ServerProtocol) -> None:
        self._reader = reader
        self._writer = writer
        self._proto = proto
        self._closed = False

    async def _flush(self) -> None:
        for chunk in self._proto.data_to_send():
            self._writer.write(chunk)
        await self._writer.drain()

    async def send(self, text: str) -> None:
        if self._closed:
            return
        self._proto.send_text(text.encode())
        await self._flush()

    async def recv(self) -> str | None:
        """One text message, or `None` on close/EOF."""
        while True:
            for event in self._proto.events_received():
                if isinstance(event, Frame):
                    if event.opcode is Opcode.TEXT:
                        return bytes(event.data).decode()
                    if event.opcode is Opcode.CLOSE:
                        await self._flush()
                        return None
                    # PING/PONG/BINARY/CONT: unused by this protocol.
            if self._proto.close_expected():
                return None
            chunk = await self._reader.read(4096)
            if not chunk:
                return None
            self._proto.receive_data(chunk)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._proto.send_close()
            await self._flush()
        self._writer.close()


async def _read_headers(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str]] | None:
    request_line = await reader.readline()
    if not request_line:
        return None
    method, path, _ = request_line.decode().split(" ", 2)
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b""):
            break
        k, _, v = line.decode().partition(":")
        headers[k.strip()] = v.strip()
    return method, path, headers


def _http_response(status: int, payload: object, *, extra_headers: dict[str, str] | None = None) -> bytes:
    body = json.dumps(payload).encode()
    reason = {200: "OK", 201: "Created", 401: "Unauthorized", 404: "Not Found", 429: "Too Many Requests"}.get(
        status, "X"
    )
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n"
    )
    for k, v in (extra_headers or {}).items():
        head += f"{k}: {v}\r\n"
    return head.encode() + b"\r\n" + body


async def _try_upgrade(
    method: str, headers: dict[str, str], reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> _WsConnection | None:
    if method != "GET" or headers.get("Upgrade", "").lower() != "websocket":
        return None
    proto = ServerProtocol()
    raw = b"GET /connect HTTP/1.1\r\n" + b"".join(f"{k}: {v}\r\n".encode() for k, v in headers.items()) + b"\r\n"
    proto.receive_data(raw)
    events = proto.events_received()
    if not events or not isinstance(events[0], Request):
        return None
    response = proto.accept(events[0])
    proto.send_response(response)
    for chunk in proto.data_to_send():
        writer.write(chunk)
    await writer.drain()
    return _WsConnection(reader, writer, proto)


# ── the relay's own state ───────────────────────────────────────────────


@dataclass
class _Box:
    public_key: bytes
    ws: _WsConnection | None = None
    capabilities: tuple[str, ...] | None = None


@dataclass
class Relay:
    private_pem: bytes
    jwk: dict[str, str]
    kid: str
    base_url: str = ""
    retry_after: float | None = None
    die_after: float | None = None
    state_file: str | None = None
    boxes: dict[str, _Box] = field(default_factory=dict)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    events_log: list[dict[str, object]] = field(default_factory=list)
    dead: bool = False

    def jwks(self) -> dict[str, object]:
        return auth.jwks_document(self.jwk)

    def load_state(self) -> None:
        """Enrolled boxes, from a prior life of this same fixture — a real
        relay's database survives its own restart; this in-memory one
        otherwise would not, which would make "kill the relay, restart it"
        indistinguishable from "the box was never enrolled" in any test
        that actually exercises reconnection."""
        if self.state_file is None or not Path(self.state_file).is_file():
            return
        data = json.loads(Path(self.state_file).read_text(encoding="utf-8"))
        for harness_id, public_key_pem in data.items():
            self.boxes[harness_id] = _Box(public_key=public_key_pem.encode())

    def save_state(self) -> None:
        if self.state_file is None:
            return
        data = {harness_id: box.public_key.decode() for harness_id, box in self.boxes.items()}
        Path(self.state_file).write_text(json.dumps(data), encoding="utf-8")

    def verify_own_token(self, token: str) -> dict[str, object] | None:
        """Verifies a token this relay minted itself, entirely in-memory —
        never a network round trip to its own `/.well-known/jwks.json`,
        which would deadlock a single-threaded server answering its own
        request from inside one connection's handler."""
        try:
            return dict(pyjwt.decode(token, key=pyjwt.PyJWK(self.jwk), algorithms=["ES256"], leeway=30))
        except pyjwt.InvalidTokenError:
            return None

    async def _die_watcher(self) -> None:
        if self.die_after is None:
            return
        await asyncio.sleep(self.die_after)
        self.dead = True
        for box in self.boxes.values():
            if box.ws is not None:
                await box.ws.close()
        logger.info("test relay: --die-after elapsed, refusing new connections and closing live ones")


# ── HTTP endpoint handlers ───────────────────────────────────────────────


async def _handle_enroll(relay: Relay, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, object]]:
    auth_header = gateway.header_value(headers, "Authorization")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return 401, {"detail": "missing bearer token"}
    claims = relay.verify_own_token(token)
    harness_id = claims.get("hrn") if claims is not None else None
    if not isinstance(harness_id, str) or not harness_id:
        return 401, {"detail": "the enrollment token was refused"}
    payload = json.loads(body)
    relay.boxes[harness_id] = _Box(public_key=payload["public_key"].encode())
    relay.save_state()
    return 201, {"harness_id": harness_id}


async def _handle_token(relay: Relay, body: bytes) -> tuple[int, dict[str, object]]:
    payload = json.loads(body)
    token = auth.mint_token(
        relay.private_pem,
        relay.kid,
        sub=payload.get("sub", "console"),
        org=payload.get("org"),
        ws=payload.get("ws", "ws1"),
        hrn=payload["hrn"],
        scope=payload.get("scope", []),
        ttl=payload.get("ttl", 3600),
        iss=relay.base_url,
    )
    return 200, {"token": token}


async def _handle_forward(relay: Relay, harness_id: str, body: bytes) -> tuple[int, dict[str, object]]:
    box = relay.boxes.get(harness_id)
    if box is None or box.ws is None:
        return 503, {"code": "HARNESS_OFFLINE", "detail": f"{harness_id!r} is not connected"}
    payload = json.loads(body)
    request_id = uuid.uuid4().hex
    request = frames.Request(
        id=request_id,
        method=payload["method"],
        path=payload["path"],
        query=payload.get("query", ""),
        headers=payload.get("headers", {}),
        body=payload.get("body"),
    )
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    relay.pending[request_id] = future
    await box.ws.send(json.dumps(frames.encode(request)))
    timeout = payload.get("timeout_ms", 5000) / 1000.0
    try:
        response = await asyncio.wait_for(future, timeout=timeout)
    except TimeoutError:
        relay.pending.pop(request_id, None)
        return 504, {"detail": "the box did not answer in time"}
    return 200, {"status": response.status, "body": response.body}


def _events_since(relay: Relay) -> list[dict[str, object]]:
    return relay.events_log


# ── the per-connection dispatcher ───────────────────────────────────────


async def _handle_ws(relay: Relay, harness_id_hint: str | None, ws: _WsConnection) -> None:
    nonce = uuid.uuid4().hex
    await ws.send(json.dumps(frames.encode(frames.Challenge(nonce=nonce))))
    raw = await ws.recv()
    if raw is None:
        return
    response = frames.decode(json.loads(raw))
    if not isinstance(response, frames.ChallengeResponse):
        return
    box = relay.boxes.get(response.harness_id)
    if box is None:
        return
    signature = base64.b64decode(response.signature)
    if not keys.verify(box.public_key, nonce.encode("utf-8"), signature):
        return
    await ws.send(json.dumps(frames.encode(frames.Welcome())))

    raw = await ws.recv()
    if raw is None:
        return
    hello = frames.decode(json.loads(raw))
    if not isinstance(hello, frames.Hello):
        return
    logger.info("test relay: hello from %s — capabilities=%s", hello.harness_id, hello.capabilities)

    box.ws = ws
    box.capabilities = hello.capabilities
    try:
        while True:
            raw = await ws.recv()
            if raw is None:
                break
            decoded = frames.decode(json.loads(raw))
            if isinstance(decoded, frames.Heartbeat):
                continue
            if isinstance(decoded, frames.Response):
                future = relay.pending.pop(decoded.id, None)
                if future is not None and not future.done():
                    future.set_result(decoded)
            elif isinstance(decoded, frames.Event | frames.Ephemeral):
                relay.events_log.append(json.loads(raw))
            # Unrecognised frames from the box are logged, not fatal — this
            # is the relay side; the box's own decode() is what wire.md's
            # unknown-frame rule actually governs.
    finally:
        box.ws = None


async def _handle_connection(relay: Relay, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        if relay.dead:
            writer.close()
            return
        parsed = await _read_headers(reader)
        if parsed is None:
            writer.close()
            return
        method, path, headers = parsed
        route = path.split("?", 1)[0]

        if route == "/connect":
            if relay.retry_after is not None:
                extra = {"Retry-After": f"{relay.retry_after:.0f}"}
                writer.write(_http_response(429, {"detail": "retry later"}, extra_headers=extra))
                await writer.drain()
                writer.close()
                return
            ws = await _try_upgrade(method, headers, reader, writer)
            if ws is None:
                writer.write(_http_response(400, {"detail": "expected a WebSocket upgrade"}))
                await writer.drain()
                writer.close()
                return
            await _handle_ws(relay, None, ws)
            await ws.close()
            return

        body_len = int(headers.get("Content-Length", "0"))
        body = await reader.readexactly(body_len) if body_len else b""

        if method == "POST" and route == "/enroll":
            status, payload = await _handle_enroll(relay, headers, body)
        elif method == "POST" and route == "/token":
            status, payload = await _handle_token(relay, body)
        elif method == "POST" and route.startswith("/forward/"):
            status, payload = await _handle_forward(relay, route.removeprefix("/forward/"), body)
        elif method == "GET" and route == "/.well-known/jwks.json":
            status, payload = 200, relay.jwks()
        elif method == "GET" and route == "/events":
            status, payload = 200, {"data": _events_since(relay)}
        elif method == "GET" and route.startswith("/status/"):
            box = relay.boxes.get(route.removeprefix("/status/"))
            if box is None:
                status, payload = 404, {"detail": "unknown harness"}
            else:
                status, payload = 200, {"connected": box.ws is not None, "capabilities": list(box.capabilities or ())}
        else:
            status, payload = 404, {"detail": f"no route for {method} {route}"}

        writer.write(_http_response(status, payload))
        await writer.drain()
        writer.close()
    except Exception:
        logger.exception("test relay connection failed")
        with contextlib.suppress(Exception):
            writer.close()


async def run(
    host: str, port: int, *, retry_after: float | None, die_after: float | None, state_file: str | None
) -> None:
    private_pem, jwk = auth.generate_dev_keypair("relay")
    relay = Relay(
        private_pem=private_pem,
        jwk=jwk,
        kid="relay",
        retry_after=retry_after,
        die_after=die_after,
        state_file=state_file,
    )
    relay.load_state()

    server = await asyncio.start_server(lambda r, w: _handle_connection(relay, r, w), host, port)
    bound_port = server.sockets[0].getsockname()[1]
    relay.base_url = f"http://{host}:{bound_port}"
    print(f"test relay listening on {relay.base_url}", flush=True)

    asyncio.get_running_loop().create_task(relay._die_watcher())
    async with server:
        await server.serve_forever()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--retry-after", type=float, default=None, help="answer every /connect with 429")
    parser.add_argument("--die-after", type=float, default=None, help="seconds until the relay stops answering")
    parser.add_argument(
        "--state-file",
        default=None,
        help="persist enrolled harness_id->public_key here, across this fixture's own restarts "
        "(a real relay's database would survive a restart; this in-memory one otherwise would not)",
    )
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run(
                args.host, args.port, retry_after=args.retry_after, die_after=args.die_after, state_file=args.state_file
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
