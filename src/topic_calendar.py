"""Calendar topic — fetches Google Calendar events and exposes them for display."""
from __future__ import annotations

import datetime as dt
import html
import operator
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

if TYPE_CHECKING:
    from collections.abc import Callable

    from sc_utility import SCLogger

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
_ALLDAY_DISPLAY = "All day"


def _esc(value: str) -> str:
    """HTML-escape a string for safe injection into innerHTML via JS template literals.

    Args:
        value (str): The string to escape.

    Returns:
        str: The escaped string, or an empty string if the input is falsy.
    """
    return html.escape(str(value)) if value else ""


class TopicCalendar:
    """Fetches Google Calendar events periodically and merges them into a unified day list."""

    def __init__(
        self,
        accounts: list[dict],
        days_ahead: int,
        credentials_file: str,
        tokens_dir: str,
        on_update: Callable[[], None],
        logger: SCLogger,
        refresh_interval_min: int = 15,
    ) -> None:
        self._accounts = accounts
        self._days_ahead = max(1, int(days_ahead))
        self._credentials_file = Path(credentials_file)
        self._tokens_dir = Path(tokens_dir)
        self._on_update = on_update
        self._logger = logger
        self._refresh_secs = max(60, int(refresh_interval_min) * 60)
        self._lock = threading.Lock()
        self._days: list[dict] = []

    # ── Public ───────────────────────────────────────────────────────────

    def get_data(self) -> dict:
        with self._lock:
            return {"calendar": list(self._days)}

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                days = self._fetch_all()
                with self._lock:
                    self._days = days
                self._on_update()
                total_events = sum(len(d["events"]) for d in days)
                self._logger.log_message(
                    f"Calendar updated: {total_events} event(s) across {len(days)} day(s)",
                    "debug",
                )
            except Exception as e:  # noqa: BLE001
                self._logger.log_message(f"Calendar fetch error: {e}", "warning")
            stop_event.wait(timeout=self._refresh_secs)

    # ── Private ──────────────────────────────────────────────────────────

    def _fetch_all(self) -> list[dict]:
        """Fetch and merge events from all configured accounts.

        Returns:
            list[dict]: A list of merged events from all accounts.
        """
        all_events: list[dict] = []
        for account in self._accounts:
            account_name = account.get("Name", "unknown")
            try:
                all_events.extend(self._fetch_account(account))
            except RuntimeError as e:
                # Credential/config issue — treat as a warning
                msg = str(e)
                self._logger.log_message(
                    f"Calendar warning for account '{account_name}': {msg}", "warning"
                )
                all_events.append(_make_error_event(account_name, msg, is_error=False))
            except Exception as e:  # noqa: BLE001
                # Unexpected error
                msg = str(e)
                self._logger.log_message(
                    f"Calendar fetch error for account '{account_name}': {msg}", "warning"
                )
                all_events.append(_make_error_event(account_name, msg, is_error=True))
        # Sort chronologically: by date then by time-of-day
        all_events.sort(key=operator.itemgetter("_date", "_sort_key"))
        return _build_day_slots(all_events)

    def _fetch_account(self, account: dict) -> list[dict]:  # noqa: PLR0914
        """Fetch events for a single Google account and return a flat event list.

        Args:
            account (dict): The account configuration dict.

        Returns:
            list[dict]: A list of event dicts for this account.
        """
        name = account.get("Name", "unknown")
        token_path = self._tokens_dir / f"{name}.json"
        creds = self._load_credentials(token_path)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)

        # Retrieve all calendars with their display colours
        calendar_list = service.calendarList().list().execute()
        calendars: list[dict] = calendar_list.get("items", [])

        # Optionally filter to specific calendars
        filter_names: list[str] | None = account.get("Calendars")
        if filter_names:
            filter_lower = {f.lower() for f in filter_names}
            calendars = [
                c for c in calendars
                if c.get("id") in filter_names
                or c.get("summary", "").lower() in filter_lower
            ]

        now = dt.datetime.now(dt.UTC)
        time_min = now.isoformat()
        time_max = (now + dt.timedelta(days=self._days_ahead)).isoformat()

        events: list[dict] = []
        for calendar in calendars:
            cal_id = calendar["id"]
            # Use the calendar's backgroundColor from Google — same colour seen in macOS Calendar
            color = calendar.get("backgroundColor", "#aaaaaa")
            try:
                result = service.events().list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
                for item in result.get("items", []):
                    parsed = _parse_event(item, color)
                    if parsed:
                        events.append(parsed)
            except HttpError as e:
                self._logger.log_message(
                    f"Error fetching calendar '{calendar.get('summary', cal_id)}': {e}", "warning"
                )
        return events

    def _load_credentials(self, token_path: Path) -> Credentials:  # noqa: PLR6301
        """Load and (if needed) refresh stored OAuth credentials.

        Args:
            token_path (Path): The path to the stored credentials JSON file.

        Returns:
            Credentials: The loaded and refreshed OAuth credentials.

        Raises:
            RuntimeError: If credentials are missing or invalid.
        """
        creds: Credentials | None = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        if not creds or not creds.valid:
            error_msg = f"No valid credentials at '{token_path}'. "
            error_msg += "Run: python src/setup_calendar_auth.py --account <name>"
            raise RuntimeError(error_msg)
        return creds


# ── Module-level helpers ──────────────────────────────────────────────────────

def _make_error_event(account_name: str, message: str, *, is_error: bool) -> dict:
    """Build a synthetic event dict for a per-account fetch failure.

    Args:
        account_name (str): The name of the account with the issue.
        message (str): The error/warning message to display.
        is_error (bool): Whether this is an error (True) or a warning (False).

    Returns:
        dict: A synthetic event dictionary representing the error or warning.
    """
    now = dt.datetime.now().astimezone()
    icon = "⛔" if is_error else "⚠️"
    return {
        "_date": now.date().isoformat(),
        "_sort_key": now.strftime("%H:%M"),
        "time": now.strftime("%I:%M %p").lstrip("0"),
        "title": _esc(f"{icon} {account_name} unavailable"),
        "location": _esc(message),
        "color": "#f44336" if is_error else "#ff9800",
    }


def _parse_event(item: dict, color: str) -> dict | None:
    """Convert a Google Calendar API event item into a display dict.

    Args:
        item (dict): The raw event item from the Google Calendar API.
        color (str): The display color associated with this event's calendar.

    Returns:
        dict | None: A display dictionary representing the event, or None if the event cannot be parsed.
    """
    start = item.get("start", {})

    all_day = "date" in start and "dateTime" not in start
    if all_day:
        date_str = start.get("date", "")
        try:
            event_date = dt.date.fromisoformat(date_str)
        except ValueError:
            return None
        sort_key = "00:00"
        display_time = _ALLDAY_DISPLAY
    else:
        start_str = start.get("dateTime", "")
        try:
            start_dt = dt.datetime.fromisoformat(start_str).astimezone()
        except ValueError:
            return None
        event_date = start_dt.date()
        sort_key = start_dt.strftime("%H:%M")
        display_time = start_dt.strftime("%I:%M %p").lstrip("0")

    return {
        "_date": event_date.isoformat(),
        "_sort_key": sort_key,
        "time": display_time,
        "title": _esc(item.get("summary") or "Untitled Event"),
        "location": _esc(item.get("location") or ""),
        "color": color,
    }


def _build_day_slots(events: list[dict]) -> list[dict]:
    """Group a sorted flat event list into per-day display dicts, skipping empty days.

    Args:
        events (list[dict]): A list of event dictionaries.

    Returns:
        list[dict]: A list of per-day display dictionaries.
    """
    days: dict[str, dict] = {}
    for event in events:
        date_key = event["_date"]
        if date_key not in days:
            try:
                d = dt.date.fromisoformat(date_key)
            except ValueError:
                continue
            days[date_key] = {
                "day_number": str(d.day),
                "day_name": d.strftime("%a"),
                "events": [],
            }
        days[date_key]["events"].append({
            "time": event["time"],
            "title": event["title"],
            "location": event["location"],
            "color": event["color"],
        })
    return list(days.values())
