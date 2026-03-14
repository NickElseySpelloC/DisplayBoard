"""Weather topic — current conditions and hourly forecast via WeatherClient."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from sc_utility import WeatherClient

if TYPE_CHECKING:
    from collections.abc import Callable

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
    """Convert a WeatherReading to a dict for display.

    Args:
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
        "icon": _sky_to_icon(reading.sky.description),
        "png_icon": reading.sky.icon_png_url,
        "precip_probability": round(reading.precip_probability * 100) if reading.precip_probability is not None else 0,
        "time": reading.local_time.strftime("%I %p").lstrip("0"),
        "day": reading.local_time.strftime("%a"),
        "sunrise_time": reading.sunrise.strftime("%I:%M %p").lstrip("0") if reading.sunrise else None,
        "sunset_time": reading.sunset.strftime("%I:%M %p").lstrip("0") if reading.sunset else None,
    }
    if include_wind:
        result["wind_speed_kmh"] = round(reading.wind.speed, 1)
        result["wind_dir"] = _deg_to_compass(reading.wind.deg)
        result["wind_description"] = f"{result['wind_speed_kmh']} km/h {_deg_to_compass(reading.wind.deg)}".strip()
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
    ) -> None:
        self._client = WeatherClient(latitude, longitude, owm_api_key or None)
        self._on_update = on_update
        self._logger = logger
        self._refresh_secs = max(60, int(refresh_interval_min) * 60)
        self._lock = threading.Lock()
        self._current: dict = {}
        self._hourly: list[dict] = []
        self._daily: list[dict] = []
        self._source: str = ""

    def get_data(self) -> dict:
        with self._lock:
            current = dict(self._current)
            hourly = list(self._hourly)
            daily = list(self._daily)
        return {
            "weather_current": current,
            "weather_hourly": hourly,
            "weather_daily": daily,
        }

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                weather_data = self._client.get_weather()

                current = _reading_to_dict(weather_data.current)
                current["source"] = weather_data.station.source

                hourly = [
                    _reading_to_dict(h, include_wind=False)
                    for h in weather_data.hourly[:11]
                ]

                daily = [
                    _reading_to_dict(h, include_wind=False)
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
