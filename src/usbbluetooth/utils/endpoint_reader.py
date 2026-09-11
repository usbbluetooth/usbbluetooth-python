#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

import errno

import usb.core

from ..exception.endpoint_stalled_exception import EndpointStalledException


class EndpointReader:
    """Reads whole messages from one IN endpoint.

    Knows nothing about what the bytes mean: it owns an endpoint, the size of
    the transfer it posts, and the floor below which that size cannot go.

    On the size: USB defines no message length. An endpoint descriptor carries
    only wMaxPacketSize, the size of a single transaction, and a message is a
    run of those ended by a short packet. The length a read has to be sized for
    therefore comes from whatever protocol is riding on the endpoint, and is
    passed in.

    Getting it wrong does not fail cleanly. Ask for less than the device sends
    and, depending on the platform, the message is either split silently across
    reads or the pipe stalls.
    """

    def __init__(self, endpoint, max_size):
        """
        :param endpoint: the pyusb IN endpoint to read from.
        :param max_size: largest message the endpoint can carry. Becomes the
            default read size.
        """
        self._endpoint = endpoint
        self._max_size = max_size
        self._read_size = max_size

    @property
    def endpoint(self):
        """The pyusb endpoint being read."""
        return self._endpoint

    @property
    def min_size(self):
        """
        Smallest usable read: one USB packet.

        A transfer shorter than the endpoint's maximum packet size cannot hold
        even the first packet of a message, so it can never be correct. This is
        a property of the hardware, not a policy, and is always enforced.
        """
        return self._endpoint.wMaxPacketSize

    @property
    def max_size(self):
        """Largest message the endpoint can carry."""
        return self._max_size

    @property
    def read_size(self):
        """
        Size of the transfer this reader posts, defaulting to max_size.

        Must lie between min_size and max_size. Lower it only if the device
        has said it will never send more; setting it below what the device does
        send brings back the truncation this size exists to prevent.
        """
        return self._read_size

    @read_size.setter
    def read_size(self, size):
        if size < self.min_size:
            raise ValueError(
                f"read size {size} is below the {self.min_size} byte maximum "
                f"packet size of endpoint "
                f"0x{self._endpoint.bEndpointAddress:02x}; a transfer that "
                f"small cannot hold a single USB packet")
        if size > self._max_size:
            raise ValueError(
                f"read size {size} is above the {self._max_size} byte largest "
                f"message endpoint "
                f"0x{self._endpoint.bEndpointAddress:02x} can carry; nothing "
                f"that big can arrive on it")
        self._read_size = size

    def clear_halt(self):
        """
        Clear a halt condition on this endpoint, best effort.

        A halted endpoint fails every transfer that follows, so clearing it is
        what stands between one bad transfer and a controller that has to be
        physically reconnected. If the clear itself fails the device is most
        likely gone, and the caller is told about the stall regardless.
        """
        try:
            self._endpoint.clear_halt()
        except (usb.core.USBError, NotImplementedError):
            # NotImplementedError: a backend that does not offer clear_halt at
            # all. Nothing to recover with, but the stall is still reported.
            pass

    def read(self, timeout):
        """
        Read one message.

        :return: the bytes read, or None if nothing arrived before the timeout.
        :raises EndpointStalledException: if the endpoint halted. The halt is
            cleared first; the message that was in flight is lost either way,
            which is why this is raised rather than retried -- a retry would
            hand back the *next* message as if it were this one.
        """
        try:
            data = self._endpoint.read(self._read_size, timeout=timeout)
        except usb.core.USBTimeoutError:
            # A timeout is not an error here: it only means nothing arrived.
            # Caught before USBError, of which it is a subclass.
            return None
        except usb.core.USBError as e:
            if e.errno != errno.EPIPE:
                raise
            self.clear_halt()
            raise EndpointStalledException(
                self._endpoint.bEndpointAddress) from e
        if data is None or len(data) == 0:
            return None
        return bytes(data)
