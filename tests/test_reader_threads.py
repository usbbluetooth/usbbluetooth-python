#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

"""The reader-thread receive path (item A6).

Each IN endpoint is drained by its own daemon thread into one bounded queue, so
read() returns whichever endpoint produced a packet first instead of polling one
and then the other. The threads start on the first read(), are joined by close(),
surface their errors on the caller's next read(), and drop the oldest packet when
the queue overflows rather than blocking the reader.
"""

import time

import pytest

from usbbluetooth import EndpointStalledException, UsbController

EVENTS_EP = 0x81
ACL_IN_EP = 0x82

AN_EVENT = b"\x0e\x04\x01\x03\x0c\x00"
HCI_RESET = b"\x01\x03\x0c\x00"
WAIT_MS = 2000


@pytest.fixture
def opened(fake_controller):
    controller, backend = fake_controller()
    controller.open()
    return controller, backend


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

def test_readers_start_lazily_on_first_read(opened):
    controller, _ = opened
    assert controller._readers == []          # open() does not start them
    controller.read(timeout=10)
    assert len(controller._readers) == 2


def test_writing_does_not_start_readers(opened):
    """A caller that only writes never pays for the reader threads."""
    controller, _ = opened
    controller.write(HCI_RESET)
    assert controller._readers == []


def test_close_joins_the_readers(opened):
    controller, _ = opened
    controller.read(timeout=10)               # start them
    readers = list(controller._readers)
    assert readers and all(r.is_alive for r in readers)
    controller.close()
    assert all(not r.is_alive for r in readers)
    assert controller._readers == []


# --------------------------------------------------------------------------
# Errors surface on read(), one endpoint failing does not kill the other
# --------------------------------------------------------------------------

def test_a_reader_error_is_re_raised_on_read(opened):
    controller, backend = opened
    backend.halt(ACL_IN_EP)
    with pytest.raises(EndpointStalledException):
        controller.read(timeout=WAIT_MS)


def test_a_stalled_endpoint_does_not_kill_the_other(opened):
    """The ACL reader dying on a stall must not stop events being delivered."""
    controller, backend = opened
    backend.halt(ACL_IN_EP)
    with pytest.raises(EndpointStalledException):
        controller.read(timeout=WAIT_MS)      # surfaces the ACL stall
    backend.push_event(AN_EVENT)
    assert controller.read(timeout=WAIT_MS) == b"\x04" + AN_EVENT


# --------------------------------------------------------------------------
# A full queue drops the oldest and counts the drops
# --------------------------------------------------------------------------

def test_full_queue_drops_oldest_and_counts_them(fake_controller, monkeypatch):
    monkeypatch.setattr(UsbController, "_QUEUE_MAXSIZE", 4)
    controller, backend = fake_controller()
    controller.open()
    controller._start_readers()               # start without consuming a packet

    pushed = 20
    for i in range(pushed):
        backend.push_event(bytes([0x0e, 0x01, i]))

    # Wait for the reader to take every event off the backend, then settle so the
    # last enqueue-or-drop is accounted for.
    assert _wait_until(lambda: not backend.events)
    time.sleep(0.05)

    drained = 0
    while controller.read(timeout=10) is not None:
        drained += 1

    assert controller.dropped_packets > 0
    assert drained == 4                        # the queue bound
    assert drained + controller.dropped_packets == pushed
