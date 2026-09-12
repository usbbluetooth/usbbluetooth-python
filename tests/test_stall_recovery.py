#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

"""What happens when an endpoint halts.

A halted endpoint fails every transfer that follows it, so leaving one halted
turns a single bad transfer into a dead controller: the reported case needed the
dongle physically reconnected, and a halted *control* pipe took one off the USB
bus altogether. Clearing the halt is what stands between the two.

The transfer that hit the stall is still lost, so it is reported rather than
retried. A retry on an IN endpoint would hand back the *next* packet as though
it were the one that was lost, which is the same class of bug as A2's truncated
reads being returned as whole packets.
"""

import pytest
import usb.core

from usbbluetooth import EndpointStalledException

EVENTS_EP = 0x81
ACL_IN_EP = 0x82
ACL_OUT_EP = 0x02
CONTROL_EP = 0x00

AN_EVENT = b"\x0e\x04\x01\x03\x0c\x00"
SOME_ACL = b"\x01\x00\x02\x00\xaa\xbb"
HCI_RESET = b"\x01\x03\x0c\x00"
AN_ACL_WRITE = b"\x02\x01\x00\x02\x00\xaa\xbb"

# Upper bound for a read expected to raise a stall from a reader thread: it is
# re-raised as soon as the thread hits it, well before this elapses.
WAIT_MS = 2000


@pytest.fixture
def opened(fake_controller):
    controller, backend = fake_controller()
    controller.open()
    return controller, backend


# --------------------------------------------------------------------------
# Reading a halted endpoint
# --------------------------------------------------------------------------

def test_a_halted_event_endpoint_is_reported(opened):
    controller, backend = opened
    backend.halted.add(EVENTS_EP)
    with pytest.raises(EndpointStalledException, match="0x81"):
        controller._event_reader.read(10)


def test_a_halted_event_endpoint_is_cleared(opened):
    controller, backend = opened
    backend.halted.add(EVENTS_EP)
    with pytest.raises(EndpointStalledException):
        controller._event_reader.read(10)
    assert backend.calls("clear_halt") == [("clear_halt", EVENTS_EP)]


def test_the_endpoint_works_again_after_the_halt_is_cleared(opened):
    """The whole point: one bad transfer must not end the session."""
    controller, backend = opened
    backend.halted.add(EVENTS_EP)
    with pytest.raises(EndpointStalledException):
        controller._event_reader.read(10)

    backend.events.append(AN_EVENT)
    assert controller._event_reader.read(10) == b"\x04" + AN_EVENT


def test_a_halted_acl_endpoint_is_reported_and_cleared(opened):
    controller, backend = opened
    backend.halted.add(ACL_IN_EP)
    with pytest.raises(EndpointStalledException, match="0x82"):
        controller._acl_reader.read(10)
    assert backend.calls("clear_halt") == [("clear_halt", ACL_IN_EP)]


def test_read_surfaces_a_stall_rather_than_reporting_no_data(opened):
    """read() returning None means "nothing arrived", which a stall is not.

    read() drains the endpoints from reader threads, so the stall is hit off the
    caller's thread and re-raised here; halt() wakes the waiting reader at once.
    """
    controller, backend = opened
    backend.halt(ACL_IN_EP)
    with pytest.raises(EndpointStalledException):
        controller.read(timeout=WAIT_MS)


def test_a_stall_is_never_swallowed_as_a_retry(opened):
    """A retry would return the next packet as if it were the lost one."""
    controller, backend = opened
    backend.halted.add(EVENTS_EP)
    backend.events.append(AN_EVENT)
    with pytest.raises(EndpointStalledException):
        controller._event_reader.read(10)
    # The queued event is still queued: nothing was consumed on its behalf.
    assert backend.events == [AN_EVENT]


# --------------------------------------------------------------------------
# Writing to a halted endpoint
# --------------------------------------------------------------------------

def test_a_halted_control_pipe_is_reported_and_cleared(opened):
    """This is the case that took a dongle off the bus entirely."""
    controller, backend = opened
    backend.halted.add(CONTROL_EP)
    with pytest.raises(EndpointStalledException, match="0x00"):
        controller.write(HCI_RESET)
    assert backend.calls("clear_halt") == [("clear_halt", CONTROL_EP)]


def test_commands_work_again_after_the_control_pipe_recovers(opened):
    controller, backend = opened
    backend.halted.add(CONTROL_EP)
    with pytest.raises(EndpointStalledException):
        controller.write(HCI_RESET)
    assert controller.write(HCI_RESET) == len(HCI_RESET)


def test_a_halted_acl_out_endpoint_is_reported_and_cleared(opened):
    controller, backend = opened
    backend.halted.add(ACL_OUT_EP)
    with pytest.raises(EndpointStalledException, match="0x02"):
        controller.write(AN_ACL_WRITE)
    assert backend.calls("clear_halt") == [("clear_halt", ACL_OUT_EP)]


# --------------------------------------------------------------------------
# Only a pipe error is treated this way
# --------------------------------------------------------------------------

def test_other_usb_errors_are_not_mistaken_for_a_stall(opened, monkeypatch):
    """Only errno 32 is recoverable by clearing a halt; the rest pass through."""
    controller, backend = opened

    def other_error(*args, **kwargs):
        raise usb.core.USBError("No such device", 19, 19)

    monkeypatch.setattr(controller._event_reader.endpoint, "read", other_error)
    with pytest.raises(usb.core.USBError) as caught:
        controller._event_reader.read(10)
    assert not isinstance(caught.value, EndpointStalledException)
    assert backend.calls("clear_halt") == []


def test_a_timeout_is_still_just_nothing_to_read(opened):
    """USBTimeoutError subclasses USBError, so ordering matters."""
    controller, backend = opened
    assert controller._event_reader.read(10) is None
    assert backend.calls("clear_halt") == []


def test_a_failing_clear_halt_still_reports_the_stall(opened, monkeypatch):
    """If the device is gone the clear fails, but the caller must still know."""
    controller, backend = opened
    backend.halted.add(EVENTS_EP)

    def refuse(*args, **kwargs):
        raise usb.core.USBError("No such device", 19, 19)

    monkeypatch.setattr(controller._event_reader.endpoint, "clear_halt", refuse)
    with pytest.raises(EndpointStalledException):
        controller._event_reader.read(10)


def test_a_backend_without_clear_halt_still_reports_the_stall(opened,
                                                              monkeypatch):
    """IBackend.clear_halt raises NotImplementedError when unimplemented."""
    controller, backend = opened
    backend.halted.add(EVENTS_EP)

    def unimplemented(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr(controller._event_reader.endpoint, "clear_halt",
                        unimplemented)
    with pytest.raises(EndpointStalledException):
        controller._event_reader.read(10)
