#!/usr/bin/env python

import usbbluetooth
from usbbluetooth import WrongDriverException


def reset_controller(dev):
    try:
        with dev as open_dev:
            print(f"Sending reset to {dev}")
            # Send a reset command
            open_dev.write(b"\x01\x03\x0c\x00")
            # Read the respose
            response = open_dev.read()
            print(f"Got response: {response}")
    except WrongDriverException:
        print(f"Could not open {dev}, wrong driver. Use Zadig to install WinUSB driver.")


# Get a list of all the available devices
devices = usbbluetooth.list_controllers()
for dev in devices:
    reset_controller(dev)
