#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

"""The HCI endpoint reader, and the lengths a Controller exposes.

HCI is the layer that defines a message length, through the length field of each
packet header, so the maximum follows from the packet type rather than being
chosen. Core 5.4 Vol 4 Part B section 2.1.1 then requires a whole HCI packet to
sit in one USB transfer, which is what makes that maximum the size a read has to
be posted for.

The same field fixes the header, and the header is the difference between the
two ways of counting one read: USB counts the whole transfer, HCI counts only
the payload. Everything a caller quotes -- a packet's own length field, or
HCI_Read_Buffer_Size -- is the HCI count, so that is what the Controller
exposes, under the spec names of the fields it states.
"""

import pytest

from usbbluetooth import DeviceClosedException
from usbbluetooth.hci_hdr_type import HciHdrType
from usbbluetooth.utils.hci_endpoint_reader import HciEndpointReader

# The header in front of each payload, and the largest length the field behind
# it can describe. Core 5.4 Vol 4 Part E sections 5.4.2 to 5.4.5.
EVENT_HDR, EVENT_MAX_PARAM = 2, 0xFF    # Parameter_Total_Length,  8 bit
ACL_HDR, ACL_MAX_DATA = 4, 0xFFFF       # Data_Total_Length,      16 bit
SCO_HDR, SCO_MAX_DATA = 3, 0xFF         # Data_Total_Length,       8 bit
ISO_HDR, ISO_MAX_DATA = 4, 0x3FFF       # Data_Total_Length,      14 bit

# The transfer each of those adds up to.
EVENT_MAX = EVENT_HDR + EVENT_MAX_PARAM
ACL_MAX = ACL_HDR + ACL_MAX_DATA
SCO_MAX = SCO_HDR + SCO_MAX_DATA
ISO_MAX = ISO_HDR + ISO_MAX_DATA

EVENT_PACKET_SIZE = 16      # wMaxPacketSize of the fake controller's 0x81
ACL_PACKET_SIZE = 64        # wMaxPacketSize of the fake controller's 0x82

# ACL_Data_Packet_Length as both attached dongles report it in
# HCI_Read_Buffer_Size: the data portion only, so 4 bytes short of a transfer.
ACL_DATA_PACKET_LENGTH = 679

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
    reader = HciEndpointReader(controller._event_reader.endpoint, packet_type)
    assert reader.max_size == max_size
    assert reader.read_size == max_size


def test_a_packet_type_a_controller_never_sends_is_refused(opened):
    """Commands are written, over the control endpoint, never read."""
    controller, _ = opened
    with pytest.raises(ValueError, match="never sends|not a packet type"):
        HciEndpointReader(controller._event_reader.endpoint, HciHdrType.COMMAND)


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
# The header, and the two ways of counting one read
# --------------------------------------------------------------------------

@pytest.mark.parametrize("packet_type, header_size, length_field", [
    (HciHdrType.EVENT, EVENT_HDR, "Parameter_Total_Length"),
    (HciHdrType.ACL_DATA, ACL_HDR, "Data_Total_Length"),
    (HciHdrType.SCO_DATA, SCO_HDR, "Data_Total_Length"),
    (HciHdrType.ISO_DATA, ISO_HDR, "Data_Total_Length"),
])
def test_the_header_and_its_length_field_come_from_the_packet_type(
        opened, packet_type, header_size, length_field):
    controller, _ = opened
    reader = HciEndpointReader(controller._event_reader.endpoint, packet_type)
    assert reader.header_size == header_size
    assert reader.length_field == length_field


def test_the_payload_bounds_are_the_transfer_bounds_less_the_header(opened):
    controller, _ = opened
    reader = controller._event_reader
    assert reader.max_payload_size == EVENT_MAX_PARAM
    assert reader.max_payload_size == reader.max_size - EVENT_HDR
    assert reader.min_payload_size == EVENT_PACKET_SIZE - EVENT_HDR
    assert reader.min_payload_size == reader.min_size - EVENT_HDR


def test_a_payload_size_starts_at_all_the_length_field_can_describe(opened):
    controller, _ = opened
    assert controller._event_reader.payload_size == EVENT_MAX_PARAM
    assert controller._acl_reader.payload_size == ACL_MAX_DATA


def test_setting_a_payload_size_adds_the_header_back(opened):
    controller, backend = opened
    reader = controller._acl_reader
    reader.payload_size = ACL_DATA_PACKET_LENGTH
    assert reader.read_size == ACL_DATA_PACKET_LENGTH + ACL_HDR
    reader.read(10)
    assert [c for c in backend.log if c[0] == "bulk_read"] == [
        ("bulk_read", 0x82, ACL_DATA_PACKET_LENGTH + ACL_HDR)]


def test_reading_a_payload_size_takes_the_header_off_again(opened):
    controller, _ = opened
    reader = controller._acl_reader
    reader.read_size = ACL_DATA_PACKET_LENGTH + ACL_HDR
    assert reader.payload_size == ACL_DATA_PACKET_LENGTH


# --------------------------------------------------------------------------
# The bounds, inherited from the plain reader and translated to payloads
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


def test_a_payload_size_needing_too_small_a_transfer_is_refused(opened):
    """The bounds bite in payload units too, and say so in payload units.

    A caller that set a length must not be told off about a number it never
    mentioned, so the transfer that length implies is named alongside it.
    """
    controller, _ = opened
    reader = controller._event_reader
    with pytest.raises(ValueError, match="Parameter_Total_Length of 5 needs a "
                                         "7 byte transfer, and .*below"):
        reader.payload_size = 5
    assert reader.payload_size == EVENT_MAX_PARAM       # unchanged


def test_a_payload_size_the_length_field_cannot_describe_is_refused(opened):
    controller, _ = opened
    reader = controller._event_reader
    with pytest.raises(ValueError, match="Parameter_Total_Length of 256 needs "
                                         "a 258 byte transfer, and .*above"):
        reader.payload_size = EVENT_MAX_PARAM + 1
    assert reader.payload_size == EVENT_MAX_PARAM       # unchanged


def test_a_payload_size_may_be_lowered_to_the_floor_and_raised_back(opened):
    controller, _ = opened
    reader = controller._event_reader
    reader.payload_size = reader.min_payload_size
    assert reader.read_size == EVENT_PACKET_SIZE
    reader.payload_size = reader.max_payload_size
    assert reader.read_size == EVENT_MAX


# --------------------------------------------------------------------------
# What the Controller exposes: the length fields, under their spec names
# --------------------------------------------------------------------------

def test_the_controller_exposes_each_length_field(opened):
    controller, _ = opened
    assert controller.event_parameter_total_length == EVENT_MAX_PARAM
    assert controller.acl_data_total_length == ACL_MAX_DATA


def test_a_reported_acl_length_can_be_pushed_down_as_it_stands(opened):
    """The point of the spec names: HCI_Read_Buffer_Size goes straight across.

    ACL_Data_Packet_Length counts the data portion, so 679 has to become a 683
    byte transfer. Doing that here rather than in the caller is what stops the
    4 bytes going missing and truncating every full sized packet.
    """
    controller, backend = opened
    controller.acl_data_total_length = ACL_DATA_PACKET_LENGTH
    controller.read(timeout=10)
    sizes = {c[0]: c[2] for c in backend.log if c[0].endswith("_read")}
    assert sizes == {"bulk_read": ACL_DATA_PACKET_LENGTH + ACL_HDR,
                     "intr_read": EVENT_MAX}
    assert controller.acl_data_total_length == ACL_DATA_PACKET_LENGTH


def test_the_controller_lengths_refuse_too_large_a_value(opened):
    controller, _ = opened
    with pytest.raises(ValueError, match="above"):
        controller.acl_data_total_length = ACL_MAX_DATA + 1
    with pytest.raises(ValueError, match="above"):
        controller.event_parameter_total_length = EVENT_MAX_PARAM + 1


def test_the_controller_lengths_validate_the_same_way_as_the_reader(opened):
    controller, _ = opened
    with pytest.raises(ValueError, match="below"):
        controller.acl_data_total_length = ACL_PACKET_SIZE - ACL_HDR - 1


def test_the_lengths_need_an_open_controller(fake_controller):
    """They describe endpoints, which only exist once the controller is open."""
    controller, _ = fake_controller()
    with pytest.raises(DeviceClosedException):
        controller.event_parameter_total_length
    with pytest.raises(DeviceClosedException):
        controller.acl_data_total_length = ACL_DATA_PACKET_LENGTH
