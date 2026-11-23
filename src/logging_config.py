"""Logging configuration for AgentForge.

Provides structured logging with consistent format across all modules.
"""

from __future__ import annotations

import logging
import sys

from src.config import settings
from src.llm.redact import sanitize


class RedactFormatter(logging.Formatter):
    """Format a record, then strip credentials from the rendered line."""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize(super().format(record))


def setup_logging() -> None:
    """Configure logging for the application.

    Sets up:
    - Console handler with colored output
    - Consistent format: timestamp | level | module | message
    - Log level from settings
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    # Create formatter
    formatter = RedactFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)

    # Add handler to root logger
    root_logger.addHandler(console_handler)

    # Set specific logger levels
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)

    logging.info("Logging configured: level=%s", settings.LOG_LEVEL)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)
