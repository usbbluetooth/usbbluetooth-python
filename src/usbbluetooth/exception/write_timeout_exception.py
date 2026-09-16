#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#


class WriteTimeoutException(Exception):
    """Exception raised when a serial port does not accept a write in time."""

    def __init__(self, port, seconds, rtscts):
        reasons = ("the port is not an HCI controller and nothing is reading what is written to it (a device "
                   "console, for instance, accepts no HCI)")
        if rtscts:
            reasons += ", or hardware flow control is enabled but the controller never asserts CTS"
        super().__init__(f"Timed out after {seconds}s writing to {port}. This usually means that {reasons}. Check "
                         "that the port, baud rate and flow control match the controller's HCI UART configuration.")
