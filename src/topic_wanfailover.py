"""WANFailoverCheck topic — polls a WAN failover status API."""
from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from collections.abc import Callable

    from sc_utility import SCLogger


class TopicWANFailoverCheck:
    """Polls a WAN failover status REST API and exposes the status fields."""

    def __init__(
        self,
        base_url: str,
        on_update: Callable[[], None],
        logger: SCLogger,
        refresh_interval_sec: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._on_update = on_update
        self._logger = logger
        self._refresh_secs = max(5, int(refresh_interval_sec))

        self._lock = threading.Lock()
        self._data: dict = {
            "connected": False,
            "timestamp": None,
            "timestamp_local": None,
            "on_primary": None,
            "status": None,
            "external_ip": None,
        }

    def get_data(self) -> dict:
        with self._lock:
            return {"wan_failover": dict(self._data)}

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self._fetch()
                self._on_update()
            except requests.exceptions.ConnectionError:
                self._logger.log_message(
                    f"WANFailoverCheck API not reachable at {self._base_url}", "warning"
                )
                with self._lock:
                    self._data["connected"] = False
            except Exception as e:  # noqa: BLE001
                self._logger.log_message(f"WANFailoverCheck fetch error: {e}", "warning")
                with self._lock:
                    self._data["connected"] = False

            stop_event.wait(timeout=self._refresh_secs)

    # ── Private ──────────────────────────────────────────────────────────

    def _fetch(self) -> None:
        resp = requests.get(self._base_url, timeout=10)
        resp.raise_for_status()
        raw: dict = resp.json()

        timestamp_utc: str | None = raw.get("timestamp")
        timestamp_local: str | None = None
        if timestamp_utc:
            try:
                dt = datetime.fromisoformat(timestamp_utc)
                timestamp_local = dt.astimezone().strftime("%d %b %Y %I:%M:%S %p")
            except (ValueError, TypeError):
                timestamp_local = timestamp_utc

        with self._lock:
            self._data = {
                "connected": True,
                "timestamp": timestamp_utc,
                "timestamp_local": timestamp_local,
                "on_primary": raw.get("on_primary"),
                "status": raw.get("status"),
                "external_ip": raw.get("external_ip"),
            }
