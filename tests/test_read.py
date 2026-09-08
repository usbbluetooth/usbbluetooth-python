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


def test_the_private_readers_each_use_their_own_endpoint(opened):
    controller, backend = opened
    backend.events.append(AN_EVENT)
    backend.acl.append(SOME_ACL)

    assert controller._read_event(257, 10) == bytes([EVENT]) + AN_EVENT
    assert [c[0] for c in backend.log if c[0].endswith("_read")] == ["intr_read"]

    backend.log.clear()
    assert controller._read_acl(65539, 10) == bytes([ACL_DATA]) + SOME_ACL
    assert [c[0] for c in backend.log if c[0].endswith("_read")] == ["bulk_read"]


def test_a_reader_returns_nothing_when_its_endpoint_is_quiet(opened):
    controller, _ = opened
    assert controller._read_event(257, 10) is None
    assert controller._read_acl(65539, 10) is None


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


def test_read_passes_the_same_bufsize_to_both_endpoints(opened):
    """Also pinned: A2c gives each endpoint its own maximum instead."""
    controller, backend = opened
    controller.read(bufsize=777, timeout=10)
    sizes = {call[0]: call[2] for call in backend.log if call[0].endswith("_read")}
    assert sizes == {"bulk_read": 777, "intr_read": 777}
