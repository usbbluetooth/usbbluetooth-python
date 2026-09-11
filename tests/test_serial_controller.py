#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

import pytest

from usbbluetooth import serial_controller
from usbbluetooth.serial_controller import SerialController
from usbbluetooth.exception.device_closed_exception import DeviceClosedException


class FakeSerial:
    """Minimal pyserial.Serial stand-in backed by an in-memory RX buffer."""

    def __init__(self):
        self.port = None
        self.baudrate = None
        self.rtscts = None
        self.timeout = None
        self.is_open = False
        self.written = bytearray()
        self._rx = bytearray()

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.written += bytes(data)
        return len(data)

    def read(self, n):
        chunk = bytes(self._rx[:n])
        del self._rx[:len(chunk)]
        return chunk

    def feed(self, data):
        self._rx += bytes(data)


class FakePySerial:
    def __init__(self, fake):
        self._fake = fake

    def Serial(self):
        return self._fake


@pytest.fixture
def fake(monkeypatch):
    f = FakeSerial()
    monkeypatch.setattr(serial_controller, "serial", FakePySerial(f))
    return f


def _open(fake, **kw):
    c = SerialController("COMX", **kw)
    c.open()
    return c


def test_open_sets_port_params(fake):
    c = _open(fake, baudrate=921600, rtscts=True)
    assert fake.is_open is True
    assert fake.port == "COMX"
    assert fake.baudrate == 921600
    assert fake.rtscts is True
    assert c.is_open is True
    c.close()
    assert fake.is_open is False


def test_write_sends_packet_verbatim_with_type_byte(fake):
    c = _open(fake)
    # HCI_Reset command, H4: type 0x01 + opcode 0x0C03 + plen 0x00
    n = c.write(bytes([0x01, 0x03, 0x0C, 0x00]))
    assert n == 4
    assert bytes(fake.written) == bytes([0x01, 0x03, 0x0C, 0x00])


def test_write_when_closed_raises(fake):
    c = SerialController("COMX")
    with pytest.raises(DeviceClosedException):
        c.write(b"\x01\x03\x0c\x00")


def test_read_event_packet(fake):
    c = _open(fake)
    # Command Complete for HCI_Reset: 04 0E 04 01 03 0C 00
    frame = bytes.fromhex("040e0401030c00")
    fake.feed(frame)
    assert c.read(timeout=100) == frame


def test_read_acl_packet_little_endian_length(fake):
    c = _open(fake)
    # ACL: 02 handle=0x0001 len=0x0004 (LE) + 4 payload bytes
    frame = bytes.fromhex("0201000400") + b"\xaa\xbb\xcc\xdd"
    fake.feed(frame)
    assert c.read(timeout=100) == frame


def test_read_reassembles_across_multiple_reads(fake):
    c = _open(fake)
    # The FakeSerial hands out at most `n` bytes per read, so a multi-field
    # packet is naturally delivered in several reads; it must still reassemble.
    frame = bytes.fromhex("040e0c0501100009160009e5021600")
    fake.feed(frame)
    assert c.read(timeout=100) == frame


def test_read_timeout_returns_none(fake):
    c = _open(fake)
    assert c.read(timeout=10) is None


def test_read_unknown_type_byte_is_dropped(fake):
    c = _open(fake)
    # 0x01 (Command) is host->controller and not a valid *read* type: drop it.
    fake.feed(b"\x01\x02\x03")
    assert c.read(timeout=10) is None


def test_read_when_closed_raises(fake):
    c = SerialController("COMX")
    with pytest.raises(DeviceClosedException):
        c.read()
