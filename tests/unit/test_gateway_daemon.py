"""Tests for sadana.gateway_daemon."""

from __future__ import annotations

import fcntl

import pytest

from sadana import config, gateway_daemon


def _refusing_make_server() -> None:
    raise AssertionError("make_server should not have been called")


@pytest.mark.unit
def test_run_refuses_to_start_when_lock_is_already_held() -> None:
    lock_path = config.get_paths().state_dir / "gateway.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = open(lock_path, "a+")  # noqa: SIM115 - the flock must outlive this line, not close at a `with` block
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = gateway_daemon.run(make_server=_refusing_make_server, lock_filename="gateway.lock")
        assert result == 1
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()


@pytest.mark.unit
def test_run_uses_the_given_lock_filename_not_a_hardcoded_one() -> None:
    """PLUGIN-MARKET-01's own reason `run()` takes `lock_filename`:
    `sadana gateway run` and `sadana marketplace serve-webhook` must lock
    on different files so one doesn't refuse because of the other. Held
    lock is `other.lock`; `run()` is asked for `gateway.lock` — it must
    reach the (refusing) `make_server`, not exit 1 on the wrong lock."""
    other_lock_path = config.get_paths().state_dir / "other.lock"
    other_lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = open(other_lock_path, "a+")  # noqa: SIM115 - the flock must outlive this line
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(AssertionError, match="make_server should not have been called"):
            gateway_daemon.run(make_server=_refusing_make_server, lock_filename="gateway.lock")
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()
