#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

from enum import Enum


class HciHdrType(Enum):
    """HCI packet types."""
    COMMAND = 0x01
    ACL_DATA = 0x02
