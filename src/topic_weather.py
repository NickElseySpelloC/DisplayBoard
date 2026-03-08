"""Weather topic — current conditions and hourly forecast via WeatherClient."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from sc_utility import WeatherClient

if TYPE_CHECKING:
    from sc_utility import SCLogger
    from weather_client.models import WeatherReading

# Map sky description strings (from OWM detailed_status and Open-Meteo) to display emoji.
# Matched case-insensitively against WeatherReading.sky.
_SKY_ICONS: list[tuple[str, str]] = [
    # Exact / most-specific first
    ("thunderstorm", "⛈️"),
    ("heavy rain", "🌧️"),
    ("shower rain", "🌧️"),
    ("rain", "🌧️"),
    ("drizzle", "🌦️"),
    ("snow", "❄️"),
    ("sleet", "🌨️"),
    ("fog", "🌫️"),
    ("mist", "🌫️"),
    ("haze", "🌫️"),
    ("smoke", "🌫️"),
    ("sand", "🌫️"),
    ("dust", "🌫️"),
    ("overcast", "☁️"),
    ("broken clouds", "⛅"),
    ("scattered clouds", "⛅"),
    ("partly cloudy", "⛅"),
    ("few clouds", "🌤️"),
    ("mainly clear", "🌤️"),
    ("clear", "☀️"),
]

_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _sky_to_icon(sky: str) -> str:
    sky_lower = sky.lower()
    for keyword, icon in _SKY_ICONS:
        if keyword in sky_lower:
            return icon
    return "🌡️"


def _deg_to_compass(deg: float | None) -> str:
    if deg is None:
        return ""
    return _COMPASS[round(deg / 22.5) % 16]


def _reading_to_dict(reading: WeatherReading, include_wind: bool = True) -> dict:
    result: dict = {
        "temp_c": round(reading.temperature, 1),
        "sky": reading.sky,
        "icon": _sky_to_icon(reading.sky),
        "time": reading.local_time.strftime("%H:%M"),
    }
    if include_wind:
        result["wind_speed_kmh"] = round(reading.wind.speed, 1)
        result["wind_dir"] = _deg_to_compass(reading.wind.deg)
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
    ) -> None:
        self._client = WeatherClient(latitude, longitude, owm_api_key or None)
        self._on_update = on_update
        self._logger = logger
        self._refresh_secs = max(60, int(refresh_interval_min) * 60)
        self._lock = threading.Lock()
        self._current: dict = {}
        self._hourly: list[dict] = []
        self._source: str = ""

    def get_data(self) -> dict:
        with self._lock:
            current = dict(self._current)
            hourly = list(self._hourly)
        return {
            "weather_current": current,
            "weather_hourly": hourly,
        }

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                current_reading, hourly_readings, station = self._client.get_weather()

                current = _reading_to_dict(current_reading)
                current["source"] = station.source

                hourly = [
                    _reading_to_dict(h, include_wind=False)
                    for h in hourly_readings[:4]
                ]

                with self._lock:
                    self._current = current
                    self._hourly = hourly

                self._on_update()
                self._logger.log_message(
                    f"Weather updated from {station.source}: "
                    f"{current_reading.temperature:.1f}°C, {current_reading.sky}",
                    "debug",
                )
            except Exception as e:  # noqa: BLE001
                self._logger.log_message(f"Weather fetch error: {e}", "warning")

            stop_event.wait(timeout=self._refresh_secs)
