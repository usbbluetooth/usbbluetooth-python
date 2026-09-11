#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#


class EndpointStalledException(Exception):
    """Exception raised when an endpoint halts and the transfer is lost."""

    def __init__(self, endpoint_address: int):
        super().__init__(f"Endpoint 0x{endpoint_address:02x} returned a pipe error, so the transfer did not complete "
                         "and any data in flight is lost. A clear halt was issued so that the controller stays "
                         "usable: an endpoint left halted fails every transfer that follows, and recovering it can "
                         "take a physical reconnection.")
