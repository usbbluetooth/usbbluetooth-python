#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#


class InsufficientPermissionsException(Exception):
    """Exception raised when trying to access a device with insufficient permissions."""

    def __init__(self):
        super().__init__("Current user has insufficient permissions to access this device. Please, visit "
                         "https://usbbluetooth.github.io/quirks/ for more information. Depending on your operating "
                         "system, you might need to set up udev rules or run this program with elevated privileges.")
