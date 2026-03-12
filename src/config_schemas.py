"""Configuration schemas for use with the SCConfigManager class."""


class ConfigSchema:
    """Base class for configuration schemas."""

    def __init__(self):
        # Validation schema to be passed to the SCConfigManager for validating the YAML configuration file.
        self.validation = {
            # Note: The schema for the Files: and Email: sections are provided by SCConfigManager

            "General": {
                "type": "dict",
                "required": True,
                "schema": {
                    "Label": {"type": "string", "required": False, "nullable": True},
                    "PollingIntervalSec": {"type": "number", "required": False, "nullable": True, "min": 5, "max": 600},
                    "ReportCriticalErrorsDelay": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 3600},
                    "PrintToConsole": {"type": "boolean", "required": False, "nullable": True},
                },
            },

            "Website": {
                "type": "dict",
                "required": True,
                "schema": {
                    "HostingIP": {"type": "string", "required": True},
                    "Port": {"type": "number", "required": False, "nullable": True, "min": 80, "max": 65535},
                    "PageAutoRefreshSec": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 3600},
                    "DebugMode": {"type": "boolean", "required": False, "nullable": True},
                    "AccessKey": {"type": "string", "required": False, "nullable": True},
                },
            },

            "DisplayBoards": {
                "type": "dict",
                "required": True,
                "schema": {
                    "AutoRotateSec": {"type": "number", "required": False, "nullable": True, "min": 0, "max": 3600},
                    "Boards": {
                        "type": "list",
                        "required": True,
                        "schema": {
                            "type": "dict",
                            "schema": {
                                "Name": {"type": "string", "required": True},
                                "Template": {"type": "string", "required": True},
                                "BackgroundImageLibrary": {"type": "string", "required": False, "nullable": True},
                            },
                        },
                    },
                },
            },


            "BackgroundImages": {
                "type": "dict",
                "required": False,
                "schema": {
                    "AutoRotateMin": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 1440},
                    "Libraries": {
                        "type": "list",
                        "required": True,
                        "schema": {
                            "type": "dict",
                            "schema": {
                                "Name": {"type": "string", "required": True},
                                "Type": {"type": "string", "required": True, "allowed": ["pexels", "unsplash"]},
                                "AccessKey": {"type": "string", "required": False, "nullable": True},
                                "Query": {"type": "string", "required": False, "nullable": True},
                            },
                        },
                    },
                },
            },


            "TopicWeather": {
                "type": "dict",
                "required": False,
                "schema": {
                    "Latitude": {"type": "float", "required": True},
                    "Longitude": {"type": "float", "required": True},
                    "RefreshIntervalMin": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 1440},
                    "OWMAPIKey": {"type": "string", "required": False, "nullable": True},
                },
            },

            "TopicPowerController": {
                "type": "dict",
                "required": False,
                "schema": {
                    "RefreshIntervalSec": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 1440},
                    "DataAPIBaseURL": {"type": "string", "required": True},
                    "AccessKey": {"type": "string", "required": False, "nullable": True},
                },
            },

            "TopicCalendar": {
                "type": "dict",
                "required": False,
                "schema": {
                    "RefreshIntervalMin": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 1440},
                },
            },

            "TopicWebPage": {
                "type": "list",
                "required": False,
                "schema": {
                    "type": "dict",
                    "schema": {
                        "Name": {"type": "string", "required": True},
                        "URL": {"type": "string", "required": True},
                        "RefreshIntervalSec": {"type": "number", "required": False, "nullable": True, "min": 1, "max": 3600},
                    },
                },
            },
        }
