#!/usr/bin/env python

from enum import Enum


class HciHdrType(Enum):
    """HCI packet types."""
    COMMAND = 0x01
    ACL_DATA = 0x02
