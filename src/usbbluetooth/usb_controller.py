#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

import errno
import queue

import usb
from .controller import Controller
from .utils.hci_endpoint_reader import HciEndpointReader
from .utils.threaded_endpoint_reader import ThreadedEndpointReader
from .hci_hdr_type import HciHdrType
from .exception.endpoint_stalled_exception import EndpointStalledException
from .exception.wrong_driver_exception import WrongDriverException
from .exception.device_closed_exception import DeviceClosedException
from .exception.insufficient_permissions_exception import InsufficientPermissionsException
from .exception.unsupported_usb_device_exception import UnsupportedUsbDeviceException


class UsbController(Controller):
    """
    A Bluetooth HCI controller reached over USB (Core 5.4 Vol 4 Part B).
    """

    #: Packets held before a slow consumer starts losing the oldest ones. Large
    #: enough that ordinary request/response never fills it; a bound only matters
    #: for a flood of unsolicited events nobody is reading.
    _QUEUE_MAXSIZE = 1024
    #: How long each reader blocks on its endpoint before looping to check the
    #: stop flag. A packet returns as soon as it arrives, so this bounds only how
    #: quickly close() is noticed by an idle reader, not read() latency.
    _READER_POLL_MS = 200
    #: Seconds close() waits for a reader thread to finish its current transfer.
    _READER_JOIN_TIMEOUT = 2.0

    def __init__(self, usb_device):
        self._dev = usb_device
        self._interface_bt = None
        self._ep_acl_out = None
        self._event_reader = None
        self._acl_reader = None
        self._hci_cmd_request_type = None
        self._hci_cmd_index = None
        self.is_open = False
        # Receive path, set up by open() and torn down by close(). The queue is
        # shared by the per-endpoint readers; read() drains it.
        self._rx_queue = None
        self._readers = []

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
        if self._interface_bt is None:
            raise UnsupportedUsbDeviceException("Bluetooth interface")

        # Get the relevant endpoints. Table 2.1 puts HCI events on an interrupt
        # IN endpoint and ACL data on a bulk pair, so every lookup is qualified
        # by direction: without that an interrupt OUT endpoint would be accepted
        # as the event endpoint and every read of it would fail.
        #
        # This reads descriptors only, so it runs before the interface is
        # claimed. A device missing any of the three is then rejected without
        # the kernel driver having been touched.
        ep_events = usb.util.find_descriptor(
            self._interface_bt,
            bDescriptorType=usb.util.DESC_TYPE_ENDPOINT,
            bmAttributes=usb.util.ENDPOINT_TYPE_INTR,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )
        if ep_events is None:
            raise UnsupportedUsbDeviceException(
                "interrupt IN endpoint for HCI events")

        ep_acl_in = usb.util.find_descriptor(
            self._interface_bt,
            bDescriptorType=usb.util.DESC_TYPE_ENDPOINT,
            bmAttributes=usb.util.ENDPOINT_TYPE_BULK,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )
        if ep_acl_in is None:
            raise UnsupportedUsbDeviceException("bulk IN endpoint for ACL data")

        ep_acl_out = usb.util.find_descriptor(
            self._interface_bt,
            bDescriptorType=usb.util.DESC_TYPE_ENDPOINT,
            bmAttributes=usb.util.ENDPOINT_TYPE_BULK,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        if ep_acl_out is None:
            raise UnsupportedUsbDeviceException("bulk OUT endpoint for ACL data")

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

        self._event_reader = HciEndpointReader(ep_events, HciHdrType.EVENT)
        self._acl_reader = HciEndpointReader(ep_acl_in, HciHdrType.ACL_DATA)
        self._ep_acl_out = ep_acl_out

        # Work out how HCI command packets have to be addressed on endpoint 0.
        # This only depends on the descriptors, so resolve it once here.
        self._hci_cmd_request_type, self._hci_cmd_index = \
            self._hci_command_addressing()

        # Fresh receive state for this session. The readers are not started
        # here: they start on the first read() (see _start_readers).
        self._rx_queue = queue.Queue(maxsize=self._QUEUE_MAXSIZE)
        self._readers = []

        self.is_open = True

    def close(self):
        # Stop the readers before releasing the interface they read from: a
        # transfer in flight against a released interface would error. Each wakes
        # every _READER_POLL_MS to see the stop flag, so the join is brief.
        for reader in self._readers:
            reader.stop(self._READER_JOIN_TIMEOUT)
        self._readers = []

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

    def _clear_halt(self, endpoint_address):
        """
        Clear a halt condition on one of this device's endpoints, best effort.

        A halted endpoint fails every transfer that follows, and a halted
        control pipe has been observed taking a controller off the USB bus
        entirely. If the clear itself fails the device is most likely gone, and
        the caller is told about the stall regardless.
        """
        try:
            self._dev.clear_halt(endpoint_address)
        except (usb.core.USBError, NotImplementedError):
            # NotImplementedError: a backend that does not offer clear_halt at
            # all. Nothing to recover with, but the stall is still reported.
            pass

    def write(self, data: bytes) -> int:
        """
        Write one HCI packet, prefixed with its HCI packet type byte.

        :return: the number of bytes written, the type byte included.
        :raises EndpointStalledException: if the endpoint halted. The halt is
            cleared first, and the packet is known not to have been delivered.
        """
        if not self.is_open:
            raise DeviceClosedException()
        type = HciHdrType(data[0])
        if type == HciHdrType.COMMAND:
            # Commands are class requests on endpoint 0, so a stall halts the
            # control pipe rather than one of the data endpoints.
            try:
                sent_bytes = self._dev.ctrl_transfer(
                    bmRequestType=self._hci_cmd_request_type,
                    bRequest=0,
                    wValue=0,
                    wIndex=self._hci_cmd_index,
                    data_or_wLength=data[1:],
                )
                return sent_bytes + 1
            except usb.core.USBError as e:
                if e.errno != errno.EPIPE:
                    raise
                self._clear_halt(0x00)
                raise EndpointStalledException(0x00) from e
        elif type == HciHdrType.ACL_DATA:
            try:
                sent_bytes = self._ep_acl_out.write(data[1:])
                return sent_bytes + 1
            except usb.core.USBError as e:
                if e.errno != errno.EPIPE:
                    raise
                address = self._ep_acl_out.bEndpointAddress
                self._clear_halt(address)
                raise EndpointStalledException(address) from e
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

    @property
    def dropped_packets(self):
        """
        Packets discarded because the receive queue was full.

        A non-zero count means packets arrived faster than read() consumed them
        for long enough to fill the queue, and the oldest were dropped to keep
        the newest. Steady in ordinary request/response use; watch it under a
        flood of unsolicited events with no reader.
        """
        return sum(reader.dropped for reader in self._readers)

    def _start_readers(self):
        """Start one ThreadedEndpointReader per IN endpoint, once. Both feed the
        one shared queue, so read() returns from whichever delivers first."""
        if self._readers:
            return
        self._readers = [
            ThreadedEndpointReader(reader, self._rx_queue,
                                   poll_ms=self._READER_POLL_MS, name=name)
            for reader, name in (
                (self._event_reader, "usbbt-event-reader"),
                (self._acl_reader, "usbbt-acl-reader"),
            )
        ]
        for reader in self._readers:
            reader.start()

    def read(self, bufsize=None, timeout=500):
        """Read the next HCI packet from the controller, from either endpoint.

        The two IN endpoints are drained concurrently by per-endpoint reader
        threads (started on the first call), so this returns whichever produced
        a packet first without waiting out one endpoint before trying the other.

        :param bufsize: deprecated and ignored. Each endpoint is read with its
            own length; see event_parameter_total_length and acl_data_total_length.
        :param timeout: milliseconds to wait for a packet.
        :return: the packet prefixed with its HCI packet type byte, or None if
            neither endpoint produced one before the timeout.
        :raises EndpointStalledException: surfaced from a reader whose endpoint
            halted; the halt was cleared and the in-flight packet lost.
        """
        if not self.is_open:
            raise DeviceClosedException()
        self._start_readers()
        # A reader that fails puts its exception on the shared queue as an item,
        # so it surfaces here in order behind whatever was already queued; there
        # is no separate error channel to fall out of sync with.
        try:
            item = self._rx_queue.get(timeout=timeout / 1000.0)
        except queue.Empty:
            return None
        if isinstance(item, BaseException):
            raise item
        return item

    def __str__(self) -> str:
        return f"UsbController{{vid={hex(self._dev.idVendor)}, pid={hex(self._dev.idProduct)}}}"
