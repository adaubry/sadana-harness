"""Tests for sadana.gateway_daemon."""

from __future__ import annotations

import fcntl

import pytest

from sadana import channel_webhook, config, gateway_daemon


def _refusing_make_server(*args: object, **kwargs: object) -> None:
    raise AssertionError("channel_webhook.make_server should not have been called")


@pytest.mark.unit
def test_run_refuses_to_start_when_secret_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channel_webhook, "make_server", _refusing_make_server)

    result = gateway_daemon.run(host="127.0.0.1", port=0, secret="", on_message=lambda event: (True, "unused"))

    assert result == 1


@pytest.mark.unit
def test_run_refuses_to_start_when_lock_is_already_held(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channel_webhook, "make_server", _refusing_make_server)

    lock_path = config.get_paths().state_dir / "gateway.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = open(lock_path, "a+")  # noqa: SIM115 - the flock must outlive this line, not close at a `with` block
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = gateway_daemon.run(
            host="127.0.0.1",
            port=0,
            secret="s3cr3t",  # pragma: allowlist secret - a fixed test fixture value
            on_message=lambda event: (True, "unused"),
        )
        assert result == 1
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()
