#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

import usbbluetooth

# Get a list of all the available devices
devices = usbbluetooth.list_controllers()
for dev in devices:
    print(dev)
