#!/usr/bin/env python

import usb
from .controller import Controller


def _usb_bt_controller_filter(dev):
    '''Custom filter function to identify USB Bluetooth controllers.'''
    # Devices that directly expose Bluetooth capabilities in the device
    # descriptor
    if (
        dev.bDeviceClass == usb.CLASS_WIRELESS_CONTROLLER
        and dev.bDeviceSubClass == usb.SUBCLASS_RF_CONTROLLER
        and dev.bDeviceProtocol == usb.PROTOCOL_BLUETOOTH_PRIMARY_CONTROLLER
    ):
        return True
    # Devices that list Bluetooth capabilities in one of their interfaces
    for cfg in dev:
        desc = usb.util.find_descriptor(
            cfg,
            bInterfaceClass=usb.CLASS_WIRELESS_CONTROLLER,
            bInterfaceSubClass=usb.SUBCLASS_RF_CONTROLLER,
            bInterfaceProtocol=usb.PROTOCOL_BLUETOOTH_PRIMARY_CONTROLLER,
        )
        if desc is not None:
            return True
    return False


def list_controllers():
    """List all connected controllers (USB Bluetooth devices)."""
    devs = usb.core.find(find_all=True, custom_match=_usb_bt_controller_filter)
    return [Controller(d) for d in devs]

