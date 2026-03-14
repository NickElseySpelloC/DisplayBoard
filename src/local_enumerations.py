"""Holds all the local enumerations used in the project."""

import datetime as dt
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1  # Version of the system_state schema we expect
CONFIG_FILE = "config.yaml"
TRIM_LOGFILE_INTERVAL = dt.timedelta(hours=2)


# Web interface command queue =================================================
@dataclass
class Command:
    """Define the structure for commands to be posted to Controller."""
    kind: str
    payload: dict[str, Any]
