#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vázquez Blanco <antoniovazquezblanco@gmail.com>
#

"""Test configuration.

Tests come in two flavours. Most of them run against a synthetic pyusb backend
(see fake_backend.py) and need no USB stack at all, so they run anywhere. The
rest need a real controller plugged in and are marked ``hardware``.

Hardware tests are skipped unless ``--hardware`` is given, and skipped again if
no controller can be found. They are opt in rather than automatic because they
reset a real adapter: run against the machine's own Bluetooth they would drop
its connections, and they need the device bound to WinUSB on Windows or
accessible to the user on Linux.
"""

import pytest
import usb.core

from fake_backend import FakeBackend, controller_descriptors

from usbbluetooth import (Controller, InsufficientPermissionsException,
                          WrongDriverException)


def pytest_addoption(parser):
    parser.addoption(
        "--hardware", action="store_true", default=False,
        help="run the tests that need a real USB Bluetooth controller")
    parser.addoption(
        "--hardware-device", default=None, metavar="VID:PID",
        help="use only this controller for the hardware tests, for example "
             "0a12:0001; by default the first one that opens is used")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "hardware: needs a real USB Bluetooth controller (opt in with "
        "--hardware)")


def _controllers(selector=None):
    """Connected controllers, or an empty list if USB is unusable here."""
    try:
        from usbbluetooth import list_controllers
        controllers = list_controllers()
    except Exception:
        # No libusb, no permissions, no USB at all: treat as "none found"
        # rather than failing collection.
        return []
    if selector:
        vendor, _, product = selector.partition(":")
        controllers = [c for c in controllers
                       if c.vendor_id == int(vendor, 16)
                       and c.product_id == int(product, 16)]
    return controllers


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--hardware"):
        skip = pytest.mark.skip(reason="needs --hardware")
    elif not _controllers(config.getoption("--hardware-device")):
        skip = pytest.mark.skip(
            reason="--hardware given but no USB Bluetooth controller found")
    else:
        return
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def fake_controller():
    """Factory for a Controller backed by synthetic descriptors.

    Returns a callable taking the same arguments as
    :func:`fake_backend.controller_descriptors` and yielding an unopened
    ``(controller, backend)`` pair.
    """
    opened = []

    def _make(**kwargs):
        backend = FakeBackend(controller_descriptors(**kwargs))
        device = usb.core.find(backend=backend)
        assert device is not None, "the fake backend served no device"
        controller = Controller(device)
        opened.append(controller)
        return controller, backend

    yield _make

    for controller in opened:
        if controller.is_open:
            controller.close()


@pytest.fixture
def hardware_controller(request):
    """An opened real controller, for tests marked ``hardware``.

    A machine can list controllers it cannot actually drive: the one built into
    the host is normally bound to the operating system's own Bluetooth stack
    rather than to a driver we can claim. So rather than taking the first
    controller listed, take the first one that opens, and skip if none does.
    """
    controllers = _controllers(request.config.getoption("--hardware-device"))
    if not controllers:
        pytest.skip("no USB Bluetooth controller found")

    unusable = []
    for controller in controllers:
        try:
            controller.open()
        except (WrongDriverException, InsufficientPermissionsException) as e:
            unusable.append(f"{controller}: {type(e).__name__}")
            continue
        try:
            yield controller
        finally:
            controller.close()
        return

    pytest.skip("no controller could be opened: " + "; ".join(unusable))
