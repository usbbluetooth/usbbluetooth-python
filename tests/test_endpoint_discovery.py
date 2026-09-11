#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

"""Which endpoints a Controller binds to, and what it does when they are absent.

Core 5.4 Vol 4 Part B Table 2.1 fixes the shape of the interface: HCI events on
an interrupt **IN** endpoint (suggested 0x81), ACL data on a bulk IN/OUT pair.
Direction is part of that definition, so every lookup has to match on it --
an endpoint found by transfer type alone can be the wrong way round, and a read
of it can only fail.

A device that does not have all three is rejected rather than half opened, and
rejected *before* the interface is claimed, so nothing has to be undone.
"""

import pytest
import usb.core
import usb.util

from fake_backend import BULK, INTR, FakeBackend, controller_descriptors

from usbbluetooth import UsbController, UnsupportedUsbDeviceException

EVENT_IN = (0x81, INTR, 16)
EVENT_OUT = (0x01, INTR, 16)
ACL_OUT = (0x02, BULK, 64)
ACL_IN = (0x82, BULK, 64)


def is_in(endpoint):
    return usb.util.endpoint_direction(
        endpoint.bEndpointAddress) == usb.util.ENDPOINT_IN


# --------------------------------------------------------------------------
# The endpoints a conformant controller offers
# --------------------------------------------------------------------------

def test_the_event_endpoint_is_the_interrupt_in_one(fake_controller):
    controller, _ = fake_controller()
    controller.open()
    assert controller._event_reader.endpoint.bEndpointAddress == 0x81
    assert is_in(controller._event_reader.endpoint)


def test_the_acl_endpoints_are_the_bulk_pair(fake_controller):
    controller, _ = fake_controller()
    controller.open()
    assert controller._acl_reader.endpoint.bEndpointAddress == 0x82
    assert is_in(controller._acl_reader.endpoint)
    assert controller._ep_acl_out.bEndpointAddress == 0x02
    assert not is_in(controller._ep_acl_out)


def test_an_interrupt_in_is_chosen_over_an_interrupt_out(fake_controller):
    """The direction match has to filter, not merely reject.

    With an interrupt OUT listed *before* the interrupt IN, matching on transfer
    type alone would bind the events reader to 0x01.
    """
    controller, _ = fake_controller(
        hci_endpoints=(EVENT_OUT, EVENT_IN, ACL_OUT, ACL_IN))
    controller.open()
    assert controller._event_reader.endpoint.bEndpointAddress == 0x81


# --------------------------------------------------------------------------
# Devices that do not match Table 2.1
# --------------------------------------------------------------------------

def test_an_interrupt_out_is_not_accepted_as_the_event_endpoint(
        fake_controller):
    """The A3 regression: this device used to open, then fail on every read."""
    controller, _ = fake_controller(
        hci_endpoints=(EVENT_OUT, ACL_OUT, ACL_IN))
    with pytest.raises(UnsupportedUsbDeviceException, match="interrupt IN"):
        controller.open()


@pytest.mark.parametrize("endpoints, missing", [
    ((ACL_OUT, ACL_IN), "interrupt IN endpoint for HCI events"),
    ((EVENT_IN, ACL_OUT), "bulk IN endpoint for ACL data"),
    ((EVENT_IN, ACL_IN), "bulk OUT endpoint for ACL data"),
])
def test_a_missing_endpoint_is_named_in_the_error(fake_controller, endpoints,
                                                  missing):
    controller, _ = fake_controller(hci_endpoints=endpoints)
    with pytest.raises(UnsupportedUsbDeviceException, match=missing):
        controller.open()


def test_a_device_with_no_bluetooth_interface_is_refused():
    """Previously an AttributeError on None, several lines later."""
    device, config, interfaces = controller_descriptors(sco=False)
    descriptor, _ = interfaces[(0, 0)]
    descriptor.bInterfaceClass = 0xFF       # no longer wireless controller
    backend = FakeBackend((device, config, interfaces))
    controller = UsbController(usb.core.find(backend=backend))
    with pytest.raises(UnsupportedUsbDeviceException, match="Bluetooth interface"):
        controller.open()


# --------------------------------------------------------------------------
# Rejection leaves the device as it was found
# --------------------------------------------------------------------------

def test_a_rejected_device_is_never_claimed(fake_controller):
    """Endpoint discovery reads descriptors, so it runs before the claim.

    Were it the other way round, refusing a device would leave its interface
    claimed and its kernel driver detached, with nothing to put them back.
    """
    controller, backend = fake_controller(
        hci_endpoints=(EVENT_OUT, ACL_OUT, ACL_IN))
    with pytest.raises(UnsupportedUsbDeviceException):
        controller.open()
    assert backend.calls("claim_interface") == []
    assert backend.calls("detach_kernel_driver") == []


def test_a_rejected_device_is_not_left_open(fake_controller):
    controller, _ = fake_controller(hci_endpoints=(ACL_OUT, ACL_IN))
    with pytest.raises(UnsupportedUsbDeviceException):
        controller.open()
    assert controller.is_open is False
