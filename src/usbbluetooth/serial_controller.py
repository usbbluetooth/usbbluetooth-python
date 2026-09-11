#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

import time

import serial
from serial.tools import list_ports

from .controller import Controller
from .hci_hdr_type import HciHdrType
from .exception.device_closed_exception import DeviceClosedException

# For each read packet type, the number of header bytes that follow the 1-byte
# packet-type indicator, and how to read the payload length out of that header.
#   Event : code(1) + plen(1)                       -> plen
#   ACL   : handle(2) + data_len(2, LE)             -> data_len
#   SCO   : handle(2) + data_len(1)                 -> data_len
#   ISO   : handle(2) + data_len(2, LE, low 14 bit) -> data_len
_H4_READ_LAYOUT = {
    HciHdrType.EVENT.value:    (2, lambda h: h[1]),
    HciHdrType.ACL_DATA.value: (4, lambda h: h[2] | (h[3] << 8)),
    HciHdrType.SCO_DATA.value: (3, lambda h: h[2]),
    HciHdrType.ISO_DATA.value: (4, lambda h: (h[2] | (h[3] << 8)) & 0x3FFF),
}


class SerialController(Controller):
    """
    A Bluetooth HCI controller reachable over a UART, using the H4 transport.
    """

    def __init__(self, port, baudrate=921600, rtscts=True, timeout=0.5):
        self._port = port
        self._baudrate = baudrate
        self._rtscts = rtscts
        self._timeout = timeout
        self._serial = None
        self.is_open = False

    def open(self):
        s = serial.Serial()
        s.port = self._port
        s.baudrate = self._baudrate
        s.rtscts = self._rtscts
        s.timeout = self._timeout
        s.open()
        s.reset_input_buffer()
        self._serial = s
        self.is_open = True

    def close(self):
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        self.is_open = False

    def write(self, data: bytes) -> int:
        """Write one HCI packet, including its H4 packet-type byte (``data[0]``).

        :return: the number of bytes written (the type byte included).
        """
        if not self.is_open:
            raise DeviceClosedException()
        return self._serial.write(bytes(data))

    def _read_exact(self, n: int, deadline: float):
        """Read exactly ``n`` bytes before ``deadline`` (monotonic seconds), or
        ``None`` if the packet did not arrive whole in time."""
        buf = bytearray()
        while len(buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._serial.timeout = remaining
            chunk = self._serial.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def read(self, bufsize=None, timeout=500):
        """Read the next HCI packet from the controller.

        :param bufsize: accepted for interface compatibility with
            :class:`Controller` and ignored (H4 framing is length-delimited).
        :param timeout: milliseconds to wait for the start of a packet.
        :return: the packet prefixed with its H4 packet-type byte, or ``None`` if
            no packet started within the timeout.
        """
        if not self.is_open:
            raise DeviceClosedException()
        # Wait up to `timeout` ms for a packet-type indicator.
        self._serial.timeout = timeout / 1000.0
        first = self._serial.read(1)
        if not first:
            return None
        ptype = first[0]
        layout = _H4_READ_LAYOUT.get(ptype)
        if layout is None:
            # Not a valid read packet-type indicator (stray/log byte): drop it
            # and let the next call resynchronise.
            return None
        hdr_len, payload_len_of = layout
        # Once a packet has started, give it a bounded window to arrive whole.
        deadline = time.monotonic() + max(timeout / 1000.0, 1.0)
        hdr = self._read_exact(hdr_len, deadline)
        if hdr is None:
            return None
        payload_len = payload_len_of(hdr)
        payload = self._read_exact(payload_len, deadline) if payload_len else b""
        if payload is None:
            return None
        return bytes([ptype]) + hdr + payload

    def _usb_ids(self):
        target = str(self._port).lower()
        for p in list_ports.comports():
            if str(p.device).lower() == target:
                return (p.vid, p.pid)
        return (None, None)

    @property
    def vendor_id(self):
        """USB VID of the underlying USB-serial bridge, or None if unavailable."""
        return self._usb_ids()[0]

    @property
    def product_id(self):
        """USB PID of the underlying USB-serial bridge, or None if unavailable."""
        return self._usb_ids()[1]

    def __str__(self) -> str:
        return f"SerialController{{port={self._port}, baud={self._baudrate}}}"
