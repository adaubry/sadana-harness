"""Two people can use the box at once — H16's acceptance test, serving P8.

Integration tier, not unit: this touches real threads and the wall clock, and
`testing-conventions` forbids a unit test doing either.

**The barrier is the assertion; the clock is only evidence.** The intent first
asked for "two turns finish inside 300 ms", and `testing-conventions` rules
that out — "timing tests must not assume a quiet machine … no assertions that
depend on something not happening within a short window". A wall-clock upper
bound goes red because a laptop was busy, which says nothing about the code.
A `threading.Barrier(2)` that both turns must reach before either can proceed
is deterministic and proves *more*: not that the two turns were fast, but that
they were genuinely inside the model call at the same instant, which is the
property. The wall-clock figures are still measured and printed as evidence
for a person to read; nothing asserts them except the one lower bound, which
is safe because a loaded machine only makes it more true.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from conftest import make_runtime, open_connections, plain_response
from sadana import client_surface, conversation_store, ids, memory_store, model_access, stores


def _turn(runtime: client_surface.Runtime, key: str) -> client_surface.TurnOutcome:
    return asyncio.run(client_surface.take_turn(runtime, account="a", conversation=key, text="hi", create_as="chat"))


def _run_both(runtime: client_surface.Runtime, keys: tuple[str, str]) -> tuple[list, float]:
    """Both turns, on their own threads, started together. Returns what the
    model stub observed and how long the pair took."""
    outcomes: list[client_surface.TurnOutcome] = []
    threads = [threading.Thread(target=lambda k=key: outcomes.append(_turn(runtime, k))) for key in keys]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a turn never finished"
    return outcomes, time.monotonic() - started


@pytest.mark.integration
def test_two_conversations_take_their_turns_at_the_same_time(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The change H16 exists for.

    Before it, one process-wide lock was held for the whole of a turn —
    including the model round trip — so a second person's message waited for
    somebody else's sentence to finish. The barrier is what makes this a
    proof: if the two turns serialized, the first would block inside the model
    stub waiting for a partner that cannot arrive until the first returns, and
    the barrier would time out and fail the test by name.
    """
    barrier = threading.Barrier(2, timeout=10)

    def _send(_request: object) -> model_access.Response:
        barrier.wait()  # only passes if both turns are in flight together
        time.sleep(0.2)
        return plain_response("reply")

    monkeypatch.setattr(model_access, "send", _send)
    runtime = make_runtime(open_connections())

    outcomes, elapsed = _run_both(runtime, ("k-one", "k-two"))

    assert [o.ok for o in outcomes] == [True, True]
    with capsys.disabled():
        print(f"\n  two conversations, concurrently: {elapsed * 1000:.0f} ms for two 200 ms turns")


@pytest.mark.integration
def test_two_turns_on_one_conversation_do_not_overlap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The half that must stay serialized.

    No barrier here — it would deadlock, which is itself the point. Instead
    the stub records when each call entered and left, and the assertion is
    that the two intervals are disjoint: an event-based statement that needs
    no quiet machine. The wall-clock lower bound is asserted as well, because
    a *lower* bound only becomes more true as a machine gets busier.
    """
    spans: list[tuple[float, float]] = []
    guard = threading.Lock()

    def _send(_request: object) -> model_access.Response:
        entered = time.monotonic()
        time.sleep(0.2)
        with guard:
            spans.append((entered, time.monotonic()))
        return plain_response("reply")

    monkeypatch.setattr(model_access, "send", _send)
    runtime = make_runtime(open_connections())

    outcomes, elapsed = _run_both(runtime, ("k-same", "k-same"))

    assert [o.ok for o in outcomes] == [True, True]
    first, second = sorted(spans)
    assert first[1] <= second[0], "two turns on one conversation were inside the model call at once"
    assert elapsed > 0.4
    with capsys.disabled():
        print(f"  one conversation, twice: {elapsed * 1000:.0f} ms for two 200 ms turns")


@pytest.mark.integration
def test_a_reader_lists_conversations_while_a_turn_holds_its_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Somebody merely looking waits for nobody.

    This is the case a console hits constantly — a list request arriving while
    a turn is mid-sentence — and the one that made a read-only connection per
    thread worth having. The reader runs on its own thread, against its own
    connection, while the turn is provably inside the model call.
    """
    listing: list[int] = []
    reached_model = threading.Event()
    reader_done = threading.Event()
    connections = open_connections()

    def _send(_request: object) -> model_access.Response:
        reached_model.set()
        assert reader_done.wait(timeout=10), "the reader blocked behind a turn"
        return plain_response("reply")

    def _list() -> None:
        assert reached_model.wait(timeout=10)
        listing.append(len(conversation_store.search_conversations(connections.reader(), "", now=0.0)))
        reader_done.set()

    monkeypatch.setattr(model_access, "send", _send)
    runtime = make_runtime(connections)
    reader = threading.Thread(target=_list)
    reader.start()

    outcome = _turn(runtime, "k-read")
    reader.join(timeout=10)

    assert outcome.ok
    assert stores.conversation_lock("k-read").locked() is False
    assert listing == [1], "the reader saw the conversation the running turn had already created"


@pytest.mark.integration
def test_each_thread_gets_its_own_reader_connection(tmp_path: Path) -> None:
    """Thread-local, and deliberately held nowhere else.

    `hermes_state.py:4818-4845` kept exactly this plus a strong set so the
    connections could be closed on shutdown, and leaked two descriptors per
    worker thread until the process hit its file-descriptor limit and failed
    every request while staying alive. Without that second reference a reader
    dies with its thread.
    """
    connections = stores.Connections(tmp_path / "c.db")
    seen: list[int] = []

    def _read() -> None:
        seen.append(id(connections.reader()))

    threads = [threading.Thread(target=_read) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(set(seen)) == 3
    assert connections.reader() is connections.reader()


@pytest.mark.integration
def test_a_turn_never_reads_another_threads_uncommitted_rows(tmp_path: Path) -> None:
    """The defect H16's own deploy review caught, pinned.

    A SQLite transaction belongs to a *connection*, not a thread. While one
    thread sits inside `write_txn` on the writer, a read issued on that same
    writer from another thread runs inside the open transaction and returns
    rows that may still be rolled back. The per-conversation lock does not
    help: it partitions `conversations`, and `memory_entries` is shared across
    every conversation an account has.

    Concretely, before the fix: thread A's turn writes a memory entry in a
    transaction that then fails; thread B, composing a brand-new conversation's
    system message, reads the entry and bakes a fact that never existed into a
    prompt that is byte-stable for the life of that conversation.

    This asserts the property directly at the store, because that is where it
    is either true or false.
    """
    connections = stores.Connections(tmp_path / "c.db")
    in_transaction = threading.Event()
    read_done = threading.Event()
    seen: dict[str, list[str]] = {}

    def _write_then_fail() -> None:
        try:
            # Raw SQL rather than `memory_store.write_entry`, which opens its
            # own `write_txn` and cannot nest inside this one.
            with conversation_store.write_txn(connections.writer) as c:
                c.execute(
                    "INSERT INTO memory_entries (account_key, entry_key, content, updated_at, id, created_at) "
                    "VALUES ('a', 'ghost', 'never really happened', 1.0, ?, 1.0)",
                    (ids.make_id("mem"),),
                )
                in_transaction.set()
                read_done.wait(timeout=10)
                raise RuntimeError("this write does not survive")
        except RuntimeError:
            pass

    def _read_midway() -> None:
        assert in_transaction.wait(timeout=10)
        seen["reader"] = [e.entry_key for e in memory_store.list_entries(connections.reader(), "a")]
        read_done.set()

    writer = threading.Thread(target=_write_then_fail)
    reader = threading.Thread(target=_read_midway)
    writer.start()
    reader.start()
    for thread in (writer, reader):
        thread.join(timeout=20)
        assert not thread.is_alive()

    assert seen["reader"] == [], "a reader saw a row from another thread's open transaction"
    assert [e.entry_key for e in memory_store.list_entries(connections.writer, "a")] == []
