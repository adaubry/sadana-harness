"""The `sadana setup` subcommand.

CLI-SHELL-06 of the CLI-SHELL block — the one command that turns a fresh
instance into a working one. Owns its parser and its handler in one file,
matching `chat.py`/`conversations.py`/`gateway.py`'s existing convention,
and prompts for and resolves its two values; the `.env` write half it used to
own now lives in `env_file.py`, shared with `sadana plugin set`.

What it fills, and in what order (per spec.md): for each value in
`_VALUE_KINDS`, a flag on the command line wins, then a value already in the
live environment is left alone, then a masked TTY prompt, then "missing."
If every value resolved, all are upserted into `state_dir/.env` and it
returns 0. If any is missing it prints exactly which and returns 1, writing
nothing — the no-keyboard path a provisioning workflow depends on.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sadana.env_file import drop_key, env_path, upsert_key

# (env var, flag name, human prompt). Every value here is a secret — none
# may be echoed. The single wired provider (openrouter) plus the daemon
# secret the intent names directly: no provider picker, no 39-stub
# enumeration (spec.md § Rejected alternatives).
_VALUE_KINDS: tuple[tuple[str, str, str], ...] = (
    ("OPENROUTER_API_KEY", "openrouter-key", "OpenRouter API key"),
    (
        "SADANA_GATEWAY_WEBHOOK_SECRET",
        "webhook-secret",
        "Webhook secret (what the gateway signs inbound messages with)",
    ),
)

# var -> flag-name, built once for the missing-values report.
_FLAG_FOR_VAR = {var: flag for var, flag, _ in _VALUE_KINDS}


class _SetupCancelled(Exception):
    pass


def build_setup_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "setup",
        help="turn a fresh instance into a working one: fill in the provider key and daemon secret",
    )
    for _var, flag, prompt in _VALUE_KINDS:
        parser.add_argument(
            f"--{flag}",
            metavar="VALUE",
            default=None,
            help=f"{prompt} to store" + " (prompted for, masked, if not given and not already set)",
        )
    parser.set_defaults(func=cmd_setup)


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _resolve_values(args: argparse.Namespace) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (resolved, missing) over ``_VALUE_KINDS``.

    A flag on the command line wins; then a value already in the live
    environment is left alone; then, only on a TTY, a masked prompt; an
    empty reply is treated as missing. Raises ``_SetupCancelled`` on
    Ctrl-C/EOF during a prompt.
    """
    resolved: list[tuple[str, str]] = []
    missing: list[str] = []
    interactive = _is_interactive()
    for var, flag, prompt in _VALUE_KINDS:
        flag_value = getattr(args, flag.replace("-", "_"), None)
        if flag_value is not None:
            # An explicitly empty flag blanks the value (removal); an
            # empty reply at the prompt means "missed it", not "blank it".
            resolved.append((var, flag_value))
            continue
        env_value = os.environ.get(var)
        if env_value:
            resolved.append((var, env_value))
            continue
        if not interactive:
            missing.append(var)
            continue
        try:
            reply = getpass.getpass(f"{prompt}: ")
        except (KeyboardInterrupt, EOFError):
            raise _SetupCancelled from None
        if reply:
            resolved.append((var, reply))
        else:
            missing.append(var)
    return resolved, missing


def cmd_setup(args: argparse.Namespace) -> int:
    try:
        resolved, missing = _resolve_values(args)
    except _SetupCancelled:
        print("setup cancelled", file=sys.stderr)
        return 1

    if missing:
        print(
            "sadana setup is missing these values; provide them with flags or set them in the environment:",
            file=sys.stderr,
        )
        for var in missing:
            print(f"  --{_FLAG_FOR_VAR[var]} <{var}>", file=sys.stderr)
        return 1

    path = env_path()
    for var, value in resolved:
        if value:
            upsert_key(path, var, value)
        else:
            drop_key(path, var)
    print("sadana setup complete. The instance is configured to run.")
    return 0
