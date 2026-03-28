"""DataManager — orchestrates all topic modules and assembles the webapp snapshot."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from thread_manager import RestartPolicy
from topic_background import TopicBackground
from topic_calendar import TopicCalendar
from topic_datetime import TopicDateTime
from topic_powercontroller import TopicPowerController
from topic_wanfailover import TopicWANFailoverCheck
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

    def _init_modules(  # noqa: PLR0914, PLR0915
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
        preferred_provider = config.get("TopicWeather", "PreferredProvider", default="owm") or "owm"
        if lat is not None and lon is not None:
            owm_key = os.environ.get("OWM_API_KEY") or config.get("TopicWeather", "OWMAPIKey", default=None) or None
            refresh_min_raw = config.get("TopicWeather", "RefreshIntervalMin", default=10) or 10
            refresh_min = int(refresh_min_raw) if not isinstance(refresh_min_raw, dict) else 10
            icon_library = config.get("TopicWeather", "IconLibrary")
            icon_theme = config.get("TopicWeather", "IconTheme")
            icon_style = config.get("TopicWeather", "IconStyle")
            self._modules["weather"] = TopicWeather(
                latitude=float(lat) if not isinstance(lat, dict) else 0.0,
                longitude=float(lon) if not isinstance(lon, dict) else 0.0,
                owm_api_key=owm_key if not isinstance(owm_key, dict) else None,
                preferred_provider=preferred_provider if not isinstance(preferred_provider, dict) else "owm",
                refresh_interval_min=refresh_min,
                on_update=notify_normal,
                logger=self._logger,
                icon_library=icon_library,  # pyright: ignore[reportArgumentType]
                icon_theme=icon_theme,  # pyright: ignore[reportArgumentType]
                icon_style=icon_style,  # pyright: ignore[reportArgumentType]
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

        # WANFailoverCheck — active when APIBaseURL is configured
        wan_url = config.get("TopicWANFailoverCheck", "APIBaseURL", default=None)
        if wan_url:
            wan_refresh_raw = config.get("TopicWANFailoverCheck", "RefreshIntervalSec", default=10) or 10
            wan_refresh = int(wan_refresh_raw) if not isinstance(wan_refresh_raw, dict) else 10
            self._modules["wan_failover"] = TopicWANFailoverCheck(
                base_url=str(wan_url),
                refresh_interval_sec=wan_refresh,
                on_update=notify_normal,
                logger=self._logger,
            )
            self._logger.log_message(f"WANFailoverCheck topic enabled ({wan_url})", "detailed")
        else:
            self._logger.log_message(
                "WANFailoverCheck topic disabled: APIBaseURL not configured.", "summary"
            )

        # Calendar — active when Accounts are configured
        cal_accounts_raw = config.get("TopicCalendar", "Accounts", default=None) or []
        cal_accounts: list = cal_accounts_raw if isinstance(cal_accounts_raw, list) else []
        if cal_accounts:
            cal_refresh_raw = config.get("TopicCalendar", "RefreshIntervalMin", default=15) or 15
            cal_refresh = int(cal_refresh_raw) if not isinstance(cal_refresh_raw, dict) else 15
            cal_days_raw = config.get("TopicCalendar", "DaysAhead", default=7) or 7
            cal_days = int(cal_days_raw) if not isinstance(cal_days_raw, dict) else 7
            cal_creds = (
                os.environ.get("GOOGLE_CREDENTIALS_FILE")
                or config.get("TopicCalendar", "CredentialsFile", default="google_credentials.json")
                or "google_credentials.json"
            )
            cal_tokens = config.get("TopicCalendar", "TokensDir", default="tokens") or "tokens"
            self._modules["calendar"] = TopicCalendar(
                accounts=cal_accounts,
                days_ahead=cal_days,
                credentials_file=str(cal_creds),
                tokens_dir=str(cal_tokens),
                on_update=notify_normal,
                logger=self._logger,
                refresh_interval_min=cal_refresh,
            )
            self._logger.log_message(
                f"Calendar topic enabled ({len(cal_accounts)} account(s), {cal_days} days ahead)",
                "detailed",
            )
        else:
            self._logger.log_message(
                "Calendar topic disabled: no accounts configured in TopicCalendar.", "summary"
            )
