#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

import usb
from .hci_hdr_type import HciHdrType
from .wrong_driver_exception import WrongDriverException
from .device_closed_exception import DeviceClosedException


class Controller:
    """Class representing a USB Bluetooth device."""

    def __init__(self, usb_device):
        self._dev = usb_device
        self._interface_bt = None
        self._ep_events = None
        self._ep_acl_in = None
        self._ep_acl_out = None
        self.is_open = False

    @property
    def vendor_id(self):
        return self._dev.idVendor

    @property
    def product_id(self):
        return self._dev.idProduct

    def open(self):
        # Try to get the active configuration...
        config = None
        try:
            config = self._dev.get_active_configuration()
        except NotImplementedError:
            # In windows, set_configuration is not implemented for devices not
            # running the correct driver, that should be WinUSB
            raise WrongDriverException()

        # Find the Bluetooth interface of the usb device...
        self._interface_bt = usb.util.find_descriptor(
            config,
            bInterfaceClass=usb.CLASS_WIRELESS_CONTROLLER,
            bInterfaceSubClass=usb.SUBCLASS_RF_CONTROLLER,
            bInterfaceProtocol=usb.PROTOCOL_BLUETOOTH_PRIMARY_CONTROLLER,
        )

        # Check if there is a kernel driver controlling the interface
        try:
            if self._dev.is_kernel_driver_active(self._interface_bt.bInterfaceNumber):
                # Detach the kernel driver
                self._dev.detach_kernel_driver(self._interface_bt.bInterfaceNumber)
        except NotImplementedError:
            # In windows, is_kernel_driver_active and detach_kernel_driver are
            # not implemented
            pass

        # Claim the interface
        usb.util.claim_interface(self._dev, self._interface_bt.bInterfaceNumber)

        # Get the relevant endpoints
        self._ep_events = usb.util.find_descriptor(
            self._interface_bt,
            bDescriptorType=usb.util.DESC_TYPE_ENDPOINT,
            bmAttributes=usb.util.ENDPOINT_TYPE_INTR,
        )
        self._ep_acl_in = usb.util.find_descriptor(
            self._interface_bt,
            bDescriptorType=usb.util.DESC_TYPE_ENDPOINT,
            bmAttributes=usb.util.ENDPOINT_TYPE_BULK,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )
        self._ep_acl_out = usb.util.find_descriptor(
            self._interface_bt,
            bDescriptorType=usb.util.DESC_TYPE_ENDPOINT,
            bmAttributes=usb.util.ENDPOINT_TYPE_BULK,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        self.is_open = True

    def close(self):
        # Release the claimed interface
        if hasattr(self, "_interface_bt") and self._interface_bt is not None:
            usb.util.release_interface(self._dev, self._interface_bt.bInterfaceNumber)

        # Reattach the kernel driver
        try:
            if self._dev.is_kernel_driver_active(self._interface_bt.bInterfaceNumber) is False:
                self._dev.attach_kernel_driver(self._interface_bt.bInterfaceNumber)
        except NotImplementedError:
            # In windows, is_kernel_driver_active and detach_kernel_driver are
            # not implemented
            pass

        self.is_open = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, type, value, tb):
        self.close()

    def write(self, data: bytearray) -> int:
        if not self.is_open:
            raise DeviceClosedException()
        type = HciHdrType(data[0])
        if type == HciHdrType.COMMAND:
            request_type = usb.util.build_request_type(
                usb.util.CTRL_OUT,
                usb.util.CTRL_TYPE_CLASS,
                usb.util.CTRL_RECIPIENT_INTERFACE,
            )
            sent_bytes = self._dev.ctrl_transfer(
                bmRequestType=request_type,
                bRequest=0,
                wValue=0,
                wIndex=self._interface_bt.bInterfaceNumber,
                data_or_wLength=data[1:],
            )
            return sent_bytes + 1
        elif type == HciHdrType.ACL_DATA:
            sent_bytes = self._ep_acl_out.write(data[1:])
            return sent_bytes + 1
        else:
            raise ValueError(f"Unsupported HCI packet type: {type}")

    def read(self, bufsize=1024):
        if not self.is_open:
            raise DeviceClosedException()
        # Data endpoint
        try:
            data_acl = self._ep_acl_in.read(bufsize, timeout=1)
            if data_acl and len(data_acl) > 0:
                return b"\x02" + data_acl
        except usb.core.USBTimeoutError:
            pass
        # Event endpoint
        try:
            data_evt = self._ep_events.read(bufsize, timeout=1)
            if data_evt and len(data_evt) > 0:
                return b"\x04" + data_evt
        except usb.core.USBTimeoutError:
            pass
        # Nothing to return
        return None

    def __str__(self) -> str:
        return f"Controller{{vid={hex(self._dev.idVendor)}, pid={hex(self._dev.idProduct)}}}"
