#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

"""Reading HCI packets back from the controller.

A controller delivers HCI events on an interrupt IN endpoint and ACL data on a
bulk IN endpoint (Core 5.4 Vol 4 Part B, Table 2.1), and the packet is handed
back prefixed with the HCI packet type byte that says which it was.
"""

import pytest

from usbbluetooth import DeviceClosedException

# HCI packet type bytes, i.e. the transport prefix.
ACL_DATA = 0x02
EVENT = 0x04

# A Command Complete for HCI_Reset, and a two byte ACL fragment.
AN_EVENT = b"\x0e\x04\x01\x03\x0c\x00"
SOME_ACL = b"\x01\x00\x02\x00\xaa\xbb"


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
    backend.events.append(AN_EVENT)
    assert controller.read(timeout=10) == bytes([EVENT]) + AN_EVENT
    assert [c[0] for c in backend.log if c[0].endswith("_read")] == [
        "bulk_read", "intr_read"]


def test_acl_data_comes_back_from_the_bulk_endpoint(opened):
    controller, backend = opened
    backend.acl.append(SOME_ACL)
    assert controller.read(timeout=10) == bytes([ACL_DATA]) + SOME_ACL


def test_nothing_pending_reads_nothing(opened):
    controller, _ = opened
    assert controller.read(timeout=10) is None


def test_reading_a_closed_controller_is_an_error(fake_controller):
    controller, _ = fake_controller()
    with pytest.raises(DeviceClosedException):
        controller.read(timeout=10)


# --------------------------------------------------------------------------
# How read() combines the two. Pinned here as it stands today; A2b changes it.
# --------------------------------------------------------------------------

def test_read_polls_acl_before_events(opened):
    """Current order, pinned so that changing it is a deliberate act.

    A2b will invert this -- section 2.4 gives events a latency requirement
    while bulk ACL is best effort -- and this test is expected to change with
    it.
    """
    controller, backend = opened
    backend.events.append(AN_EVENT)
    backend.acl.append(SOME_ACL)
    assert controller.read(timeout=10) == bytes([ACL_DATA]) + SOME_ACL


def test_each_endpoint_is_read_with_its_own_maximum(opened):
    """Replaces the pinned single-bufsize behaviour, per A2c.

    The sizes come from the HCI packet headers: an 8 bit length for events, a
    16 bit length for ACL data.
    """
    controller, backend = opened
    controller.read(timeout=10)
    sizes = {call[0]: call[2] for call in backend.log if call[0].endswith("_read")}
    assert sizes == {"intr_read": 2 + 0xFF, "bulk_read": 4 + 0xFFFF}


def test_the_bufsize_argument_is_ignored(opened):
    """Kept for compatibility but deprecated: scapy passes MTU, which would
    over size every event read if it were honoured."""
    controller, backend = opened
    controller.read(bufsize=65535, timeout=10)
    sizes = {call[0]: call[2] for call in backend.log if call[0].endswith("_read")}
    assert sizes == {"intr_read": 2 + 0xFF, "bulk_read": 4 + 0xFFFF}
