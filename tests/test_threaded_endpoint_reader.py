#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

"""The ThreadedEndpointReader in isolation.

It wraps anything with ``read(timeout_ms) -> bytes | None`` in a thread and puts
what it reads on a queue the caller owns, so it can be exercised against a plain
in-memory fake reader with no USB backend or Controller in sight. Several sharing
one queue, a failure travelling the queue as an item, and drop-oldest overflow
are all provable here rather than through the whole USB stack.
"""

import queue
import time

from usbbluetooth.utils.threaded_endpoint_reader import ThreadedEndpointReader

# Small poll so an idle reader loops (and so stop() returns) quickly under test.
POLL_MS = 20
# Sentinel the fake reader turns into a raised error on read().
_RAISE = object()


class FakeReader:
    """A minimal stand-in for HciEndpointReader.

    ``read(timeout_ms)`` blocks up to the timeout for a fed item and returns it,
    or None if none arrived; ``fail_with`` makes the next read raise.
    """

    def __init__(self):
        self._q = queue.Queue()
        self._error = None

    def feed(self, item):
        self._q.put(item)

    def fail_with(self, exc):
        self._error = exc
        self._q.put(_RAISE)

    def empty(self):
        return self._q.empty()

    def read(self, timeout):
        try:
            item = self._q.get(timeout=timeout / 1000.0)
        except queue.Empty:
            return None
        if item is _RAISE:
            raise self._error
        return item


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------

def test_start_makes_the_thread_alive_and_stop_ends_it():
    reader = ThreadedEndpointReader(FakeReader(), queue.Queue(), poll_ms=POLL_MS)
    assert not reader.is_alive
    reader.start()
    assert reader.is_alive
    reader.stop(1.0)
    assert not reader.is_alive


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------

def test_what_is_read_reaches_the_shared_queue():
    q = queue.Queue()
    fake = FakeReader()
    reader = ThreadedEndpointReader(fake, q, poll_ms=POLL_MS)
    reader.start()
    try:
        fake.feed(b"\x04hello")
        assert q.get(timeout=2.0) == b"\x04hello"
    finally:
        reader.stop(1.0)


def test_two_readers_feed_one_queue():
    q = queue.Queue()
    a, b = FakeReader(), FakeReader()
    ra = ThreadedEndpointReader(a, q, poll_ms=POLL_MS)
    rb = ThreadedEndpointReader(b, q, poll_ms=POLL_MS)
    ra.start()
    rb.start()
    try:
        a.feed(b"\x04a")
        b.feed(b"\x02b")
        got = {q.get(timeout=2.0), q.get(timeout=2.0)}
        assert got == {b"\x04a", b"\x02b"}
    finally:
        ra.stop(1.0)
        rb.stop(1.0)


# --------------------------------------------------------------------------
# A terminal error travels the queue as an item, and ends that reader
# --------------------------------------------------------------------------

def test_a_reader_error_is_put_on_the_queue_and_stops_the_reader():
    q = queue.Queue()
    fake = FakeReader()
    reader = ThreadedEndpointReader(fake, q, poll_ms=POLL_MS)
    reader.start()
    try:
        boom = RuntimeError("endpoint gone")
        fake.fail_with(boom)
        assert q.get(timeout=2.0) is boom
        assert _wait_until(lambda: not reader.is_alive)
    finally:
        reader.stop(1.0)


def test_packets_queued_before_an_error_come_out_before_it():
    q = queue.Queue()
    fake = FakeReader()
    reader = ThreadedEndpointReader(fake, q, poll_ms=POLL_MS)
    reader.start()
    try:
        boom = RuntimeError("later")
        fake.feed(b"\x04first")
        fake.fail_with(boom)
        assert q.get(timeout=2.0) == b"\x04first"
        assert q.get(timeout=2.0) is boom
    finally:
        reader.stop(1.0)


# --------------------------------------------------------------------------
# A full queue drops the oldest and counts it
# --------------------------------------------------------------------------

def test_full_queue_drops_oldest_and_counts_them():
    q = queue.Queue(maxsize=4)
    fake = FakeReader()
    reader = ThreadedEndpointReader(fake, q, poll_ms=POLL_MS)
    reader.start()
    try:
        pushed = 20
        for i in range(pushed):
            fake.feed(bytes([i]))
        # Wait for the reader to take every item off the fake, then settle.
        assert _wait_until(fake.empty)
        time.sleep(0.05)

        drained = []
        while not q.empty():
            drained.append(q.get_nowait())

        assert reader.dropped > 0
        assert len(drained) == 4                    # the queue bound
        assert len(drained) + reader.dropped == pushed
        # Drop-oldest keeps the newest: the last four pushed survive.
        assert drained == [bytes([i]) for i in range(16, 20)]
    finally:
        reader.stop(1.0)
