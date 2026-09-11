#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Optional


class Controller(ABC):
    """
    Abstract Bluetooth HCI controller.
    """

    #: True while the transport is open.
    is_open: bool = False

    @abstractmethod
    def open(self) -> None:
        """Open the transport and make the controller ready for I/O."""

    @abstractmethod
    def close(self) -> None:
        """Close the transport and release any resources it holds."""

    @abstractmethod
    def write(self, data: bytes) -> int:
        """
        Write one HCI packet, prefixed with its HCI packet-type byte.

        :param data: the packet, ``data[0]`` being its HCI packet-type byte.
        :return: the number of bytes written, the type byte included.
        """

    @abstractmethod
    def read(self, bufsize: Optional[int] = None, timeout: int = 500) -> Optional[bytes]:
        """
        Read the next HCI packet from the controller.

        :param bufsize: kept for interface compatibility; may be ignored.
        :param timeout: milliseconds to wait for a packet.
        :return: the packet prefixed with its HCI packet-type byte, or ``None``
            if no packet arrived before the timeout.
        """

    @property
    @abstractmethod
    def vendor_id(self) -> Optional[int]:
        """USB Vendor ID associated with the controller, or ``None``."""

    @property
    @abstractmethod
    def product_id(self) -> Optional[int]:
        """USB Product ID associated with the controller, or ``None``."""

    def __enter__(self: "Controller") -> "Controller":
        self.open()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        self.close()
        return None
