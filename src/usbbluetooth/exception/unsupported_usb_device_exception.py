#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#


class UnsupportedUsbDeviceException(Exception):
    """Exception raised when a device does not expose a usable Bluetooth Controller function."""

    def __init__(self, missing: str):
        super().__init__(f"This device does not expose a usable Bluetooth Controller function: no {missing} was "
                         "found. Core 5.4 Vol 4 Part B Table 2.1 requires a Bluetooth interface carrying an "
                         "interrupt IN endpoint for HCI events, plus bulk IN and OUT endpoints for ACL data.")
