"""The `sadana enroll` subcommand: the box's own side of the console's
enrollment handshake.

`docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md`. Owns its parser
and handler in one file, matching `door.py`/`gateway.py`'s own convention;
`enroll.py` does the work and returns an outcome, this renders it.
"""

from __future__ import annotations

import argparse
import sys

from sadana import enroll


def build_enroll_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("enroll", help="enroll this box with a hosted console")
    parser.add_argument("token", help="the one-time enrollment token minted in the console")
    parser.add_argument("--relay", required=True, help="the console's relay URL")
    parser.add_argument("--console", default=None, help="the console's own URL, for its JWKS")
    parser.add_argument("--replace-key", action="store_true", help="overwrite an existing key/identity")
    parser.set_defaults(func=cmd_enroll)


def cmd_enroll(args: argparse.Namespace) -> int:
    outcome = enroll.enroll(args.token, args.relay, console=args.console, replace_key=args.replace_key)
    match outcome:
        case enroll.Enrolled(harness_id=harness_id, verified=verified):
            if not verified:
                print("warning: the token names no issuer; its claims are trusted unverified", file=sys.stderr)
            print(harness_id)
            return 0
        case enroll.TokenRefused(reason=reason):
            print(f"error: {reason}", file=sys.stderr)
            return 1
        case enroll.AlreadyEnrolled(harness_id=harness_id):
            print(harness_id)
            print("already enrolled; pass --replace-key to re-enroll", file=sys.stderr)
            return 1
        case enroll.NetworkFailed(detail=detail):
            print(f"error: {detail}", file=sys.stderr)
            return 1
