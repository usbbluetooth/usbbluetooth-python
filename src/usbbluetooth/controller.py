#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

import usb
from .utils.hci_endpoint_reader import HciEndpointReader
from .hci_hdr_type import HciHdrType
from .exception.wrong_driver_exception import WrongDriverException
from .exception.device_closed_exception import DeviceClosedException
from .exception.insufficient_permissions_exception import InsufficientPermissionsException


class Controller:
    """Class representing a USB Bluetooth device."""

    def __init__(self, usb_device):
        self._dev = usb_device
        self._interface_bt = None
        self._ep_acl_out = None
        self._event_reader = None
        self._acl_reader = None
        self._hci_cmd_request_type = None
        self._hci_cmd_index = None
        self.is_open = False

    @property
    def vendor_id(self):
        return self._dev.idVendor

    @property
    def product_id(self):
        return self._dev.idProduct

    @property
    def is_single_function(self):
        """True if the whole USB device is a Bluetooth Controller and not a composite device."""
        return (self._dev.bDeviceClass == usb.CLASS_WIRELESS_CONTROLLER
                and self._dev.bDeviceSubClass == usb.SUBCLASS_RF_CONTROLLER
                and self._dev.bDeviceProtocol == usb.PROTOCOL_BLUETOOTH_PRIMARY_CONTROLLER)

    @property
    def is_composite(self):
        """True if the USB device is a composite device with a Bluetooth Controller function."""
        return not self.is_single_function

    def _hci_command_addressing(self):
        """
        Return the (bmRequestType, wIndex) to address HCI command packets.

        Endpoint 0 carries HCI commands as class requests, and the Setup Data
        can target either the device or an interface. A single function
        Controller is addressed as the device, with wIndex 0. A Controller
        inside a composite device is addressed as its interface, with wIndex
        selecting which interface that is.

        Composite devices are required to also accept device-addressed HCI
        commands and route them to the Controller function, so the device form
        is the safe one; the interface form is used only when the descriptors
        say the Controller really is one function among several.
        """
        if self.is_single_function:
            recipient = usb.util.CTRL_RECIPIENT_DEVICE
            index = 0x00
        else:
            recipient = usb.util.CTRL_RECIPIENT_INTERFACE
            index = self._interface_bt.bInterfaceNumber
        request_type = usb.util.build_request_type(
            usb.util.CTRL_OUT,
            usb.util.CTRL_TYPE_CLASS,
            recipient,
        )
        return request_type, index

    def open(self):
        # Try to get the active configuration...
        config = None
        try:
            config = self._dev.get_active_configuration()
        except NotImplementedError:
            # In windows, set_configuration is not implemented for devices not
            # running the correct driver, that should be WinUSB
            raise WrongDriverException()
        except usb.core.USBError as e:
            if e.errno == 13:
                # This happens in Linux when the user has insufficient permissions
                # to access the USB device
                raise InsufficientPermissionsException()
            else:
                # This may be anything, report it to the user...
                raise e

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
                self._dev.detach_kernel_driver(
                    self._interface_bt.bInterfaceNumber)
        except NotImplementedError:
            # In windows, is_kernel_driver_active and detach_kernel_driver are
            # not implemented
            pass

        # Claim the interface
        usb.util.claim_interface(
            self._dev, self._interface_bt.bInterfaceNumber)

        # Get the relevant endpoints.
        ep_events = usb.util.find_descriptor(
            self._interface_bt,
            bDescriptorType=usb.util.DESC_TYPE_ENDPOINT,
            bmAttributes=usb.util.ENDPOINT_TYPE_INTR,
        )
        self._event_reader = HciEndpointReader(ep_events, HciHdrType.EVENT)

        ep_acl_in = usb.util.find_descriptor(
            self._interface_bt,
            bDescriptorType=usb.util.DESC_TYPE_ENDPOINT,
            bmAttributes=usb.util.ENDPOINT_TYPE_BULK,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )
        self._acl_reader = HciEndpointReader(ep_acl_in, HciHdrType.ACL_DATA)

        self._ep_acl_out = usb.util.find_descriptor(
            self._interface_bt,
            bDescriptorType=usb.util.DESC_TYPE_ENDPOINT,
            bmAttributes=usb.util.ENDPOINT_TYPE_BULK,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )

        # Work out how HCI command packets have to be addressed on endpoint 0.
        # This only depends on the descriptors, so resolve it once here.
        self._hci_cmd_request_type, self._hci_cmd_index = \
            self._hci_command_addressing()

        self.is_open = True

    def close(self):
        # Check if we have information about the Bluetooth interface
        if hasattr(self, "_interface_bt") and self._interface_bt is not None:
            # Release the claimed interface
            usb.util.release_interface(
                self._dev, self._interface_bt.bInterfaceNumber)

            # Reattach the kernel driver
            try:
                if self._dev.is_kernel_driver_active(self._interface_bt.bInterfaceNumber) is False:
                    self._dev.attach_kernel_driver(
                        self._interface_bt.bInterfaceNumber)
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
            sent_bytes = self._dev.ctrl_transfer(
                bmRequestType=self._hci_cmd_request_type,
                bRequest=0,
                wValue=0,
                wIndex=self._hci_cmd_index,
                data_or_wLength=data[1:],
            )
            return sent_bytes + 1
        elif type == HciHdrType.ACL_DATA:
            sent_bytes = self._ep_acl_out.write(data[1:])
            return sent_bytes + 1
        else:
            raise ValueError(f"Unsupported HCI packet type: {type}")

    @property
    def event_parameter_total_length(self):
        """
        Largest Parameter_Total_Length an event read from here may state.

        Parameter_Total_Length (Core 5.4 Vol 4 Part E section 5.4.4) is the
        event's own length field, and like every HCI length it counts the
        parameters only. The two byte header is added on top when the transfer
        is posted, so a caller stays in the units HCI quotes and never adds it.

        Defaults to 0xFF, all an 8 bit field can describe. Lower it only if the
        controller has said it will never send more; setting it below what the
        controller does send brings back the truncation the length exists to
        prevent. It cannot go below the endpoint's maximum packet size less that
        header, since no smaller transfer can hold even one USB packet.
        """
        if not self.is_open:
            raise DeviceClosedException()
        return self._event_reader.payload_size

    @event_parameter_total_length.setter
    def event_parameter_total_length(self, length):
        if not self.is_open:
            raise DeviceClosedException()
        self._event_reader.payload_size = length

    @property
    def acl_data_total_length(self):
        """
        Largest Data_Total_Length an ACL packet read from here may state.

        As event_parameter_total_length, for HCI ACL data: Data_Total_Length
        (Core 5.4 Vol 4 Part E section 5.4.2), with a four byte header in front
        of it.

        The usual reason to lower this is ACL_Data_Packet_Length from
        HCI_Read_Buffer_Size, which is typically far below what the 16 bit field
        allows and is quoted in exactly these units, so it can be assigned
        straight across. Reading it is HCI knowledge, so it has to be pushed
        down from a layer that decodes commands rather than discovered here.
        """
        if not self.is_open:
            raise DeviceClosedException()
        return self._acl_reader.payload_size

    @acl_data_total_length.setter
    def acl_data_total_length(self, length):
        if not self.is_open:
            raise DeviceClosedException()
        self._acl_reader.payload_size = length

    def read(self, bufsize=None, timeout=500):
        """Read the next HCI packet from the controller, from either endpoint.

        :param bufsize: deprecated and ignored. Each endpoint is now read with
            its own length; see event_parameter_total_length and
            acl_data_total_length.
        :return: the packet prefixed with its HCI packet type byte, or None if
            neither endpoint produced one before the timeout.
        """
        if not self.is_open:
            raise DeviceClosedException()
        # Data endpoint
        packet = self._acl_reader.read(timeout)
        if packet is None:
            # Event endpoint
            packet = self._event_reader.read(timeout)
        return packet

    def __str__(self) -> str:
        return f"Controller{{vid={hex(self._dev.idVendor)}, pid={hex(self._dev.idProduct)}}}"
