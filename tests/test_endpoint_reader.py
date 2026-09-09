#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

"""The plain endpoint reader: an endpoint, a transfer size, and a floor.

It knows nothing about what the bytes mean. USB defines no message length -- an
endpoint descriptor carries only wMaxPacketSize, the size of one transaction --
so the size a read must be posted for comes from the protocol above and is
passed in.
"""

import pytest

from usbbluetooth.utils.endpoint_reader import EndpointReader

EVENT_PACKET_SIZE = 16      # wMaxPacketSize of the fake controller's 0x81
SOME_BYTES = b"\x0e\x04\x01\x03\x0c\x00"


@pytest.fixture
def reader(fake_controller):
    """A reader over the fake controller's interrupt IN endpoint."""
    controller, backend = fake_controller()
    controller.open()
    return EndpointReader(controller._event_reader.endpoint, 999), backend


def test_it_reads_the_bytes_unchanged(reader):
    endpoint_reader, backend = reader
    backend.events.append(SOME_BYTES)
    assert endpoint_reader.read(10) == SOME_BYTES


def test_it_reads_nothing_when_the_endpoint_is_quiet(reader):
    endpoint_reader, _ = reader
    assert endpoint_reader.read(10) is None


def test_it_starts_at_the_maximum_it_was_given(reader):
    endpoint_reader, _ = reader
    assert endpoint_reader.max_size == 999
    assert endpoint_reader.read_size == 999


def test_it_posts_a_transfer_of_its_read_size(reader):
    endpoint_reader, backend = reader
    endpoint_reader.read_size = 128
    endpoint_reader.read(10)
    assert [c for c in backend.log if c[0] == "intr_read"] == [
        ("intr_read", 0x81, 128)]


def test_the_floor_comes_from_the_endpoint(reader):
    endpoint_reader, _ = reader
    assert endpoint_reader.min_size == EVENT_PACKET_SIZE
    assert endpoint_reader.endpoint.wMaxPacketSize == EVENT_PACKET_SIZE


def test_a_read_size_below_one_usb_packet_is_refused(reader):
    """A transfer too small for a single USB packet can never be correct."""
    endpoint_reader, _ = reader
    with pytest.raises(ValueError, match="below"):
        endpoint_reader.read_size = EVENT_PACKET_SIZE - 1
    assert endpoint_reader.read_size == 999      # unchanged


def test_the_read_size_may_be_lowered_to_the_floor(reader):
    endpoint_reader, _ = reader
    endpoint_reader.read_size = EVENT_PACKET_SIZE
    assert endpoint_reader.read_size == EVENT_PACKET_SIZE


def test_a_read_size_above_the_maximum_is_refused(reader):
    """Nothing that big can arrive, so asking for it is a caller bug."""
    endpoint_reader, _ = reader
    with pytest.raises(ValueError, match="above"):
        endpoint_reader.read_size = 4096
    assert endpoint_reader.read_size == 999      # unchanged


def test_the_read_size_may_be_raised_back_to_the_maximum(reader):
    endpoint_reader, _ = reader
    endpoint_reader.read_size = 128
    endpoint_reader.read_size = endpoint_reader.max_size
    assert endpoint_reader.read_size == 999
