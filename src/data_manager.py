"""DataManager — orchestrates all topic modules and assembles the webapp snapshot."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from thread_manager import RestartPolicy
from topic_background import TopicBackground
from topic_datetime import TopicDateTime
from topic_powercontroller import TopicPowerController
from topic_weather import TopicWeather

if TYPE_CHECKING:
    from collections.abc import Callable

    from sc_utility import SCConfigManager, SCLogger


class DataManager:
    """Creates and manages all active topic modules based on the application config."""

    def __init__(
        self,
        config: SCConfigManager,
        logger: SCLogger,
        notify_force: Callable[[], None],
        notify_normal: Callable[[], None],
    ) -> None:
        self._modules: dict = {}
        self._logger = logger
        self._init_modules(config, notify_force, notify_normal)

    # ── Public ───────────────────────────────────────────────────────────

    def get_snapshot(self) -> dict:
        """Return a merged dict of all topic data for the current state."""
        snapshot: dict = {}
        for module in self._modules.values():
            snapshot.update(module.get_data())
        return snapshot

    def get_thread_specs(self) -> list[dict]:
        """Return thread registration dicts for each module, suitable for ThreadManager.add()."""
        specs = []
        for name, module in self._modules.items():
            if not hasattr(module, "run"):
                continue
            restart_policy = RestartPolicy(mode="on_crash", max_restarts=5, backoff_seconds=5.0)
            # DateTime is rapid-push and critical; retry indefinitely.
            if name == "datetime":
                restart_policy = RestartPolicy(mode="always", max_restarts=999, backoff_seconds=1.0)
            specs.append({
                "name": name,
                "target": module.run,
                "restart": restart_policy,
            })
        return specs

    # ── Private ──────────────────────────────────────────────────────────

    def _init_modules(
        self,
        config: SCConfigManager,
        notify_force: Callable[[], None],
        notify_normal: Callable[[], None],
    ) -> None:
        # DateTime — always active, rapid push every second
        self._modules["datetime"] = TopicDateTime(on_update=notify_force)

        # Weather — active when Latitude + Longitude are configured
        lat = config.get("TopicWeather", "Latitude", default=None)
        lon = config.get("TopicWeather", "Longitude", default=None)
        if lat is not None and lon is not None:
            owm_key = os.environ.get("OWM_API_KEY") or config.get("TopicWeather", "OWMAPIKey", default=None) or None
            refresh_min_raw = config.get("TopicWeather", "RefreshIntervalMin", default=10) or 10
            refresh_min = int(refresh_min_raw) if not isinstance(refresh_min_raw, dict) else 10
            self._modules["weather"] = TopicWeather(
                latitude=float(lat) if not isinstance(lat, dict) else 0.0,
                longitude=float(lon) if not isinstance(lon, dict) else 0.0,
                owm_api_key=owm_key if not isinstance(owm_key, dict) else None,
                refresh_interval_min=refresh_min,
                on_update=notify_normal,
                logger=self._logger,
            )
            self._logger.log_message(
                f"Weather topic enabled ({lat}, {lon})", "detailed"
            )
        else:
            self._logger.log_message(
                "Weather topic disabled: Latitude/Longitude not configured in TopicWeather.", "summary"
            )

        # PowerController — active when DataAPIBaseURL is configured
        pc_url = config.get("TopicPowerController", "DataAPIBaseURL", default=None)
        if pc_url:
            pc_key = (
                os.environ.get("PC_DATAAPI_ACCESS_KEY")
                or config.get("TopicPowerController", "AccessKey", default=None)
                or None
            )
            refresh_sec_raw = config.get("TopicPowerController", "RefreshIntervalSec", default=10) or 10
            refresh_sec = int(refresh_sec_raw) if not isinstance(refresh_sec_raw, dict) else 10
            self._modules["pc"] = TopicPowerController(
                base_url=str(pc_url),
                access_key=pc_key if not isinstance(pc_key, dict) else None,
                refresh_interval_sec=refresh_sec,
                on_update=notify_normal,
                logger=self._logger,
            )
            self._logger.log_message(f"PowerController topic enabled ({pc_url})", "detailed")
        else:
            self._logger.log_message(
                "PowerController topic disabled: DataAPIBaseURL not configured.", "summary"
            )

        # Background images — active when BackgroundImages section is configured
        libraries_raw = config.get("BackgroundImages", "Libraries", default=None) or []
        libraries: list = libraries_raw if isinstance(libraries_raw, list) else []
        if libraries:
            boards_raw = config.get("DisplayBoards", "Boards", default=[]) or []
            boards: list = boards_raw if isinstance(boards_raw, list) else []
            auto_rotate_raw = config.get("BackgroundImages", "AutoRotateMin", default=5) or 5
            auto_rotate_min = int(auto_rotate_raw) if not isinstance(auto_rotate_raw, dict) else 5
            self._modules["background"] = TopicBackground(
                boards=boards,
                libraries=libraries,
                auto_rotate_min=auto_rotate_min,
                on_update=notify_normal,
                logger=self._logger,
            )
            self._logger.log_message(
                f"Background images enabled ({len(libraries)} librar{'y' if len(libraries) == 1 else 'ies'}, "
                f"{auto_rotate_min} min rotation)",
                "detailed",
            )
        else:
            self._logger.log_message("Background images disabled: no libraries configured.", "summary")
