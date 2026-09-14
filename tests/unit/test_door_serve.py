"""`door/serve.py` — the loopback development listener's own belt-and-braces
check.

The socket is deliberately absent, matching `test_editor_server.py`'s own
convention: `_from_this_machine` is pure and tested directly; the real
transport is proven with a curl transcript outside `make test` (`CLAUDE.md`:
"Prove a block's first real external round trip with a standalone script
outside `make test`"), not by binding a socket here.
"""

from __future__ import annotations

import pytest

from sadana.door.serve import _from_this_machine


@pytest.mark.unit
def test_a_request_from_this_machines_own_page_is_served() -> None:
    assert _from_this_machine({"Host": "127.0.0.1:7001", "Origin": "http://127.0.0.1:7001"}) is True


@pytest.mark.unit
def test_a_request_with_no_origin_header_is_served_if_the_host_is_loopback() -> None:
    """curl, or an operator's own script, sends no `Origin` at all."""
    assert _from_this_machine({"Host": "127.0.0.1:7001"}) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example.com"},
        {"Host": "127.0.0.1:7001", "Origin": "http://evil.example.com"},
        {},
    ],
)
def test_a_request_not_from_this_machine_is_refused(headers: dict) -> None:
    assert _from_this_machine(headers) is False


@pytest.mark.unit
def test_localhost_and_ipv6_loopback_are_also_this_machine() -> None:
    assert _from_this_machine({"Host": "localhost:7001"}) is True
    assert _from_this_machine({"Host": "[::1]:7001"}) is True
