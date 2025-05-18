"""Logging configuration and utilities."""

from __future__ import annotations

import logging
import logging.config
import sys
from typing import Optional

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

# Format for console output
CONSOLE_FORMAT = (
    "%(log_color)s%(levelname)-8s%(reset)s | "
    "%(name)s | "
    "%(message_log_color)s%(message)s"
)


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
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
    else:
        formatter = colorlog.ColoredFormatter(
            CONSOLE_FORMAT,
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
        structlog.processors.format_exc_info,
        add_service_info,
    ]

    if JSON_LOGS:
        processors = shared_processors + [
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """Get a configured logger instance.

    Args:
        name: The name of the logger. If None, the root logger is used.

    Returns:
        A configured logger instance.
    """
    return structlog.get_logger(name)


# Set up default logging when the module is imported
setup_logging()
