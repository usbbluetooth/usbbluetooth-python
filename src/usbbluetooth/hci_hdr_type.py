#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

from enum import Enum


class HciHdrType(Enum):
    """
    HCI packet type, the transport prefix that identifies a packet.

    Core 5.4 Vol 4 Part A section 2 assigns these codes; the USB transport
    carries each type on its own endpoint rather than sending the code on the
    wire, so the value is added on read and stripped on write.
    """
    COMMAND = 0x01
    ACL_DATA = 0x02
    SCO_DATA = 0x03
    EVENT = 0x04
    ISO_DATA = 0x05
