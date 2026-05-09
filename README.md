# USB Bluetooth

[![Build](https://github.com/usbbluetooth/usbbluetooth-python/actions/workflows/build.yml/badge.svg)](https://github.com/usbbluetooth/usbbluetooth-python/actions/workflows/build.yml)
[![PyPI](https://img.shields.io/pypi/v/usbbluetooth)](https://pypi.org/project/usbbluetooth/)
[![Snyk](https://img.shields.io/badge/Snyk-security%20report-blue?logo=snyk)](https://snyk.io/advisor/python/usbbluetooth)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE.md)

Take full control of your USB Bluetooth controllers from Python!

If you want to use this library with Scapy, please check out [scapy-usbbluetooth](https://pypi.org/project/scapy-usbbluetooth/).

For general documentation about the project, please visit [usbbluetooth.github.io](https://usbbluetooth.github.io).

For a C version of this library, check out [UsbBluetooth for C](https://github.com/usbbluetooth/usbbluetooth).

For a C# version of this library, check out [UsbBluetooth for C#](https://github.com/usbbluetooth/usbbluetooth-csharp).

## Installation

Just use pip :)

```
pip install usbbluetooth
```

## Usage

Once installed, you may list devices using `usbbluetooth.list_controllers()`, and for each device you may `open()` the device, `write()` and `read()` to them and `close()` it once you are done.
See the [examples](examples/) folder for some sample code.

## Plaform quirks

This package has some requirements to work because of different platform particularities.
To make sure the package works, please, see <https://usbbluetooth.github.io/quirks/>

## History

Package versions `< 0.1` were bindings around the original C library.
The history for those packages can be found in the [usbbluetooth repository](https://github.com/usbbluetooth/usbbluetooth) in the respective tags.

Package versions `>= 0.1` are developed in this repo and based of `pyusb`, the Python LibUSB binding.
