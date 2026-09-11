#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

import argparse

from usbbluetooth import SerialController


def reset_controller(port, baudrate, rtscts):
    dev = SerialController(port, baudrate=baudrate, rtscts=rtscts)
    with dev as open_dev:
        print(f"Sending reset to {dev}")
        # Send an HCI reset command (H4: type 0x01 + opcode 0x0C03 + plen 0x00)
        open_dev.write(b"\x01\x03\x0c\x00")
        # Read the response
        response = open_dev.read()
        print(f"Got response: {response.hex() if response else None}")


def main():
    parser = argparse.ArgumentParser(description="Send an HCI reset to a controller over a UART.")
    parser.add_argument("port", help="Port (e.g. COM8 or /dev/ttyUSB0)")
    parser.add_argument("-b", "--baudrate", type=int, default=921600, help="Baudrate (default: %(default)s)")
    parser.add_argument("--flow-control", action=argparse.BooleanOptionalAction, default=True,
        help="RTS/CTS hardware flow control (default: enabled)")
    args = parser.parse_args()
    reset_controller(args.port, args.baudrate, args.flow_control)


if __name__ == "__main__":
    main()
