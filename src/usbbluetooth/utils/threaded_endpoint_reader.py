#!/usr/bin/env python
#
# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: 2025 Antonio Vazquez Blanco <antoniovazquezblanco@gmail.com>
#

import queue
import threading
from usbbluetooth.utils.hci_endpoint_reader import EndpointReader


class ThreadedEndpointReader:
    """
    Drains one reader on its own thread into a queue the caller owns.
    """

    def __init__(self, reader: EndpointReader, out_queue, poll_ms=200, name="usbbt-reader"):
        """
        :param reader: object exposing ``read(timeout_ms) -> bytes | None``.
        :param out_queue: queue shared with the other readers and drained by the
            owner; this reader only puts onto it.
        :param poll_ms: how long each blocking read waits before looping to check
            the stop flag. A packet is delivered as soon as it arrives, so this
            bounds only how quickly stop() is noticed by an idle reader, not read
            latency.
        :param name: reader thread name, for diagnostics.
        """
        self._reader = reader
        self._queue = out_queue
        self._poll_ms = poll_ms
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._dropped = 0

    @property
    def dropped(self):
        """Packets this reader discarded because the shared queue was full."""
        return self._dropped

    @property
    def is_alive(self):
        """Whether the reader thread is currently running."""
        return self._thread.is_alive()

    def start(self):
        """Start the reader thread."""
        self._thread.start()

    def stop(self, join_timeout):
        """Signal the thread to finish and wait up to ``join_timeout`` seconds.

        The thread wakes every ``poll_ms`` to check the flag, so an idle reader
        is joined within about that long."""
        self._stop.set()
        self._thread.join(join_timeout)

    def _run(self):
        try:
            while not self._stop.is_set():
                packet = self._reader.read(self._poll_ms)
                if packet is not None:
                    self._put(packet)
        except Exception as error:
            # A terminal error rides the queue as an item and stops this reader;
            # the consumer re-raises it and the other readers keep running.
            self._put(error)

    def _put(self, item):
        """Enqueue an item, dropping the oldest to make room when full."""
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                else:
                    self._dropped += 1
