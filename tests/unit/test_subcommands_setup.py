"""Tests for sadana.subcommands.setup: the guided first-run config command.

``cmd_setup`` writes a real `.env` in the state dir — that is its whole
purpose. Testing-conventions bars real filesystem writes, but the conftest
autouse fixture redirects ``SADANA_STATE_DIR`` to a tmp path and the runner
pins HOME, so these tests write only under ``tmp_path``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from sadana.subcommands.setup import (
    _SetupCancelled,
    build_setup_parser,
    cmd_setup,
)

_VAL = "f1xed-secret"  # pragma: allowlist secret - fixed test fixture value, not a real credential
_VAL2 = "an0ther-secret"  # pragma: allowlist secret - fixed test fixture value, not a real credential


def _args(**kwargs: str | None) -> argparse.Namespace:
    # argparse would store "--openrouter-key" as "openrouter_key".
    return argparse.Namespace(
        openrouter_key=kwargs.get("openrouter_key"),
        webhook_secret=kwargs.get("webhook_secret"),
    )


def _env_path() -> Path:
    from sadana import config

    return config.get_paths().state_dir / ".env"


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"')
    return out


@pytest.mark.unit
def test_build_setup_parser_wires_both_flags() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_setup_parser(subparsers)
    args = parser.parse_args(["setup", "--openrouter-key", "K", "--webhook-secret", "S"])
    assert (args.openrouter_key, args.webhook_secret) == ("K", "S")
    assert args.func is cmd_setup


@pytest.mark.unit
def test_cmd_setup_scripted_writes_both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cmd_setup(_args(openrouter_key="K", webhook_secret="S")) == 0
    env_vals = _read_env(_env_path())
    assert env_vals == {"OPENROUTER_API_KEY": "K", "SADANA_GATEWAY_WEBHOOK_SECRET": "S"}


@pytest.mark.unit
def test_cmd_setup_no_tty_nothing_supplied_reports_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert cmd_setup(_args()) == 1
    err = capsys.readouterr().err
    assert "OPENROUTER_API_KEY" in err
    assert "SADANA_GATEWAY_WEBHOOK_SECRET" in err
    assert not _env_path().exists()


@pytest.mark.unit
def test_cmd_setup_leaves_alone_values_already_in_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setenv("OPENROUTER_API_KEY", _VAL)
    assert cmd_setup(_args(webhook_secret="S")) == 0
    env_vals = _read_env(_env_path())
    assert env_vals["OPENROUTER_API_KEY"] == _VAL
    assert env_vals["SADANA_GATEWAY_WEBHOOK_SECRET"] == "S"


@pytest.mark.unit
def test_cmd_setup_upsert_replaces_only_owned_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'OTHER_SECRET="x"\nOPENROUTER_API_KEY="old"\n', encoding="utf-8"
    )  # pragma: allowlist secret - fixed test fixture line
    assert cmd_setup(_args(openrouter_key="new", webhook_secret="S")) == 0
    after = _env_path().read_text(encoding="utf-8")
    assert after.count("OTHER_SECRET") == 1
    assert 'OPENROUTER_API_KEY="new"' in after  # pragma: allowlist secret - fixed test fixture value
    assert "old" not in after


@pytest.mark.unit
def test_cmd_setup_never_echoes_a_secret(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert cmd_setup(_args(openrouter_key=_VAL, webhook_secret=_VAL2)) == 0
    captured = capsys.readouterr()
    assert _VAL not in captured.out
    assert _VAL not in captured.err
    assert _VAL2 not in captured.out
    assert _VAL2 not in captured.err


@pytest.mark.unit
def test_cmd_setup_filled_env_is_ready_for_load_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cmd_setup(_args(openrouter_key="K", webhook_secret="S")) == 0

    from sadana import config

    dotenv = config.get_paths().state_dir / ".env"
    assert dotenv.exists()
    config.load_dotenv()
    assert os.environ.get("OPENROUTER_API_KEY") == "K"
    assert os.environ.get("SADANA_GATEWAY_WEBHOOK_SECRET") == "S"
    # load_dotenv writes straight into os.environ, bypassing monkeypatch —
    # undo it here so later tests aren't poisoned by values resolving from env.
    os.environ.pop("OPENROUTER_API_KEY", None)
    os.environ.pop("SADANA_GATEWAY_WEBHOOK_SECRET", None)


@pytest.mark.unit
def test_resolve_values_prompts_on_tty_and_raises_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sadana.subcommands.setup import _resolve_values

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    # load_dotenv writes straight into os.environ (bypassing monkeypatch), so
    # an earlier test may have leaked real values — clear them so the prompt
    # branch is what runs.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SADANA_GATEWAY_WEBHOOK_SECRET", raising=False)

    import sadana.subcommands.setup as setup_mod

    def fake_getpass(prompt: str) -> str:
        return next(
            (v for k, v in {"OpenRouter": "K", "Webhook": "S"}.items() if prompt.startswith(k)),
            "",
        )

    monkeypatch.setattr(setup_mod.getpass, "getpass", fake_getpass)
    resolved, missing = _resolve_values(_args())
    assert missing == []
    assert dict(resolved) == {
        "OPENROUTER_API_KEY": "K",
        "SADANA_GATEWAY_WEBHOOK_SECRET": "S",
    }

    def raising_getpass(prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_mod.getpass, "getpass", raising_getpass)
    with pytest.raises(_SetupCancelled):
        _resolve_values(_args())


@pytest.mark.unit
def test_cmd_setup_interrupt_prints_cancelled_and_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SADANA_GATEWAY_WEBHOOK_SECRET", raising=False)

    import sadana.subcommands.setup as setup_mod

    def raising_getpass(prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_mod.getpass, "getpass", raising_getpass)
    assert cmd_setup(_args()) == 1
    assert "cancelled" in capsys.readouterr().err
    assert not _env_path().exists()


@pytest.mark.unit
def test_cmd_setup_creates_dotenv_0600_at_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    # The .env holds secrets: it must be mode 0600 from the instant of
    # creation, not after a later chmod (a write-then-chmod leaves a window
    # where the file is world-readable).
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert cmd_setup(_args(openrouter_key="K", webhook_secret="S")) == 0
    assert _env_path().stat().st_mode & 0o777 == 0o600


@pytest.mark.unit
def test_quote_env_value_strips_newlines(monkeypatch: pytest.MonkeyPatch) -> None:
    # A newline in a value must not split one .env assignment across two
    # physical lines — that would inject a second assignment the reader
    # imports on the next load.
    import sadana.subcommands.setup as setup_mod

    value = 'abc"\nexport OPENROUTER_API_KEY="PWNED'
    quoted = setup_mod._quote_env_value(value)
    assert "\n" not in quoted
    assert "\r" not in quoted
    # and the written line round-trips to exactly the sanitized value
    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"OPENROUTER_API_KEY={quoted}\n", encoding="utf-8")
    assert _read_env(path)["OPENROUTER_API_KEY"] == 'abc"export OPENROUTER_API_KEY="PWNED'


@pytest.mark.unit
def test_upsert_replaces_spaced_key_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # A hand-edited "KEY = value" line is the same assignment to load_dotenv
    # (it strips the key's whitespace); the writer must replace it, not
    # append a duplicate that would shadow it.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    spaced = 'OPENROUTER_API_KEY = "old"\nOTHER="x"\n'  # pragma: allowlist secret - fixed test fixture line
    path.write_text(spaced, encoding="utf-8")
    assert cmd_setup(_args(openrouter_key="new", webhook_secret=_VAL2)) == 0
    after = _env_path().read_text(encoding="utf-8")
    assert after.count("OPENROUTER_API_KEY") == 1
    assert 'OPENROUTER_API_KEY="new"' in after  # pragma: allowlist secret - fixed test fixture line
    assert 'OTHER="x"' in after


@pytest.mark.unit
def test_cmd_setup_empty_flag_removes_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicitly-empty flag is a removal: blanking the value drops the
    # key from the .env rather than writing an empty assignment the reader
    # would skip anyway.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cmd_setup(_args(openrouter_key="K", webhook_secret="S")) == 0
    assert cmd_setup(_args(openrouter_key="", webhook_secret="S")) == 0
    env_vals = _read_env(_env_path())
    assert "OPENROUTER_API_KEY" not in env_vals
    assert env_vals["SADANA_GATEWAY_WEBHOOK_SECRET"] == "S"
