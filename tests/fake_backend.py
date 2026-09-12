#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

"""A pyusb backend backed by synthetic descriptors instead of real hardware.

pyusb talks to hardware through :class:`usb.backend.IBackend`, and
:func:`usb.core.find` accepts a backend to use. Implementing that interface
over a descriptor tree built in Python lets the whole Controller lifecycle --
open, claim, transfer, close -- run with no USB stack present, and lets a test
assert on the exact transfers the library issued.

Descriptors default to a Controller shaped like the recommended configuration
of Core 5.4 Vol 4 Part B, Table 2.1: one interface with an interrupt IN for HCI
events plus bulk IN/OUT for ACL data, and a second interface with isochronous
alternate settings for SCO.
"""

import array
import threading
import time

import usb.backend
import usb.core

# Endpoint transfer types, as encoded in bmAttributes.
ISO = 1
BULK = 2
INTR = 3

# Bluetooth Controller codes, Core 5.4 Vol 4 Part B, section 3.1, Table 3.1.
BLUETOOTH_CLASS = (0xE0, 0x01, 0x01)
# Miscellaneous / common class / interface association: how a composite device
# that uses IADs identifies itself.
COMPOSITE_IAD_CLASS = (0xEF, 0x02, 0x01)
# A device whose class is defined per interface rather than device wide.
PER_INTERFACE_CLASS = (0x00, 0x00, 0x00)

# Table 2.1: HCI events on an interrupt IN, ACL data on bulk IN/OUT.
HCI_ENDPOINTS = ((0x81, INTR, 16), (0x02, BULK, 64), (0x82, BULK, 64))
# Table 2.1 alternate settings 0 to 5, by isochronous max packet size.
SCO_PACKET_SIZES = (0, 9, 17, 25, 33, 49)


class Desc:
    """A descriptor: pyusb only ever reads attributes off these."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def _endpoint(address, attributes, max_packet_size):
    return Desc(bLength=7, bDescriptorType=5, bEndpointAddress=address,
                bmAttributes=attributes, wMaxPacketSize=max_packet_size,
                bInterval=1, bRefresh=0, bSynchAddress=0,
                extra_descriptors=b"")


def _interface(number, alternate, endpoints, classes):
    return (Desc(bLength=9, bDescriptorType=4, bInterfaceNumber=number,
                 bAlternateSetting=alternate, bNumEndpoints=len(endpoints),
                 bInterfaceClass=classes[0], bInterfaceSubClass=classes[1],
                 bInterfaceProtocol=classes[2], iInterface=0,
                 extra_descriptors=b""),
            [_endpoint(*e) for e in endpoints])


def controller_descriptors(device_class=BLUETOOTH_CLASS, bt_interface=0,
                           sco=True, hci_endpoints=HCI_ENDPOINTS,
                           vendor_id=0x0A12, product_id=0x0001):
    """Build the descriptor tree of a USB Bluetooth Controller.

    :param device_class: the (class, subclass, protocol) triple of the Device
        Descriptor. ``BLUETOOTH_CLASS`` describes a single function Controller;
        anything else makes the Controller one function of a composite device.
    :param bt_interface: interface number carrying HCI events and ACL data.
        Any interface before it is filled with an unrelated vendor specific
        function, so that a composite layout can be described.
    :param sco: whether to add the SCO interface and its alternate settings.
    """
    device = Desc(bLength=18, bDescriptorType=1, bcdUSB=0x0200,
                  bDeviceClass=device_class[0],
                  bDeviceSubClass=device_class[1],
                  bDeviceProtocol=device_class[2],
                  bMaxPacketSize0=64, idVendor=vendor_id,
                  idProduct=product_id, bcdDevice=0x0100, iManufacturer=0,
                  iProduct=0, iSerialNumber=0, bNumConfigurations=1,
                  address=1, bus=1, port_number=1, port_numbers=(1,), speed=2)

    interfaces = {}
    # Unrelated functions sitting before the Controller in a composite device.
    for number in range(bt_interface):
        interfaces[(number, 0)] = _interface(
            number, 0, ((0x01 + number, BULK, 64),), (0xFF, 0xFF, 0xFF))
    # The Controller itself.
    interfaces[(bt_interface, 0)] = _interface(
        bt_interface, 0, hci_endpoints, BLUETOOTH_CLASS)
    if sco:
        for alternate, size in enumerate(SCO_PACKET_SIZES):
            interfaces[(bt_interface + 1, alternate)] = _interface(
                bt_interface + 1, alternate,
                ((0x03, ISO, size), (0x83, ISO, size)), BLUETOOTH_CLASS)

    numbers = sorted({n for (n, _) in interfaces})
    config = Desc(bLength=9, bDescriptorType=2, wTotalLength=0,
                  bNumInterfaces=len(numbers), bConfigurationValue=1,
                  iConfiguration=0, bmAttributes=0xE0, bMaxPower=50,
                  extra_descriptors=b"")
    return device, config, interfaces


class FakeBackend(usb.backend.IBackend):
    """Serves a synthetic descriptor tree and records every transfer.

    Only the parts of :class:`usb.backend.IBackend` the library actually uses
    are implemented; anything else raises the inherited NotImplementedError,
    which keeps the fake honest about what it covers.
    """

    def __init__(self, descriptors=None):
        self.device, self.config, self.interfaces = (
            descriptors or controller_descriptors())
        #: every transfer and lifecycle call, in order, as tuples
        self.log = []
        #: interface numbers currently claimed
        self.claimed = []
        #: interface number -> selected alternate setting
        self.altsettings = {}
        #: bytes handed back by successive interrupt reads
        self.events = []
        #: bytes handed back by successive bulk reads
        self.acl = []
        #: endpoint addresses currently halted. A halted endpoint fails every
        #: transfer with a pipe error until clear_halt is called for it, which
        #: is what makes recovery observable. Endpoint 0 is the control pipe.
        self.halted = set()
        #: Guards every field a reader thread touches (log, events, acl, halted)
        #: and lets a blocking read wait for data instead of spinning. The
        #: Controller drains each IN endpoint from its own thread, so the fake
        #: has to be safe under concurrent reads; see item A6.
        self._cond = threading.Condition()

    # -- helpers ----------------------------------------------------------

    @property
    def interface_numbers(self):
        return sorted({n for (n, _) in self.interfaces})

    def calls(self, kind):
        """Every logged call of the given kind (a thread-safe snapshot)."""
        with self._cond:
            return [entry for entry in self.log if entry[0] == kind]

    def log_snapshot(self):
        """A thread-safe copy of the whole call log."""
        with self._cond:
            return list(self.log)

    # -- injecting data / halts (wakes any waiting reader thread) ---------

    def push_event(self, data):
        """Queue an event for the interrupt IN endpoint and wake a waiter."""
        with self._cond:
            self.events.append(data)
            self._cond.notify_all()

    def push_acl(self, data):
        """Queue ACL data for the bulk IN endpoint and wake a waiter."""
        with self._cond:
            self.acl.append(data)
            self._cond.notify_all()

    def halt(self, ep):
        """Halt an endpoint and wake a waiter so it sees the stall at once."""
        with self._cond:
            self.halted.add(ep)
            self._cond.notify_all()

    @staticmethod
    def _timeout():
        # errno 110 ETIMEDOUT, as the libusb backend reports it
        return usb.core.USBTimeoutError("timed out", 110, 60)

    @staticmethod
    def _pipe_error():
        # errno 32 EPIPE, which is how pyusb surfaces LIBUSB_ERROR_PIPE, i.e.
        # a stalled endpoint. Third positional argument is the errno.
        return usb.core.USBError("Pipe error", 9, 32)

    def _fail_if_halted(self, ep):
        if ep in self.halted:
            raise self._pipe_error()

    def _fill(self, buff, data):
        length = min(len(data), len(buff))
        buff[:length] = array.array("B", bytes(data[:length]))
        return length

    # -- enumeration ------------------------------------------------------

    def enumerate_devices(self):
        yield self.device

    def get_device_descriptor(self, dev):
        return dev

    def get_configuration_descriptor(self, dev, config):
        if config != 0:
            raise IndexError("only one configuration is described")
        return self.config

    def get_interface_descriptor(self, dev, intf, alt, config):
        try:
            number = self.interface_numbers[intf]
        except IndexError:
            raise IndexError("no such interface")
        if (number, alt) not in self.interfaces:
            raise IndexError("no such alternate setting")
        return self.interfaces[(number, alt)][0]

    def get_endpoint_descriptor(self, dev, ep, intf, alt, config):
        number = self.interface_numbers[intf]
        return self.interfaces[(number, alt)][1][ep]

    # -- lifecycle --------------------------------------------------------

    def open_device(self, dev):
        return dev

    def close_device(self, handle):
        self.log.append(("close_device",))

    def get_configuration(self, handle):
        return self.config.bConfigurationValue

    def set_configuration(self, handle, value):
        self.log.append(("set_configuration", value))

    def claim_interface(self, handle, intf):
        self.log.append(("claim_interface", intf))
        self.claimed.append(intf)

    def release_interface(self, handle, intf):
        self.log.append(("release_interface", intf))
        if intf in self.claimed:
            self.claimed.remove(intf)

    def set_interface_altsetting(self, handle, intf, alt):
        self.log.append(("set_interface_altsetting", intf, alt))
        self.altsettings[intf] = alt

    def is_kernel_driver_active(self, handle, intf):
        return False

    def detach_kernel_driver(self, handle, intf):
        self.log.append(("detach_kernel_driver", intf))

    def attach_kernel_driver(self, handle, intf):
        self.log.append(("attach_kernel_driver", intf))

    def clear_halt(self, handle, ep):
        with self._cond:
            self.log.append(("clear_halt", ep))
            self.halted.discard(ep)
            self._cond.notify_all()

    # -- transfers --------------------------------------------------------

    def ctrl_transfer(self, handle, bmRequestType, bRequest, wValue, wIndex,
                      data, timeout):
        payload = bytes(data)
        with self._cond:
            self.log.append(("ctrl_transfer", bmRequestType, bRequest, wValue,
                             wIndex, payload))
            self._fail_if_halted(0x00)
        return len(payload)

    def _blocking_read(self, source, ep, buff, kind, timeout):
        """Shared body of intr_read / bulk_read.

        Waits (releasing the condition lock) until the endpoint has data, halts,
        or the timeout elapses, so a reader thread blocks like a real transfer
        instead of spinning and wakes the moment data is injected.
        """
        with self._cond:
            self.log.append((kind, ep, len(buff)))
            deadline = time.monotonic() + (timeout or 0) / 1000.0
            while True:
                self._fail_if_halted(ep)
                if source:
                    return self._fill(buff, source.pop(0))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._timeout()
                self._cond.wait(remaining)

    def intr_read(self, handle, ep, intf, buff, timeout):
        return self._blocking_read(self.events, ep, buff, "intr_read", timeout)

    def intr_write(self, handle, ep, intf, data, timeout):
        with self._cond:
            self.log.append(("intr_write", ep, bytes(data)))
        return len(data)

    def bulk_read(self, handle, ep, intf, buff, timeout):
        return self._blocking_read(self.acl, ep, buff, "bulk_read", timeout)

    def bulk_write(self, handle, ep, intf, data, timeout):
        with self._cond:
            self.log.append(("bulk_write", ep, bytes(data)))
            self._fail_if_halted(ep)
        return len(data)
