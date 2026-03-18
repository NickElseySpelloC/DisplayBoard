"""Weather topic — current conditions and hourly forecast via WeatherClient."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from sc_utility import WeatherClient
from weather_client.icon_provider import WeatherIconProvider

if TYPE_CHECKING:
    from collections.abc import Callable

    from sc_utility import SCLogger
    from weather_client.models import WeatherReading

ICON_LIBRARY = "meteocons"
ICON_THEME = "fill-animated"
STATIC_ICON_PREFIX = "/weather_icons"
type ReadingIconRefs = dict[str, str | None]


def build_icon_url(icon_provider: WeatherIconProvider, icon_name: str, *, static_prefix: str = STATIC_ICON_PREFIX) -> str:
    """Build a URL that a FastAPI/ASGI app could serve as a static SVG asset.

    Returns:
        A URL path for a packaged weather icon SVG.
    """
    icon_relative_path = icon_provider.get_icon_relative_path(icon_name)
    return f"{static_prefix.rstrip('/')}/{icon_relative_path}"


def build_reading_icon_refs(icon_provider: WeatherIconProvider, reading: WeatherReading) -> ReadingIconRefs:
    """Create ASGI-friendly image references for a weather reading.

    Returns:
        A mapping of semantic icon roles to static URL paths.
    """
    return {
        "generic_icon": build_icon_url(icon_provider, "clear-day"),
        "condition_icon": build_icon_url(icon_provider, reading.sky.icon_info.icon_name),
        "sunrise_icon": build_icon_url(icon_provider, reading.astral_info.sunrise_icon_name),
        "sunset_icon": build_icon_url(icon_provider, reading.astral_info.sunset_icon_name),
        # "precipitation_icon": build_icon_url(icon_provider, reading.precipitation_icon_name),
        "precipitation_icon": build_icon_url(icon_provider, "raindrop-measure"),    # Override
        "wind_icon": build_icon_url(icon_provider, reading.wind_icon_name),
    }


def _reading_to_dict(icon_provider: WeatherIconProvider, reading: WeatherReading, include_wind: bool = True) -> dict:
    """Convert a WeatherReading to a dict for display.

    Args:
        icon_provider: The WeatherIconProvider to use for icon URLs.
        reading: The WeatherReading to convert.
        include_wind: Whether to include wind speed and direction in the output.

    Returns:
        A dict with keys: temp_c, sky, icon, time, and optionally wind_speed_kmh and wind_dir.
    """
    result: dict = {
        "temp_c": round(reading.temperature.reading, 1),
        "temp_high_c": round(reading.temperature.high, 1) if reading.temperature.high is not None else None,
        "temp_low_c": round(reading.temperature.low, 1) if reading.temperature.low is not None else None,
        "temp_feels_like_c": round(reading.temperature.feels_like, 1) if reading.temperature.feels_like is not None else None,
        "sky_title": reading.sky.title,
        "sky_description": reading.sky.description,
        "icon": reading.sky.icon_info.unicode_char,
        "precip_probability": round(reading.precip_probability * 100) if reading.precip_probability is not None else 0,
        "time": reading.local_time.strftime("%I %p").lstrip("0"),
        "day": reading.local_time.strftime("%a"),
        "sunrise_time": reading.astral_info.sunrise.strftime("%I:%M %p").lstrip("0") if reading.astral_info.sunrise else None,
        "sunset_time": reading.astral_info.sunset.strftime("%I:%M %p").lstrip("0") if reading.astral_info.sunset else None,
        "icon_images": build_reading_icon_refs(icon_provider, reading),
    }
    if include_wind:
        result["wind_speed_kmh"] = round(reading.wind.speed, 1)
        result["wind_dir"] = reading.wind.direction if reading.wind.direction is not None else None
        result["wind_description"] = f"{result['wind_speed_kmh']} km/h {reading.wind.direction}".strip()
    else:
        result["wind_description"] = None
    return result


class TopicWeather:
    """Fetches weather data periodically and stores current + hourly readings."""

    def __init__(
        self,
        latitude: float,
        longitude: float,
        on_update: Callable[[], None],
        logger: SCLogger,
        refresh_interval_min: int = 10,
        owm_api_key: str | None = None,
        preferred_provider: str = "owm",
    ) -> None:
        self._client = WeatherClient(latitude, longitude, owm_api_key or None)
        self._on_update = on_update
        self._logger = logger
        self._refresh_secs = max(60, int(refresh_interval_min) * 60)
        self._preferred_provider = preferred_provider
        self._lock = threading.Lock()
        self._current: dict = {}
        self._hourly: list[dict] = []
        self._daily: list[dict] = []
        self._source: str = ""
        self._icon_provider = WeatherIconProvider(library=ICON_LIBRARY, theme=ICON_THEME)
        self._counter = 0  # To Do: For testing: counts how many times data has been fetched

    def get_data(self) -> dict:
        with self._lock:
            current = dict(self._current)
            hourly = list(self._hourly)
            daily = list(self._daily)
            # current["sky_description"] = f"{current['sky_description']}:{self._counter}"    # To Do: Remove
            # self._counter += 1
        return {
            "weather_current": current,
            "weather_hourly": hourly,
            "weather_daily": daily,
        }

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                weather_data = self._client.get_weather(first_choice=self._preferred_provider)  # Issue #6

                current = _reading_to_dict(self._icon_provider, weather_data.current)
                current["source"] = weather_data.station.source

                hourly = [
                    _reading_to_dict(self._icon_provider, h, include_wind=False)
                    for h in weather_data.hourly[:11]
                ]

                daily = [
                    _reading_to_dict(self._icon_provider, h, include_wind=False)
                    for h in weather_data.daily[:6]
                ]

                with self._lock:
                    self._current = current
                    self._hourly = hourly
                    self._daily = daily

                self._on_update()
                self._logger.log_message(
                    f"Weather updated from {weather_data.station.source}: "
                    f"{current['temp_c']:.1f}°C, {current['sky_title']}",
                    "debug",
                )
            except Exception as e:  # noqa: BLE001
                self._logger.log_message(f"Weather fetch error: {e}", "warning")

            stop_event.wait(timeout=self._refresh_secs)
