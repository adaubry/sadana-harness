"""Tests for sadana.door.run_control: register(), request_stop(), unregister()."""

from __future__ import annotations

import pytest

from sadana.door import run_control


@pytest.mark.unit
def test_register_then_request_stop_sets_the_event_and_returns_true() -> None:
    event = run_control.register("run-1")
    assert not event.is_set()

    assert run_control.request_stop("run-1") is True
    assert event.is_set()

    run_control.unregister("run-1")


@pytest.mark.unit
def test_request_stop_on_an_unregistered_id_returns_false() -> None:
    assert run_control.request_stop("never-registered") is False


@pytest.mark.unit
def test_unregister_then_request_stop_returns_false() -> None:
    run_control.register("run-2")
    run_control.unregister("run-2")

    assert run_control.request_stop("run-2") is False


@pytest.mark.unit
def test_unregister_on_an_id_never_registered_is_a_silent_no_op() -> None:
    run_control.unregister("never-here")  # must not raise


@pytest.mark.unit
def test_register_returns_a_fresh_event_each_time_even_for_the_same_id() -> None:
    first = run_control.register("run-3")
    second = run_control.register("run-3")

    assert first is not second
    # request_stop only ever reaches the currently registered event.
    assert run_control.request_stop("run-3") is True
    assert second.is_set()
    assert not first.is_set()

    run_control.unregister("run-3")
