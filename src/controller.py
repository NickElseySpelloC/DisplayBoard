"""The AppController class that orchestrates the application."""
import contextlib
import queue
import time
from collections.abc import Callable
from threading import Event, RLock
from typing import TYPE_CHECKING

from sc_foundation import (
    DateHelper,
    SCConfigManager,
    SCLogger,
)

from data_manager import DataManager
from local_enumerations import TRIM_LOGFILE_INTERVAL, Command

if TYPE_CHECKING:
    import datetime as dt


class AppController:
    """The AppController class that orchestrates the application."""

    # Public Functions ============================================================================
    def __init__(self, config: SCConfigManager, logger: SCLogger, wake_event: Event):
        """Initializes the AppController.

        Args:
            config (SCConfigManager): The configuration manager for the system.
            logger (SCLogger): The logger for the system.
            wake_event (Event): The event used to wake the controller.
        """
        self.config = config
        self.last_config_check = DateHelper.now()
        self.logger = logger
        self.logger_last_trim: dt.datetime | None = None
        self.wake_event = wake_event
        self.cmd_q: queue.Queue[Command] = queue.Queue()    # Used to post commands into the controller's loop
        self.command_pending: bool = False
        self.report_critical_errors_delay = config.get("General", "ReportCriticalErrorsDelay", default=None)
        if isinstance(self.report_critical_errors_delay, (int, float)):
            self.report_critical_errors_delay = round(self.report_critical_errors_delay, 0)
        else:
            self.report_critical_errors_delay = None

        # Setup the environment
        self.last_tick_time = DateHelper.now()
        self._io_shutdown_lock = RLock()  # Serialize writes during shutdown

        # Optional callback used to notify the webapp (WebSocket) layer that new data is available
        # This is set by main.py once the ASGI webapp is initialised.
        self._webapp_notify: Callable[[], None] | None = None
        self._last_webapp_notify: dt.datetime | None = None

        self._initialise(startup_mode=True)

        # Create data manager — must come after _initialise() so poll_interval is set
        self._data_manager = DataManager(
            config=config,
            logger=logger,
            notify_force=lambda: self.signal_data_update(force=True),
            notify_normal=lambda: self.signal_data_update(force=False),
        )

    def get_data_thread_specs(self) -> list[dict]:
        """Return thread specs for all data topic modules (for registration with ThreadManager)."""
        return self._data_manager.get_thread_specs()

    def signal_data_update(self, *, force: bool = False) -> None:
        """Called by data topic threads when new data is available.

        Args:
            force: If True, push a WS snapshot immediately (bypasses throttle).
                   Use for rapid-push topics like datetime.
                   If False, the push is throttled to poll_interval.
        """
        self._maybe_notify_webapp(force=force)
        if not force:
            self.wake_event.set()

    def set_webapp_notifier(self, notify: Callable[[], None] | None) -> None:
        """Register a callback invoked when the webapp should push a new snapshot."""
        self._webapp_notify = notify

    def post_command(self, cmd: Command) -> None:
        """Post a command to the controller from the web app."""
        self.cmd_q.put(cmd)
        self.command_pending: bool = True
        self.wake_event.set()

    def set_wake_event(self) -> None:
        """Set the wake event to wake the controller loop."""
        self.wake_event.set()

    def get_webapp_data(self) -> dict:
        """Assemble and return the full state snapshot for the webapp.

        Called from the ASGI thread (via asyncio.to_thread) and from the
        broadcast worker. Thread-safe: each topic module protects its own data.

        Returns:
            dict: Snapshot containing global metadata plus all topic data.
        """
        loop_count = 0
        while self._have_pending_commands() and loop_count < 10:
            time.sleep(0.1)
            loop_count += 1

        snapshot = self._data_manager.get_snapshot()
        snapshot["global"] = {
            "AppLabel": self.app_label,
        }
        return snapshot

    def run(self, stop_event: Event):
        """The main loop of the controller.

        Args:
            stop_event (Event): The event used to stop the controller.
        """
        self.logger.log_message("Controller starting main control loop.", "detailed")

        while not stop_event.is_set():
            time_now = DateHelper.now()
            console_msg = f"Main tick at {time_now.strftime('%H:%M:%S')}"
            self.print_to_console(console_msg)

            force_refresh = self._run_scheduler_tick()
            # Push updates periodically and immediately after commands.
            self._maybe_notify_webapp(force=force_refresh)
            self.wake_event.clear()

            # Update the last tick time after the tick is complete
            self.last_tick_time = time_now

            self.wake_event.wait(timeout=self.poll_interval)

        self.shutdown()

    def shutdown(self):
        """Shutdown the power controller, turning off outputs if configured to do so."""
        with self._io_shutdown_lock:
            self.logger.log_message("Starting Controller shutdown...", "debug")
            # TO DO: Shutdown tasks
            self.logger.log_message("Controller shutdown complete.", "detailed")

    def print_to_console(self, message: str):
        """Print a message to the console if PrintToConsole is enabled.

        Args:
            message (str): The message to print.
        """
        if self.config.get("General", "PrintToConsole", default=False):
            print(message)

    # Private Functions ===========================================================================
    def _initialise(self, startup_mode: bool | None = False):  # noqa: ARG002
        """(re) initialise the controller.

        Args:
            startup_mode (bool): If True, we're doing the initial startup initialisation. If False, we're doing a reinitialisation due to a config change.
        """
        self.poll_interval = int(self.config.get("General", "PollingIntervalSec", default=30) or 30)  # pyright: ignore[reportArgumentType]
        self.app_label = self.config.get("General", "Label", default="AppController")

    def _run_scheduler_tick(self) -> bool:
        """Do all the control processing of the main loop.

        Returns:
            bool: True if one or more commands were processed or there has been a state change.
        """
        commands_processed = self._clear_commands()          # Get all commands from the queue and apply them

        # Deal with config changes including downstream objects
        self._check_for_configuration_changes()

        # Check for fatal error recovery
        self._check_fatal_error_recovery()

        # Trim the logfile if needed
        self._trim_logfile_if_needed()

        return commands_processed

    def _check_for_configuration_changes(self):
        """Reload the configuration from disk if it has changed and apply downstream changes."""
        last_modified = self.config.check_for_config_changes(self.last_config_check)
        if last_modified:
            self.last_config_check = last_modified
            self.logger.log_message("Configuration file has changed, reloading...", "detailed")
            self._initialise()
            # TO DO: Remove this once the config manager supports granular change detection and downstream notifications (issue 8)
            self.logger.log_message("Please restart the application to apply configuration changes. Automatic reload is not yet supported.", "warning")

    def _apply_command(self, cmd: Command) -> None:
        """Apply a command posted to the controller."""
        pass

    def _have_pending_commands(self) -> bool:
        """Check if there are any pending commands in the command queue.

        Returns:
            bool: True if there are pending commands, False otherwise.
        """
        if not self.cmd_q.empty():
            return True
        return bool(self.command_pending)

    def _clear_commands(self) -> bool:
        """Clear all commands in the command queue.

        Returns:
            bool: True if one or more commands were processed.
        """
        processed = False
        while True:
            try:
                cmd = self.cmd_q.get_nowait()
            except queue.Empty:
                break
            self._apply_command(cmd)
            processed = True

        self.command_pending = False
        return processed

    def _check_fatal_error_recovery(self):
        """Check for fatal errors in the system and handle them."""
        # If the prior run fails, send email that this run worked OK
        if self.logger.get_fatal_error():
            self.logger.log_message(f"{self.app_label} started successfully after a prior failure.", "summary")
            self.logger.clear_fatal_error()
            self.logger.send_email(f"{self.app_label} recovery", "Application was successfully started after a prior critical failure.")

    def _maybe_notify_webapp(self, *, force: bool = False) -> None:
        notify = self._webapp_notify
        if not notify:
            return

        now = DateHelper.now()
        # Throttle periodic pushes to the polling interval; force=True bypasses throttle.
        if not force and self._last_webapp_notify is not None and (now - self._last_webapp_notify).total_seconds() < self.poll_interval:
            return

        self._last_webapp_notify = now
        # Web push is best-effort; do not crash the controller loop.
        with contextlib.suppress(Exception):
            notify()

    def _trim_logfile_if_needed(self) -> None:
        """Trim the logfile if needed based on time interval."""
        if not self.logger_last_trim or (DateHelper.now() - self.logger_last_trim) >= TRIM_LOGFILE_INTERVAL:
            self.logger.trim_logfile()
            self.logger_last_trim = DateHelper.now()
            self.logger.log_message("Logfile trimmed.", "debug")
