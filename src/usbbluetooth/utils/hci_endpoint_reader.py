#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

from ..hci_hdr_type import HciHdrType
from .endpoint_reader import EndpointReader


# What each HCI packet header says about length. HCI is the only layer in the
# stack that defines a message length, and it does so through a single field of
# each packet header, so that one field is where both numbers a read has to be
# sized from come from: the header carrying it, and the largest value it can
# hold. Core 5.4 Vol 4 Part E:
#
#   packet type  header  length field                    width
#   ACL data       4     Data_Total_Length (5.4.2)       16 bit
#   SCO data       3     Data_Total_Length (5.4.3)        8 bit
#   event          2     Parameter_Total_Length (5.4.4)   8 bit
#   ISO data       4     Data_Total_Length (5.4.5)       14 bit
#
# Core 5.4 Vol 4 Part B section 2.1.1 requires a whole HCI packet to sit in one
# USB transfer, which is what makes header plus largest length the size a read
# has to be posted for.
_HCI_LENGTH_FIELDS = {
    HciHdrType.ACL_DATA: ("Data_Total_Length", 4, 0xFFFF),
    HciHdrType.SCO_DATA: ("Data_Total_Length", 3, 0xFF),
    HciHdrType.EVENT: ("Parameter_Total_Length", 2, 0xFF),
    HciHdrType.ISO_DATA: ("Data_Total_Length", 4, 0x3FFF),
}


class HciEndpointReader(EndpointReader):
    """
    An EndpointReader for an endpoint carrying one kind of HCI packet.

    Adds the two things that are HCI rather than USB: which packet type the
    endpoint carries, and how large a packet of that type can be. The size is
    derived from the type rather than passed in, because the two are not
    independent -- it is the length field of that packet's own header that sets
    it.

    That same field is also what fixes the header size, and the header is the
    difference between the two ways of counting the same read. USB counts the
    whole transfer; HCI never counts its own header, so a length quoted by the
    layers above -- a packet's Data_Total_Length, or the
    ACL_Data_Packet_Length a controller reports in HCI_Read_Buffer_Size -- is
    the payload alone. Both counts are offered, ``read_size`` and
    ``payload_size``, and the header is added and removed here, where the
    packet type that sets it is already known.

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
        if packet_type not in _HCI_LENGTH_FIELDS:
            raise ValueError(
                f"{packet_type} is not a packet type a controller sends")
        length_field, header_size, max_length = _HCI_LENGTH_FIELDS[packet_type]
        super().__init__(endpoint, header_size + max_length)
        self._packet_type = packet_type
        self._length_field = length_field
        self._header_size = header_size

    @property
    def packet_type(self):
        """The HciHdrType of the packets this endpoint carries."""
        return self._packet_type

    @property
    def length_field(self):
        """
        Spec name of the header field this endpoint's packets state a length in.

        Names the units payload_size is counted in, so an error about one can
        say which field a caller got wrong.
        """
        return self._length_field

    @property
    def header_size(self):
        """Octets of HCI header in front of the payload, set by the type."""
        return self._header_size

    @property
    def min_payload_size(self):
        """min_size counted the way HCI counts. See payload_size."""
        return self.min_size - self._header_size

    @property
    def max_payload_size(self):
        """
        max_size counted the way HCI counts: all the length field can describe.

        See payload_size.
        """
        return self.max_size - self._header_size

    @property
    def payload_size(self):
        """
        read_size counted the way HCI counts, the header excluded.

        This is the value of the length field this endpoint's packets carry, so
        it is the number the layers above already hold: HCI_Read_Buffer_Size
        reports the data portion of an ACL packet, not the transfer it takes to
        carry one. Setting it adds the header back before the transfer is
        posted, and the resulting read_size is bounded exactly as it is when set
        directly.
        """
        return self.read_size - self._header_size

    @payload_size.setter
    def payload_size(self, size):
        try:
            self.read_size = size + self._header_size
        except ValueError as e:
            raise ValueError(
                f"a {self._length_field} of {size} needs a "
                f"{size + self._header_size} byte transfer, and {e}") from e

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
