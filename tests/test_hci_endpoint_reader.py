#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

"""The HCI endpoint reader, and the read sizes a Controller exposes.

HCI is the layer that defines a message length, through the length field of each
packet header, so the maximum follows from the packet type rather than being
chosen. Core 5.4 Vol 4 Part B section 2.1.1 then requires a whole HCI packet to
sit in one USB transfer, which is what makes that maximum the size a read has to
be posted for.
"""

import pytest

from usbbluetooth import DeviceClosedException
from usbbluetooth.hci_hdr_type import HciHdrType
from usbbluetooth.utils.hci_endpoint_reader import HciEndpointReader

# Derived from the width of each packet header's length field.
EVENT_MAX = 2 + 0xFF        # event code, then an 8 bit parameter length
ACL_MAX = 4 + 0xFFFF        # handle and flags, then a 16 bit data length
SCO_MAX = 3 + 0xFF          # handle and flags, then an 8 bit data length
ISO_MAX = 4 + 0x3FFF        # handle and flags, then a 14 bit data length

EVENT_PACKET_SIZE = 16      # wMaxPacketSize of the fake controller's 0x81
ACL_PACKET_SIZE = 64        # wMaxPacketSize of the fake controller's 0x82

AN_EVENT = b"\x0e\x04\x01\x03\x0c\x00"


@pytest.fixture
def opened(fake_controller):
    controller, backend = fake_controller()
    controller.open()
    return controller, backend


# --------------------------------------------------------------------------
# The packet type, and the size that follows from it
# --------------------------------------------------------------------------

def test_a_reader_knows_the_packet_type_of_its_endpoint(opened):
    controller, _ = opened
    assert controller._event_reader.packet_type is HciHdrType.EVENT
    assert controller._acl_reader.packet_type is HciHdrType.ACL_DATA


@pytest.mark.parametrize("packet_type, max_size", [
    (HciHdrType.EVENT, EVENT_MAX),
    (HciHdrType.ACL_DATA, ACL_MAX),
    (HciHdrType.SCO_DATA, SCO_MAX),
    (HciHdrType.ISO_DATA, ISO_MAX),
])
def test_the_maximum_is_derived_from_the_packet_type(opened, packet_type,
                                                     max_size):
    controller, _ = opened
    reader = HciEndpointReader(controller._ep_events, packet_type)
    assert reader.max_size == max_size
    assert reader.read_size == max_size


def test_a_packet_type_a_controller_never_sends_is_refused(opened):
    """Commands are written, over the control endpoint, never read."""
    controller, _ = opened
    with pytest.raises(ValueError, match="never sends|not a packet type"):
        HciEndpointReader(controller._ep_events, HciHdrType.COMMAND)


def test_the_readers_a_controller_builds_get_the_right_sizes(opened):
    controller, _ = opened
    assert controller._event_reader.max_size == EVENT_MAX
    assert controller._acl_reader.max_size == ACL_MAX


# --------------------------------------------------------------------------
# The packet type byte, which the USB transport does not put on the wire
# --------------------------------------------------------------------------

def test_a_reader_prefixes_the_packet_type_it_carries(opened):
    controller, backend = opened
    backend.events.append(AN_EVENT)
    assert controller._event_reader.read(10) == (
        bytes([HciHdrType.EVENT.value]) + AN_EVENT)


def test_a_reader_returns_nothing_when_its_endpoint_is_quiet(opened):
    controller, _ = opened
    assert controller._event_reader.read(10) is None
    assert controller._acl_reader.read(10) is None


# --------------------------------------------------------------------------
# The floor is inherited from the plain reader
# --------------------------------------------------------------------------

def test_the_floor_is_still_the_endpoints_maximum_packet_size(opened):
    controller, _ = opened
    assert controller._event_reader.min_size == EVENT_PACKET_SIZE
    assert controller._acl_reader.min_size == ACL_PACKET_SIZE


def test_a_read_size_below_one_usb_packet_is_still_refused(opened):
    controller, _ = opened
    with pytest.raises(ValueError, match="below"):
        controller._event_reader.read_size = EVENT_PACKET_SIZE - 1


def test_a_read_size_above_the_derived_maximum_is_refused(opened):
    """No event can exceed what its 8 bit length field can describe."""
    controller, _ = opened
    with pytest.raises(ValueError, match="above"):
        controller._event_reader.read_size = EVENT_MAX + 1
    assert controller._event_reader.read_size == EVENT_MAX


def test_the_controller_sizes_refuse_too_large_a_value(opened):
    controller, _ = opened
    with pytest.raises(ValueError, match="above"):
        controller.acl_read_size = ACL_MAX + 1


# --------------------------------------------------------------------------
# What the Controller exposes
# --------------------------------------------------------------------------

def test_the_controller_exposes_each_size(opened):
    controller, _ = opened
    assert controller.event_read_size == EVENT_MAX
    assert controller.acl_read_size == ACL_MAX


def test_setting_a_size_changes_the_transfer_that_is_posted(opened):
    controller, backend = opened
    controller.acl_read_size = 683      # an HCI_Read_Buffer_Size figure
    controller.read(timeout=10)
    sizes = {c[0]: c[2] for c in backend.log if c[0].endswith("_read")}
    assert sizes == {"bulk_read": 683, "intr_read": EVENT_MAX}


def test_the_sizes_validate_the_same_way_as_the_reader(opened):
    controller, _ = opened
    with pytest.raises(ValueError, match="below"):
        controller.acl_read_size = ACL_PACKET_SIZE - 1


def test_the_sizes_need_an_open_controller(fake_controller):
    """They describe endpoints, which only exist once the controller is open."""
    controller, _ = fake_controller()
    with pytest.raises(DeviceClosedException):
        controller.event_read_size
    with pytest.raises(DeviceClosedException):
        controller.acl_read_size = 683
