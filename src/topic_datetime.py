"""DateTime topic — provides the current date and time with rapid push updates."""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime


class TopicDateTime:
    """Generates current date/time data and pushes an update every second."""

    def __init__(self, on_update: Callable[[], None]) -> None:
        self._on_update = on_update
        self._lock = threading.Lock()
        self._data: dict = {}

    def get_data(self) -> dict:
        with self._lock:
            return {"datetime": dict(self._data)}

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            now = datetime.now()
            new_data = {
                "time": now.strftime("%H:%M"),
                "date": now.strftime(f"%A {now.day} %B %Y"),  # e.g. "Saturday 7 March 2026"
            }
            with self._lock:
                self._data = new_data
            self._on_update()
            stop_event.wait(timeout=1.0)
