# USB Bluetooth

[![Build](https://github.com/usbbluetooth/usbbluetooth-python/actions/workflows/build.yml/badge.svg)](https://github.com/usbbluetooth/usbbluetooth-python/actions/workflows/build.yml)
[![PyPI](https://img.shields.io/pypi/v/usbbluetooth)](https://pypi.org/project/usbbluetooth/)
[![Snyk](https://snyk.io/advisor/python/usbbluetooth/badge.svg)](https://snyk.io/advisor/python/usbbluetooth)

Take full control of your USB Bluetooth controllers from Python!

If you want to use this library with Scapy, please check out [scapy-usbbluetooth](https://pypi.org/project/scapy-usbbluetooth/).

## Installation

Just use pip :)

```
pip install usbbluetooth
```

## Usage

Once installed, you may list devices using `usbbluetooth.list_controllers()`, and for each device you may `open()` the device, `write()` and `read()` to them and `close()` it once you are done.
See the [examples](examples/) folder for some sample code.

## Plaform quirks

### Windows

In Windows you may have to install WinUSB driver in your device using Zadig. Otherwise, UsbBluetooth will detect your device but it may not be able to take control of your device.

### Linux

Your Linux user must have permissions to access USB hardware. Here are several options to ensure access:

- **Run as root**: Execute the application with elevated privileges using `sudo`. Note that this may not be ideal for security reasons.

- **Add user to a group**: Add your user to the `plugdev`, `usb` or `uucp` group (depending on your distribution). Remeber to reboot or log out and log back in for the changes to take effect. For example:

  ```
  sudo usermod -a -G plugdev $USER
  ```

- **Create a udev rule**: Create a custom udev rule to automatically set permissions for USB Bluetooth devices. Create a file like `/etc/udev/rules.d/99-usbbluetooth.rules` with content similar to:
  ```
  SUBSYSTEM=="usb", ATTR{idVendor}=="your_vendor_id", ATTR{idProduct}=="your_product_id", MODE="0666"
  ```
  Replace `your_vendor_id` and `your_product_id` with the actual vendor and product IDs of your device (you can find these using `lsusb`). Then reload udev rules with `sudo udevadm control --reload-rules && sudo udevadm trigger`.

## History

Package versions `< 0.1` were bindings around the original C library. The history for those packages can be found in the [usbbluetooth repository](https://github.com/usbbluetooth/usbbluetooth) in the respective tags.

Package versions `>= 0.1` are developed in this repo and based of `pyusb`, the Python LibUSB binding.
