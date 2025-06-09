"""Logging configuration and utilities."""

from __future__ import annotations

import logging
import logging.config
import sys

import colorlog
import structlog
from structlog.types import EventDict, Processor

from .config import JSON_LOGS, LOG_LEVEL

# Color scheme for console output
LOG_COLORS = {
    "DEBUG": "cyan",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "red,bg_white",
}

# Format for console output - include timestamp but let structlog handle level/name
CONSOLE_FORMAT = "%(asctime)s | %(message_log_color)s%(message)s"


def add_service_info(
    logger: logging.Logger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Add service context to log records."""
    record = event_dict.get("_record")
    if record:
        event_dict["filename"] = record.filename
        event_dict["funcName"] = record.funcName
        event_dict["lineno"] = record.lineno
    return event_dict


def setup_logging() -> None:
    """Configure logging with structlog and colorlog."""
    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    # Clear any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Configure console handler
    if JSON_LOGS:
        # For JSON logs, just pass through the structured message
        formatter = logging.Formatter("%(message)s")
    else:
        formatter = colorlog.ColoredFormatter(
            CONSOLE_FORMAT,
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors=LOG_COLORS,
            secondary_log_colors={"message": LOG_COLORS},
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Configure structlog
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        add_service_info,
    ]

    if JSON_LOGS:
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(
                colors=True,
                level_styles={
                    "critical": "\033[41m",
                    "exception": "\033[41m",
                    "error": "\033[31m",
                    "warn": "\033[33m",
                    "warning": "\033[33m",
                    "info": "\033[32m",
                    "debug": "\033[36m",
                },
            ),
        ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a configured logger instance.

    Args:
        name: The name of the logger. If None, the root logger is used.

    Returns:
        A configured logger instance.
    """
    return structlog.get_logger(name)


# Set up default logging when the module is imported
setup_logging()
