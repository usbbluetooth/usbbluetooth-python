#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

"""How HCI command packets are addressed on endpoint 0.

Core 5.4 Vol 4 Part B section 2.2 lets the Setup Data of an HCI command target
either the device or an interface, and gives one form for each kind of
Controller:

  ==========================  =============  ========  =======  =============
  Controller                  bmRequestType  bRequest  wValue   wIndex
  ==========================  =============  ========  =======  =============
  single function (2.2.1)     0x20 device    0x00      0x0000   0x0000
  composite       (2.2.2)     0x21 interface 0x00      0x0000   interface
  ==========================  =============  ========  =======  =============

Which one applies is decided from the Device Descriptor: section 3.1 Table 3.1
says a Bluetooth Controller carries 0xE0/0x01/0x01 there. Note this cannot be
decided by counting interfaces, because section 6.2 lets a single function
Controller own two or three of them (HCI and ACL, SCO, optionally firmware
upgrade).
"""

from types import SimpleNamespace

import pytest

from fake_backend import (BLUETOOTH_CLASS, COMPOSITE_IAD_CLASS,
                          PER_INTERFACE_CLASS)

from usbbluetooth import UsbController

# Section 2.2.1 and 2.2.2.
REQUEST_TYPE_DEVICE = 0x20
REQUEST_TYPE_INTERFACE = 0x21

HCI_RESET = b"\x01\x03\x0c\x00"


# --------------------------------------------------------------------------
# Recognising a single function Controller
# --------------------------------------------------------------------------

def _controller_with_device_class(classes):
    """A Controller wrapping nothing but a Device Descriptor."""
    return UsbController(SimpleNamespace(bDeviceClass=classes[0],
                                      bDeviceSubClass=classes[1],
                                      bDeviceProtocol=classes[2]))


@pytest.mark.parametrize("classes, single_function", [
    # Table 3.1: the codes of a Bluetooth Controller device.
    (BLUETOOTH_CLASS, True),
    # A composite device that associates its functions with an IAD.
    (COMPOSITE_IAD_CLASS, False),
    # A device that declares its classes per interface instead.
    (PER_INTERFACE_CLASS, False),
    # Wireless controller, but not a radio frequency one.
    ((0xE0, 0x02, 0x01), False),
    # Section 3.1: "The bDeviceProtocol value 0x04 is previously used."
    ((0xE0, 0x01, 0x04), False),
])
def test_single_function_is_read_from_the_device_descriptor(classes,
                                                            single_function):
    controller = _controller_with_device_class(classes)
    assert controller.is_single_function is single_function
    assert controller.is_composite is not single_function


def test_single_function_and_composite_are_opposites():
    for classes in (BLUETOOTH_CLASS, COMPOSITE_IAD_CLASS, PER_INTERFACE_CLASS):
        controller = _controller_with_device_class(classes)
        assert controller.is_composite != controller.is_single_function


# --------------------------------------------------------------------------
# Choosing the addressing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("classes, bt_interface, request_type, index", [
    # 2.2.1: a single function Controller is addressed as the device, and
    # wIndex stays 0 even when the Bluetooth interface is not interface 0.
    (BLUETOOTH_CLASS, 0, REQUEST_TYPE_DEVICE, 0x00),
    (BLUETOOTH_CLASS, 2, REQUEST_TYPE_DEVICE, 0x00),
    # 2.2.2: a Controller inside a composite device is addressed as its
    # interface, and wIndex selects which one.
    (COMPOSITE_IAD_CLASS, 2, REQUEST_TYPE_INTERFACE, 0x02),
    (PER_INTERFACE_CLASS, 4, REQUEST_TYPE_INTERFACE, 0x04),
])
def test_addressing_follows_the_kind_of_controller(fake_controller, classes,
                                                   bt_interface, request_type,
                                                   index):
    controller, _ = fake_controller(device_class=classes,
                                    bt_interface=bt_interface)
    controller.open()
    assert controller._interface_bt.bInterfaceNumber == bt_interface
    assert controller._hci_cmd_request_type == request_type
    assert controller._hci_cmd_index == index


def test_addressing_is_resolved_once_when_the_controller_is_opened(
        fake_controller):
    controller, _ = fake_controller()
    assert controller._hci_cmd_request_type is None
    assert controller._hci_cmd_index is None
    controller.open()
    assert controller._hci_cmd_request_type == REQUEST_TYPE_DEVICE
    assert controller._hci_cmd_index == 0x00


# --------------------------------------------------------------------------
# What actually reaches endpoint 0
# --------------------------------------------------------------------------

def test_a_command_from_a_single_function_controller_targets_the_device(
        fake_controller):
    controller, backend = fake_controller(device_class=BLUETOOTH_CLASS,
                                          bt_interface=0)
    controller.open()
    controller.write(HCI_RESET)
    # bmRequestType, bRequest, wValue, wIndex, payload
    assert backend.calls("ctrl_transfer") == [
        ("ctrl_transfer", REQUEST_TYPE_DEVICE, 0, 0, 0x00, HCI_RESET[1:])]


def test_a_command_from_a_composite_controller_targets_its_interface(
        fake_controller):
    controller, backend = fake_controller(device_class=COMPOSITE_IAD_CLASS,
                                          bt_interface=2)
    controller.open()
    controller.write(HCI_RESET)
    assert backend.calls("ctrl_transfer") == [
        ("ctrl_transfer", REQUEST_TYPE_INTERFACE, 0, 0, 0x02, HCI_RESET[1:])]


def test_a_single_function_controller_never_indexes_its_interface(
        fake_controller):
    """The interface number must not leak into wIndex under 2.2.1.

    Regression test: addressing the device while passing the interface number
    as wIndex matches neither form in section 2.2, and goes unnoticed on the
    common dongle where the Bluetooth interface happens to be number 0.
    """
    controller, backend = fake_controller(device_class=BLUETOOTH_CLASS,
                                          bt_interface=2)
    controller.open()
    controller.write(HCI_RESET)
    _, request_type, _, _, index, _ = backend.calls("ctrl_transfer")[0]
    assert (request_type, index) == (REQUEST_TYPE_DEVICE, 0x00)


def test_the_packet_type_byte_is_stripped_from_the_payload(fake_controller):
    """Endpoint 0 carries the HCI command itself, not its transport prefix."""
    controller, backend = fake_controller()
    controller.open()
    sent = controller.write(HCI_RESET)
    payload = backend.calls("ctrl_transfer")[0][5]
    assert payload == b"\x03\x0c\x00"
    assert sent == len(HCI_RESET)


# --------------------------------------------------------------------------
# Against a real controller
# --------------------------------------------------------------------------

@pytest.mark.hardware
def test_hardware_addressing_matches_the_descriptors(hardware_controller):
    controller = hardware_controller
    if controller.is_single_function:
        expected = (REQUEST_TYPE_DEVICE, 0x00)
    else:
        expected = (REQUEST_TYPE_INTERFACE,
                    controller._interface_bt.bInterfaceNumber)
    assert (controller._hci_cmd_request_type,
            controller._hci_cmd_index) == expected


@pytest.mark.hardware
def test_hardware_accepts_a_command_and_answers(hardware_controller):
    """A real controller must answer the addressing we chose for it."""
    controller = hardware_controller
    controller.write(HCI_RESET)
    for _ in range(8):
        event = controller.read(timeout=300)
        if event is not None:
            break
    assert event is not None, "no event after HCI_Reset"
    # 0x04 event packet, 0x0e Command Complete, for HCI_Reset (0x0c03)
    assert bytes(event)[:6] == b"\x04\x0e\x04\x01\x03\x0c"
