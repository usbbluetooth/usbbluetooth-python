#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

"""Reading HCI packets back from the controller.

A controller delivers HCI events on an interrupt IN endpoint and ACL data on a
bulk IN endpoint (Core 5.4 Vol 4 Part B, Table 2.1), and the packet is handed
back prefixed with the HCI packet type byte that says which it was.

Since A6 the two IN endpoints are drained concurrently by reader threads feeding
one queue, so read() returns whichever produced a packet first rather than
polling one endpoint and then the other.
"""

import pytest

from usbbluetooth import DeviceClosedException

# HCI packet type bytes, i.e. the transport prefix.
ACL_DATA = 0x02
EVENT = 0x04

# A Command Complete for HCI_Reset, and a two byte ACL fragment.
AN_EVENT = b"\x0e\x04\x01\x03\x0c\x00"
SOME_ACL = b"\x01\x00\x02\x00\xaa\xbb"

# Comfortable upper bound for a read that is expected to succeed: a queued packet
# is returned the moment a reader thread delivers it, so a read only approaches
# this bound when nothing ever arrives.
WAIT_MS = 2000


@pytest.fixture
def opened(fake_controller):
    controller, backend = fake_controller()
    controller.open()
    return controller, backend


# --------------------------------------------------------------------------
# Each endpoint, on its own
# --------------------------------------------------------------------------

def test_an_event_comes_back_from_the_interrupt_endpoint(opened):
    controller, backend = opened
    backend.push_event(AN_EVENT)
    assert controller.read(timeout=WAIT_MS) == bytes([EVENT]) + AN_EVENT


def test_acl_data_comes_back_from_the_bulk_endpoint(opened):
    controller, backend = opened
    backend.push_acl(SOME_ACL)
    assert controller.read(timeout=WAIT_MS) == bytes([ACL_DATA]) + SOME_ACL


def test_nothing_pending_reads_nothing(opened):
    controller, _ = opened
    assert controller.read(timeout=10) is None


def test_reading_a_closed_controller_is_an_error(fake_controller):
    controller, _ = fake_controller()
    with pytest.raises(DeviceClosedException):
        controller.read(timeout=10)


# --------------------------------------------------------------------------
# How read() combines the two endpoints
# --------------------------------------------------------------------------

def test_read_returns_packets_from_both_endpoints(opened):
    """Both endpoints are drained concurrently, so both packets come back.

    The order between them is no longer guaranteed: read() used to poll ACL and
    then events (pinned by the old test_read_polls_acl_before_events), and A6
    removed that serialisation in favour of a reader thread per endpoint.
    """
    controller, backend = opened
    backend.push_event(AN_EVENT)
    backend.push_acl(SOME_ACL)
    got = {controller.read(timeout=WAIT_MS), controller.read(timeout=WAIT_MS)}
    assert got == {bytes([EVENT]) + AN_EVENT, bytes([ACL_DATA]) + SOME_ACL}


def test_each_endpoint_is_read_with_its_own_maximum(opened):
    """Per A2c, each endpoint posts its own transfer size, taken from that HCI
    packet header: an 8 bit length for events, a 16 bit length for ACL data."""
    controller, _ = opened
    assert controller._event_reader.read_size == 2 + 0xFF
    assert controller._acl_reader.read_size == 4 + 0xFFFF


def test_the_bufsize_argument_is_ignored(opened):
    """Kept for compatibility but deprecated: scapy passes MTU, which would over
    size every event read if it were honoured, so it must not change read_size."""
    controller, backend = opened
    backend.push_event(AN_EVENT)
    assert controller.read(bufsize=65535, timeout=WAIT_MS) == \
        bytes([EVENT]) + AN_EVENT
    assert controller._event_reader.read_size == 2 + 0xFF
    assert controller._acl_reader.read_size == 4 + 0xFFFF
