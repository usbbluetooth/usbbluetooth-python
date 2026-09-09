#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

from ..hci_hdr_type import HciHdrType
from .endpoint_reader import EndpointReader


# Largest HCI packet of each type, header included. HCI is the only layer in
# the stack that defines a message length, and it does so through the length
# field of each packet header, so the width of that field sets the maximum:
#
#   ACL data  handle and flags (2) + a 16 bit data length
#   SCO data  handle and flags (2) + an 8 bit data length
#   event     event code (1) + an 8 bit parameter length
#   ISO data  handle and flags (2) + a 14 bit data length, 2 bits reserved
#
# Core 5.4 Vol 4 Part B section 2.1.1 requires a whole HCI packet to sit in one
# USB transfer, which is what makes these the sizes a read has to be posted for.
_HCI_MAX_SIZES = {
    HciHdrType.ACL_DATA: 4 + 0xFFFF,
    HciHdrType.SCO_DATA: 3 + 0xFF,
    HciHdrType.EVENT: 2 + 0xFF,
    HciHdrType.ISO_DATA: 4 + 0x3FFF,
}


class HciEndpointReader(EndpointReader):
    """
    An EndpointReader for an endpoint carrying one kind of HCI packet.

    Adds the two things that are HCI rather than USB: which packet type the
    endpoint carries, and how large a packet of that type can be. The size is
    derived from the type rather than passed in, because the two are not
    independent -- it is the length field of that packet's own header that sets
    it.

    The USB transport gives each packet type its own endpoint instead of sending
    the type code on the wire, so the code is added back here on read.
    """

    def __init__(self, endpoint, packet_type):
        """
        :param endpoint: the pyusb IN endpoint to read from.
        :param packet_type: the HciHdrType the endpoint carries.
        :raises ValueError: if packets of that type are never read, which is the
            case for HCI commands: those are written, over the control endpoint.
        """
        if packet_type not in _HCI_MAX_SIZES:
            raise ValueError(
                f"{packet_type} is not a packet type a controller sends")
        super().__init__(endpoint, _HCI_MAX_SIZES[packet_type])
        self._packet_type = packet_type

    @property
    def packet_type(self):
        """The HciHdrType of the packets this endpoint carries."""
        return self._packet_type

    def read(self, timeout):
        """
        Read one HCI packet.

        :return: the packet prefixed with its HCI packet type byte, or None if
            nothing arrived before the timeout.
        """
        data = super().read(timeout)
        if data is None:
            return None
        return bytes([self._packet_type.value]) + data
