"""The `sadana door` subcommand: serve the door locally, and mint a
development token to talk to it.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 48-49.
Development only — the tether (H30) is the real transport. Owns its parser
and its handler in one file, matching `editor.py`/`chat.py`/`gateway.py`,
and reuses `gateway_daemon.run()`'s lifecycle the same way `editor.py`
already does — the fourth caller of a seam `PLUGIN-MARKET-01` generalised,
not a new one.
"""

from __future__ import annotations

import argparse
import http.server
import ipaddress
import json
import sys
from pathlib import Path

from sadana import client_surface, config, gateway_daemon
from sadana.door import auth
from sadana.door import context as door_context
from sadana.door.serve import make_server
from sadana.tether import identity


def build_door_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("door", help="the console's HTTP door — development only")
    door_sub = parser.add_subparsers(dest="door_command", required=True)

    serve_parser = door_sub.add_parser("serve", help="serve the door on this machine")
    serve_parser.add_argument("--bind", default=None, help="override the configured bind address")
    serve_parser.add_argument("--port", type=int, default=None, help="override the configured port")
    serve_parser.set_defaults(func=cmd_door_serve)

    token_parser = door_sub.add_parser("token", help="mint a development token for a local door")
    token_parser.add_argument("--sub", required=True)
    token_parser.add_argument("--org", default=None)
    token_parser.add_argument("--ws", required=True)
    token_parser.add_argument("--scope", required=True, help="comma-separated <noun>:<verb> list")
    token_parser.add_argument("--ttl", type=int, default=300)
    token_parser.set_defaults(func=cmd_door_token)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _ensure_dev_keypair() -> tuple[Path, Path]:
    door_dir = config.get_paths().state_dir / "door"
    door_dir.mkdir(parents=True, exist_ok=True)
    key_path = door_dir / "dev_key.pem"
    jwks_path = door_dir / "dev_jwks.json"
    if not key_path.exists():
        private_pem, jwk = auth.generate_dev_keypair("dev")
        key_path.write_bytes(private_pem)
        key_path.chmod(0o600)
        jwks_path.write_text(json.dumps(auth.jwks_document(jwk)), encoding="utf-8")
    return key_path, jwks_path


def cmd_door_serve(args: argparse.Namespace) -> int:
    host = args.bind or config.env("SADANA_DOOR_HOST", "127.0.0.1")
    port = args.port or config.env_int("SADANA_DOOR_PORT", 7001)
    if not _is_loopback(host):
        print(
            f"SADANA_DOOR_HOST is {host!r}; this development listener serves loopback only. "
            "The door's own bearer-token verification is the real access control, not this check — "
            "but it is not a substitute for one either.",
            file=sys.stderr,
        )
        return 1

    key_path, jwks_path = _ensure_dev_keypair()
    jwks_url = config.env("SADANA_DOOR_JWKS_URL", "")
    source = auth.JwksSource(url=jwks_url) if jwks_url else auth.JwksSource(path=jwks_path)
    verifier = auth.Verifier(source)

    # An enrolled box (H30) is the box's real identity from here on; a box
    # that has never enrolled falls back to the args/config it always used,
    # so `door serve` and the conformance test keep working unenrolled.
    enrolled = identity.load()
    if enrolled is not None:
        harness_id = enrolled.harness_id
        org = enrolled.org
    else:
        harness_id = config.env("SADANA_DOOR_HARNESS_ID", "hrn_dev")
        org = config.env("SADANA_DOOR_ORG", "") or None

    # H20: `conversations.py`/`messages.py` are the first nouns that need a
    # `client_surface.Runtime` (to call `open_conversation`/`take_turn`) —
    # `open_runtime()` is the one place that assembly already lives, so this
    # replaces `door.py`'s own bare `stores.Connections(...)` call rather than
    # building a second, narrower one beside it.
    turn_runtime = client_surface.open_runtime()

    # `door.context.build()` (H30) is the one place the noun registry is
    # assembled, shared with the tether — a second, independently-typed
    # dict here is exactly what `console_fit_plan.md` §5(e)'s "one door, two
    # transports" would otherwise let drift.
    ctx = door_context.build(
        turn_runtime, box_identity=auth.BoxIdentity(harness_id=harness_id, org=org), verifier=verifier
    )

    def _make_server() -> http.server.ThreadingHTTPServer:
        return make_server(host, port, ctx=ctx)

    print(f"the door is listening on http://{host}:{port}/ — dev key: {key_path}")
    return gateway_daemon.run(make_server=_make_server, lock_filename="door.lock")


def cmd_door_token(args: argparse.Namespace) -> int:
    key_path, _ = _ensure_dev_keypair()
    private_pem = key_path.read_bytes()
    scopes = [s.strip() for s in args.scope.split(",") if s.strip()]
    # `hrn` isn't a CLI flag: a minted token is only ever useful against
    # *this* box, so it always carries this box's own configured id — the
    # same one `cmd_door_serve` presents as `ctx.runtime.harness_id`.
    harness_id = config.env("SADANA_DOOR_HARNESS_ID", "hrn_dev")
    token = auth.mint_token(
        private_pem, "dev", sub=args.sub, org=args.org, ws=args.ws, hrn=harness_id, scope=scopes, ttl=args.ttl
    )
    print(token)
    return 0
