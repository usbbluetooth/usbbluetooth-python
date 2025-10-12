#!/usr/bin/env python


class DeviceClosedException(Exception):
    """Exception raised when interacting with a closed device."""

    def __init__(self):
        super().__init__("The device you are trying to interact with has not "
                         "been opened. Please, call the open() function "
                         "before reading or writing to a device. "
                         "Alternatively, use the `with` syntax to open and "
                         "close the device.")
