#!/usr/bin/env python3
"""Standalone proof that CLI-SHELL-06's `sadana setup` really turns a fresh
instance into a working one — the whole point of the work item.

Not a pytest test: it drives `sadana setup` as a *subprocess* against a
throwaway state dir, and the model round trip is stubbed at
`model_access.send` (the same module-level reassignment
prove_conversation_e2e.py and prove_gateway_webhook_e2e.py use). Run by hand,
paste its output into review.md's ## Evidence as the Test-stage
deliverable. No real API key, no network, no real state dir is touched.

What it proves, end to end:

1. `sadana setup --openrouter-key ... --webhook-secret ...` on a non-TTY
   writes `state_dir/.env` containing exactly those two keys, chmod 0600,
   and prints no secret — a client-side CLI whose whole output is stdout.
2. A *fresh process* running plain `sadana setup` right after is
   idempotent: the stored values are re-read from the .env by
   `config.load_dotenv()` and left alone (exit 0, nothing to fill).
3. A *fresh process* with a clean state dir, `sadana setup` with no flags
   and no TTY reports both missing by name and exits 1 without hanging —
   the provisioning path no keyboard depends on.
4. `config.load_dotenv()` in a live process sees both keys from the file
   the subprocess wrote. The deserialization half of the format.
5. A real `sadana chat` turn — driven through the real `main(["chat"])`
   entry, one line fed on stdin — completes a COMPLETED turn with the
   stored key in the environment, where before setup the same real
   `model_access.send()` returned `NeedsCredentialOrProviderChange` with
   zero network.
6. `gateway_daemon.run()` with an empty secret refuses to start (exit 1),
   and with the stored secret — read the same way a real `sadana gateway
   run` reads it — binds a real socket, serves a real request, and stops
   cleanly on SIGTERM.

The stub `_canned_send` below stands in for the provider's network half
exactly as prove_gateway_webhook_e2e.py's own does; the live provider round
trip is MODEL-ACCESS-01's already-proven job.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import client_surface, config, gateway_dispatch, model_access  # noqa: E402
from sadana.gateway import MessageEvent  # noqa: E402
from sadana.subcommands.gateway import cmd_gateway_run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
OPENROUTER_KEY = "sk-prove-fresh-instance-key"  # pragma: allowlist secret - fixed proof-only value
WEBHOOK_SECRET = "pw-prove-fresh-instance-secret"  # pragma: allowlist secret - fixed proof-only value
HOST = "127.0.0.1"
PORT = 18767
GW_URL = f"http://{HOST}:{PORT}/webhook"  # pragma: allowlist secret - local test URL


def _cli(*args: str, state_dir: Path, timeout_s: float = 60.0) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SADANA_STATE_DIR"] = str(state_dir)
    # src-layout, not installed: module resolution comes from PYTHONPATH,
    # not from a site-packages copy.
    env["PYTHONPATH"] = str(ROOT / "src")
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("SADANA_GATEWAY_WEBHOOK_SECRET", None)
    for harmful in ("NOTIFY_SOCKET", "XDG_CONFIG_HOME"):
        env.pop(harmful, None)
    return subprocess.run(
        [PYTHON, "-m", "sadana.cli", *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _env_values(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"')
    return out


def _canned_send(request: model_access.Request) -> model_access.Response:
    last_user = next((m.get("content") for m in reversed(request.messages) if m.get("role") == "user"), "")
    if isinstance(last_user, list):
        last_user = "".join(block.get("text", "") for block in last_user if isinstance(block, dict))
    return model_access.Response(
        content=f"echo: {last_user}", tool_calls=(), finish_reason="stop", usage=model_access.Usage()
    )


def _wait_for_port(host: str, port: int, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"gateway never bound {host}:{port}")


def scenario_setup_writes_dotenv(state: Path) -> None:
    print("\n=== 1. `sadana setup` with both flags, non-TTY ===")
    # The subprocess's stdin is a pipe (not a tty) → scripted path. If the
    # command ever tried to prompt, getpass would fail with a warning and
    # treat it as missing → exit 1, caught by the assert below.
    result = _cli("setup", "--openrouter-key", OPENROUTER_KEY, "--webhook-secret", WEBHOOK_SECRET, state_dir=state)
    print(f"exit={result.returncode}")

    if result.returncode == 0:
        print("stdout:", result.stdout.strip())
        print("stderr:", result.stderr.strip())

    dotenv = state / ".env"
    assert result.returncode == 0, f"setup should exit 0 with both flags, got {result.returncode}"
    assert dotenv.is_file(), f"state_dir/.env was not written at {dotenv}"
    values = _env_values(dotenv)
    assert values == {
        "OPENROUTER_API_KEY": OPENROUTER_KEY,
        "SADANA_GATEWAY_WEBHOOK_SECRET": WEBHOOK_SECRET,
    }, f"unexpected .env contents: {values!r}"
    mode = dotenv.stat().st_mode & 0o777
    assert mode == 0o600, f"expected .env mode 0600, got {mode:o}"
    assert OPENROUTER_KEY not in result.stdout, "setup must never print a secret to stdout"
    assert OPENROUTER_KEY not in result.stderr, "setup must never print a secret to stderr"
    assert WEBHOOK_SECRET not in result.stdout, "setup must never print a secret to stdout"
    assert WEBHOOK_SECRET not in result.stderr, "setup must never print a secret to stderr"
    print("[ok] wrote state_dir/.env with exactly both keys, mode 0600, printed no secret")


def scenario_fresh_process_setup_is_idempotent(state: Path) -> None:
    print("\n=== 2. a fresh `sadana setup` (no flags, no TTY) after the .env exists ===")
    result = _cli("setup", state_dir=state)
    print(f"exit={result.returncode}")
    assert result.returncode == 0, (
        f"idempotent re-run should exit 0 (all values already present), got {result.returncode}: "
        f"{result.stdout} {result.stderr}"
    )
    values = _env_values(state / ".env")
    assert values["OPENROUTER_API_KEY"] == OPENROUTER_KEY
    assert values["SADANA_GATEWAY_WEBHOOK_SECRET"] == WEBHOOK_SECRET
    print("[ok] values were read back from the .env and left alone — nothing to fill, exit 0")


def scenario_missing_without_dotenv(state: Path, tmp: Path) -> None:
    print("\n=== 3. a fresh `sadana setup` (no flags, no TTY) with nothing stored ===")
    clean = tmp / "clean-state"
    result = _cli("setup", state_dir=clean)
    print(f"exit={result.returncode}")
    assert result.returncode == 1, f"no input + no .env should exit 1, got {result.returncode}"
    assert "OPENROUTER_API_KEY" in result.stderr, "stderr should name the missing OPENROUTER_API_KEY"
    assert (
        "SADANA_GATEWAY_WEBHOOK_SECRET" in result.stderr
    ), "stderr should name the missing SADANA_GATEWAY_WEBHOOK_SECRET"
    assert not (clean / ".env").exists(), "nothing may be written when values are missing"
    print("[ok] reported both missing by name, wrote nothing, did not hang")


def scenario_load_dotenv_sees_stored_keys(state: Path) -> None:
    print("\n=== 4. config.load_dotenv() reads the stored .env in a live process ===")
    saved_state = os.environ.get("SADANA_STATE_DIR")
    os.environ["SADANA_STATE_DIR"] = str(state)
    try:
        config.load_dotenv()
        assert os.environ.get("OPENROUTER_API_KEY") == OPENROUTER_KEY, "OPENROUTER_API_KEY not loaded"
        assert (
            os.environ.get("SADANA_GATEWAY_WEBHOOK_SECRET") == WEBHOOK_SECRET
        ), "SADANA_GATEWAY_WEBHOOK_SECRET not loaded"
        print("[ok] both keys are in the live environment after load_dotenv()")
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("SADANA_GATEWAY_WEBHOOK_SECRET", None)
        if saved_state is None:
            os.environ.pop("SADANA_STATE_DIR", None)
        else:
            os.environ["SADANA_STATE_DIR"] = saved_state


def scenario_before_setup_send_needs_credential_and_after_setup_chat_completes(state: Path, tmp: Path) -> None:
    print("\n=== 5. before setup: real send() needs a credential, no network ===")
    clean = tmp / "pre-state"
    saved_state = os.environ.get("SADANA_STATE_DIR")
    os.environ["SADANA_STATE_DIR"] = str(clean)
    try:
        # send() reads env at call time; prove the plain missing-file state
        # yields the zero-network credential outcome.
        saved_key = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            request = model_access.Request(
                messages=({"role": "user", "content": "hi"},), provider="openrouter", model=model_access.DEFAULT_MODEL
            )
            outcome = model_access.send(request)
            print(f"outcome={outcome!r}")
            assert isinstance(
                outcome, model_access.NeedsCredentialOrProviderChange
            ), f"expected NeedsCredentialOrProviderChange before setup, got {outcome!r}"
            print("[ok] real send() returned NeedsCredentialOrProviderChange (zero network)")
        finally:
            if saved_key is not None:
                os.environ["OPENROUTER_API_KEY"] = saved_key
            else:
                os.environ.pop("OPENROUTER_API_KEY", None)
    finally:
        if saved_state is None:
            os.environ.pop("SADANA_STATE_DIR", None)
        else:
            os.environ["SADANA_STATE_DIR"] = saved_state

    print("\n=== 5b. after setup: a real `sadana chat` turn completes with the stored key ===")
    os.environ["SADANA_STATE_DIR"] = str(state)
    try:
        config.load_dotenv()
        assert os.environ.get("OPENROUTER_API_KEY") == OPENROUTER_KEY, "stored key not in env"

        old_send = model_access.send
        model_access.send = _canned_send  # type: ignore[assignment]

        old_stdin = sys.stdin
        sys.stdin = io.StringIO("ping\n")
        try:
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                from sadana.cli import main

                code = main(["chat"])
        finally:
            sys.stdin = old_stdin
            model_access.send = old_send
        output = captured.getvalue()
        print(f"exit={code} chat-output={output.strip()!r}")
        assert code == 0, f"a configured `sadana chat` should complete a turn and exit 0, got {code}"
        assert "echo: ping" in output, f"expected the turn's reply in the chat output, got {output!r}"
        print("[ok] `sadana chat` completed a real turn with the stored key in the environment")
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("SADANA_GATEWAY_WEBHOOK_SECRET", None)
        if saved_state is None:
            os.environ.pop("SADANA_STATE_DIR", None)
        else:
            os.environ["SADANA_STATE_DIR"] = saved_state


def scenario_gateway_guard(state: Path, tmp: Path) -> None:
    print("\n=== 6a. gateway_daemon.run() without a secret refuses to start ===")
    # A clean state dir (no .env) so the subprocess has no stored secret.
    clean = tmp / "gw-clean"
    result = _cli("gateway", "run", state_dir=clean, timeout_s=15.0)
    print(f"exit={result.returncode}")
    assert result.returncode == 1, f"run() with an empty secret should refuse (exit 1), got {result.returncode}"
    assert "not set; refusing to start" in result.stderr
    print("[ok] run() refused to start without a secret")


def scenario_gateway_with_stored_secret(state: Path) -> None:
    print("\n=== 6b. gateway_daemon.run() starts with the stored secret ===")
    os.environ["SADANA_STATE_DIR"] = str(state)
    try:
        config.load_dotenv()
        saved_host = os.environ.get("SADANA_GATEWAY_HOST")
        saved_port = os.environ.get("SADANA_GATEWAY_PORT")
        os.environ["SADANA_GATEWAY_HOST"] = HOST
        os.environ["SADANA_GATEWAY_PORT"] = str(PORT)
        try:
            old_send = model_access.send
            model_access.send = _canned_send  # type: ignore[assignment]
            runtime = client_surface.open_runtime(provider="openrouter", model=model_access.DEFAULT_MODEL)

            def on_message(event: MessageEvent) -> tuple[bool, str]:
                return asyncio.run(
                    gateway_dispatch.handle_inbound(
                        runtime,
                        event,
                    )
                )

            def _drive() -> None:
                _wait_for_port(HOST, PORT)
                print(f"[ok] gateway bound {HOST}:{PORT} with the stored secret")
                body = b'{"chat_id": "setup-proof", "text": "ping"}\n'
                req = urllib.request.Request(
                    GW_URL,
                    data=body,
                    method="POST",
                    headers={"X-Sadana-Webhook-Secret": WEBHOOK_SECRET, "Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed http://127.0.0.1
                    status = resp.status
                    body = resp.read().decode("utf-8")
                print(f"http={status} body={body}")
                assert status == 200
                os.kill(os.getpid(), signal.SIGTERM)

            driver = threading.Thread(target=_drive)
            driver.start()

            # cmd_gateway_run reads secret from config.env at call time; the
            # stored secret is now in os.environ via load_dotenv. We reach the
            # daemon the exact way `sadana gateway run` does.
            args = argparse.Namespace(host=HOST, port=PORT, gateway_command=None, command=None)
            code = cmd_gateway_run(args)
            driver.join()
            print(f"exit={code}")
            assert code == 0, f"gateway_daemon.run() with the stored secret should stop cleanly on SIGTERM, got {code}"
            print("[ok] a real daemon ran with the stored secret and stopped cleanly")
        finally:
            model_access.send = old_send
            os.environ.pop("SADANA_GATEWAY_HOST", None)
            os.environ.pop("SADANA_GATEWAY_PORT", None)
            if saved_host is not None:
                os.environ["SADANA_GATEWAY_HOST"] = saved_host
            if saved_port is not None:
                os.environ["SADANA_GATEWAY_PORT"] = saved_port
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("SADANA_GATEWAY_WEBHOOK_SECRET", None)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="sadana-setup-proof-"))
    state = tmp / "state"

    try:
        scenario_setup_writes_dotenv(state)
        scenario_fresh_process_setup_is_idempotent(state)
        scenario_missing_without_dotenv(state, tmp)
        scenario_load_dotenv_sees_stored_keys(state)
        scenario_before_setup_send_needs_credential_and_after_setup_chat_completes(state, tmp)
        scenario_gateway_guard(state, tmp)
        scenario_gateway_with_stored_secret(state)

        print("\nALL ASSERTIONS PASSED")
    finally:
        # Clean up the proof's own env changes regardless of where it failed.
        env_keys = (
            "OPENROUTER_API_KEY",
            "SADANA_GATEWAY_WEBHOOK_SECRET",
            "SADANA_STATE_DIR",
            "SADANA_GATEWAY_HOST",
            "SADANA_GATEWAY_PORT",
        )
        for key in env_keys:
            os.environ.pop(key, None)


if __name__ == "__main__":
    main()
