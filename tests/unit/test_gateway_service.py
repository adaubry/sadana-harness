"""Tests for sadana.gateway_service: the root-refusal paths only.

`install`/`start`/`stop`/`restart` all refuse before touching anything real
when not root — pytest never runs as root in this dev/CI environment (a real
condition, not a mock, matching `test_gateway_daemon.py`'s own precedent).
`status()` doesn't require root, so exercising it at all means touching the
real `/etc/systemd/system/` path outside any tmp sandboxing — that's
`scripts/prove_gateway_lifecycle_e2e.py`'s job, not this suite's.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest

from sadana import gateway_service, gateway_unit


@pytest.mark.unit
@pytest.mark.parametrize(
    "fn", [gateway_service.install, gateway_service.start, gateway_service.stop, gateway_service.restart]
)
def test_refuses_when_not_root(fn: Callable[[], int]) -> None:
    assert os.geteuid() != 0
    assert fn() == 1


@pytest.mark.unit
def test_install_refuses_before_writing_the_unit_file() -> None:
    assert os.geteuid() != 0
    # A real path, never redirected by this suite's tmp-dir isolation — a
    # read-only existence check is safe here specifically because install()
    # must refuse before ever reaching the write, so this never touches real
    # state: the file can only fail to exist, in either environment.
    assert not gateway_unit.unit_path().exists()
    assert gateway_service.install() == 1
    assert not gateway_unit.unit_path().exists()
