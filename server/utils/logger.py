"""
OpenAVC structured logging utility.

Provides consistent log formatting across all modules.
Usage:
    from server.utils.logger import get_logger
    log = get_logger(__name__)
    log.info("Something happened")
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

# Format: [timestamp] [LEVEL] [module] message
LOG_FORMAT = "[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _configure_root():
    """Configure the root logger once."""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Console output
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Persistent file logging (10 MB per file, 3 rotated files)
    try:
        from server.system_config import get_log_dir
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(log_dir / "openavc.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except Exception:
        pass  # Don't fail startup if log dir isn't writable

    # Feed all log output into the in-memory buffer for WebSocket streaming
    from server.utils.log_buffer import get_log_buffer, BufferHandler
    buffer_handler = BufferHandler(get_log_buffer())
    buffer_handler.setLevel(logging.DEBUG)
    root.addHandler(buffer_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger with consistent formatting.

    Args:
        name: Module name, typically __name__ (e.g., "server.core.state_store")

    Returns:
        Configured Logger instance.
    """
    _configure_root()
    return logging.getLogger(name)
