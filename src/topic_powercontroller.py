"""PowerController topic — polls the PC DataAPI for outputs, meters, probes, and energy prices."""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from sc_utility import SCLogger

# Map Amber/PC energy price status values to normalised display classes.
# PC DataAPI passes through Amber status strings.
_STATUS_NORMALISE: dict[str, str] = {
    "spike":   "spike",
    "high":    "high",
    "neutral": "medium",
    "ok":      "low",
    "low":     "low",
    "verylow": "low",
}

# Map normalised energy status to display icons.
_STATUS_ICONS: dict[str, str] = {
    "spike":  "⛔️",
    "high":   "🛑",
    "medium": "⚠️",
    "low":    "✅",
}


def _normalise_status(raw: str | None) -> str:
    if not raw:
        return "medium"
    return _STATUS_NORMALISE.get(raw.lower().replace(" ", ""), "medium")


def _parse_outputs(data: dict) -> list[dict]:
    results = []
    for item in data.get("Outputs", []):
        results.append({
            "name": item.get("Name", ""),
            "display_name": item.get("DisplayName", item.get("Name", "")),
            "state": item.get("State", "").upper() == "ON",
            "mode": item.get("AppMode", ""),
        })
    return results


def _parse_meters(data: dict) -> list[dict]:
    results = []
    for item in data.get("Meters", []):
        power_raw = item.get("Power", 0)
        results.append({
            "name": item.get("Name", ""),
            "display_name": item.get("DisplayName", item.get("Name", "")),
            "power_w": round(float(power_raw), 1) if power_raw is not None else 0.0,
        })
    return results


def _parse_probes(data: dict) -> list[dict]:
    results = []
    for item in data.get("TempProbes", []):
        temp_raw = item.get("Temperature")
        results.append({
            "name": item.get("Name", ""),
            "display_name": item.get("DisplayName", item.get("Name", "")),
            "temperature": round(float(temp_raw), 1) if temp_raw is not None else None,
        })
    return results


def _parse_energy_prices(data: dict) -> tuple[dict, list[dict]]:
    """Return (current_price_dict, forecast_list)."""
    current: dict = {}
    forecast: list[dict] = []
    time_now = datetime.now()

    for item in data.get("EnergyPrices", []):
        price_type = str(item.get("Type", "")).lower()
        price_raw = item.get("Price")
        status_raw = item.get("Status", "")
        start_raw = item.get("StartDateTime", "")

        # Parse start time for display label
        display_time = ""
        if start_raw:
            try:
                dt = datetime.fromisoformat(start_raw)
                display_time = dt.astimezone().strftime("%I:%M %p").lstrip("0")
            except (ValueError, TypeError):
                display_time = ""

        price_val = float(price_raw) if price_raw is not None else None

        if price_type == "current" and not current:
            current = {
                "price": price_val,
                "status": _normalise_status(status_raw),
                "status_icon": _STATUS_ICONS.get(_normalise_status(status_raw), "🟡"),
                "time": display_time,
            }
        elif price_type == "forecast" and len(forecast) < 6:
            # Skip forecast entries that are in the past
            try:
                dt = datetime.fromisoformat(start_raw)
                if dt < time_now:
                    continue
            except (ValueError, TypeError):
                pass
            forecast.append({
                "price": price_val,
                "status": _normalise_status(status_raw),
                "status_icon": _STATUS_ICONS.get(_normalise_status(status_raw), "🟡"),
                "time": display_time,
            })

    return current, forecast


class TopicPowerController:
    """Polls the PowerController DataAPI and exposes outputs, meters, probes, and energy prices."""

    def __init__(
        self,
        base_url: str,
        on_update: Callable[[], None],
        logger: SCLogger,
        access_key: str | None = None,
        refresh_interval_sec: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_key = access_key
        self._on_update = on_update
        self._logger = logger
        self._refresh_secs = max(5, int(refresh_interval_sec))

        self._lock = threading.Lock()
        self._pc_data: dict = {"connected": False, "outputs": [], "meters": [], "probes": []}
        self._amber_data: dict = {"connected": False, "current_price": None, "current_status": "medium", "current_status_icon": "🟡", "forecast": []}

    def get_data(self) -> dict:
        with self._lock:
            return {
                "powercontroller": dict(self._pc_data),
                "amber_energy": dict(self._amber_data),
            }

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self._fetch_all()
                self._on_update()
            except requests.exceptions.ConnectionError:
                self._logger.log_message(
                    f"PowerController DataAPI not reachable at {self._base_url}", "warning"
                )
                with self._lock:
                    self._pc_data["connected"] = False
                    self._amber_data["connected"] = False
            except Exception as e:  # noqa: BLE001
                self._logger.log_message(f"PowerController fetch error: {e}", "warning")
                with self._lock:
                    self._pc_data["connected"] = False
                    self._amber_data["connected"] = False

            stop_event.wait(timeout=self._refresh_secs)

    # ── Private ──────────────────────────────────────────────────────────

    def _fetch_all(self) -> None:
        outputs_raw = self._get("/outputs")
        meters_raw = self._get("/meters")
        probes_raw = self._get("/tempprobes")
        prices_raw = self._get("/energyprices")

        outputs = _parse_outputs(outputs_raw if isinstance(outputs_raw, dict) else {})
        meters = _parse_meters(meters_raw if isinstance(meters_raw, dict) else {})
        probes = _parse_probes(probes_raw if isinstance(probes_raw, dict) else {})
        current_price, forecast = _parse_energy_prices(prices_raw if isinstance(prices_raw, dict) else {})

        with self._lock:
            self._pc_data = {
                "connected": True,
                "outputs": outputs,
                "meters": meters,
                "probes": probes,
            }
            self._amber_data = {
                "connected": True,
                "current_price": current_price.get("price"),
                "current_status": current_price.get("status", "medium"),
                "current_status_icon": current_price.get("status_icon", "🟡"),
                "forecast": forecast,
            }

    def _get(self, endpoint: str) -> Any:
        url = self._base_url + endpoint
        headers: dict[str, str] = {}
        if self._access_key:
            headers["Authorization"] = f"Bearer {self._access_key}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
