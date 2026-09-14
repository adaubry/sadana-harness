"""Tests for sadana.door.events: the bounded ephemeral-frame queue and push()."""

from __future__ import annotations

import queue

import pytest

from sadana.door import events


def _drain(q: queue.Queue[dict]) -> list[dict]:
    """Empties `q` and returns everything it held, in arrival order — used
    both to reset `events.ephemeral_queue` to a known-empty state before a
    test pushes its own frames, and to read back what a test pushed."""
    drained = []
    while True:
        try:
            drained.append(q.get_nowait())
        except queue.Empty:
            return drained


@pytest.mark.unit
def test_pushed_frames_arrive_in_non_decreasing_seq_order() -> None:
    _drain(events.ephemeral_queue)

    for seq in range(1, 4):
        events.push({"type": "ephemeral", "name": "message.delta", "data": {"seq": seq}})

    arrived = _drain(events.ephemeral_queue)
    assert [f["data"]["seq"] for f in arrived] == [1, 2, 3]


@pytest.mark.unit
def test_push_drops_the_oldest_frame_once_the_queue_is_full() -> None:
    _drain(events.ephemeral_queue)
    capacity = events.ephemeral_queue.maxsize
    assert capacity > 0, "the queue must be bounded for this test to mean anything"

    for seq in range(capacity + 1):
        events.push({"data": {"seq": seq}})

    arrived = _drain(events.ephemeral_queue)
    assert len(arrived) == capacity
    # The very first frame (seq=0) was dropped to make room for the last one.
    assert [f["data"]["seq"] for f in arrived] == list(range(1, capacity + 1))


@pytest.mark.unit
def test_ephemeral_queue_is_a_plain_bounded_queue() -> None:
    assert isinstance(events.ephemeral_queue, queue.Queue)
    assert events.ephemeral_queue.maxsize > 0
