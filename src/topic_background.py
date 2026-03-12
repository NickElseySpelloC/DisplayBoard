"""Background image topic — fetches rotating background images from Unsplash or Pexels."""
from __future__ import annotations

import os
import random
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from sc_utility import SCLogger


class TopicBackground:
    """Fetches a new background image per board at a configured interval."""

    def __init__(
        self,
        boards: list[dict],
        libraries: list[dict],
        on_update: Callable[[], None],
        logger: SCLogger,
        auto_rotate_min: int = 5,
    ) -> None:
        self._on_update = on_update
        self._logger = logger
        self._rotate_secs = max(60, int(auto_rotate_min) * 60)

        # Build map: board_name -> library config (only boards that have a library)
        lib_by_name = {lib["Name"]: lib for lib in libraries}
        self._board_libs: dict[str, dict] = {}
        for board in boards:
            lib_name = board.get("BackgroundImageLibrary")
            if lib_name and lib_name in lib_by_name:
                self._board_libs[board["Name"]] = lib_by_name[lib_name]

        self._lock = threading.Lock()
        # Initialise all boards to None; boards without a library stay None
        self._board_urls: dict[str, str | None] = {b["Name"]: None for b in boards}

    def get_data(self) -> dict:
        with self._lock:
            return {"background_images": dict(self._board_urls)}

    def run(self, stop_event: threading.Event) -> None:
        # Fetch once immediately on startup, then on each rotation interval
        self._fetch_all()
        while not stop_event.is_set():
            stop_event.wait(timeout=self._rotate_secs)
            if not stop_event.is_set():
                self._fetch_all()

    # ── Private ──────────────────────────────────────────────────────────

    def _fetch_all(self) -> None:
        updated = False
        for board_name, lib_cfg in self._board_libs.items():
            try:
                url = self._fetch_image(lib_cfg)
                if url:
                    with self._lock:
                        self._board_urls[board_name] = url
                    updated = True
            except Exception as e:  # noqa: BLE001
                lib_type = lib_cfg.get("Type", "unknown")
                self._logger.log_message(
                    f"Background image fetch failed [{lib_type}] for board '{board_name}': {e}",
                    "warning",
                )
        if updated:
            self._on_update()

    def _fetch_image(self, lib_cfg: dict[str, Any]) -> str | None:
        lib_type = lib_cfg.get("Type", "").lower()
        if lib_type == "unsplash":
            return self._fetch_unsplash(lib_cfg)
        if lib_type == "pexels":
            return self._fetch_pexels(lib_cfg)
        self._logger.log_message(f"Unknown background library type: {lib_type}", "warning")
        return None

    def _fetch_unsplash(self, lib_cfg: dict[str, Any]) -> str | None:
        access_key = os.environ.get("UNSPLASH_ACCESS_KEY") or lib_cfg.get("AccessKey") or ""
        if not access_key:
            self._logger.log_message("Unsplash access key not configured.", "warning")
            return None

        query = lib_cfg.get("Query", "nature landscape")
        self._logger.log_message(f"Fetching background image from Unsplash with query: '{query}'", "debug")
        resp = requests.get(
            "https://api.unsplash.com/photos/random",
            headers={"Authorization": f"Client-ID {access_key}"},
            params={"query": query, "orientation": "landscape"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("urls", {}).get("regular")

    def _fetch_pexels(self, lib_cfg: dict[str, Any]) -> str | None:
        access_key = os.environ.get("PEXELS_ACCESS_KEY") or lib_cfg.get("AccessKey") or ""
        if not access_key:
            self._logger.log_message("Pexels access key not configured.", "warning")
            return None

        query = lib_cfg.get("Query", "nature landscape")
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": access_key},
            params={"query": query, "per_page": 15, "orientation": "landscape"},
            timeout=10,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        if not photos:
            return None
        return random.choice(photos).get("src", {}).get("large2x")
