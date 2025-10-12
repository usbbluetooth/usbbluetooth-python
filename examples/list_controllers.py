#!/usr/bin/env python

import usbbluetooth

# Get a list of all the available devices
devices = usbbluetooth.list_controllers()
for dev in devices:
    print(dev)
