#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#


class WrongDriverException(Exception):
    """Exception raised when trying to access a device with wrong driver."""

    def __init__(self):
        super().__init__("The device you are trying to interact with is not "
                         "accessible. This is most likely because it is being "
                         "controlled by the wrong driver. In Windows, please "
                         "use Zadig to change the driver of your device to "
                         "WinUSB.")
