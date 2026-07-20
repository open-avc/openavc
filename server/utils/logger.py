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
# References to the mutable handlers so their settings can be re-applied at
# runtime (PATCH /system/config) without a restart.
_console_handler: logging.Handler | None = None
_file_handler: logging.Handler | None = None


def _build_file_handler(formatter: logging.Formatter) -> logging.Handler | None:
    """Build the rotating file handler from the current logging config.

    Honors ``logging.file_enabled`` / ``max_size_mb`` / ``max_files`` (defaults
    50 MB per file, 5 rotated files). Returns ``None`` when file logging is
    disabled or the log directory can't be prepared, so callers just skip the
    handler. Bad/non-numeric sizes fall back to the defaults rather than
    producing an unbounded or broken handler.
    """
    from server.system_config import get_log_dir, get_system_config

    cfg = get_system_config()
    if not cfg.get("logging", "file_enabled", True):
        return None

    try:
        max_mb = float(cfg.get("logging", "max_size_mb", 50))
        if max_mb <= 0:
            max_mb = 50.0
    except (TypeError, ValueError):
        max_mb = 50.0
    try:
        max_files = int(cfg.get("logging", "max_files", 5))
        if max_files < 0:
            max_files = 5
    except (TypeError, ValueError):
        max_files = 5

    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(log_dir / "openavc.log"),
        maxBytes=int(max_mb * 1024 * 1024),
        backupCount=max_files,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(formatter)
    return handler


class _ProactorResetFilter(logging.Filter):
    """Silence the benign Windows proactor connection-reset traceback.

    On Windows, a browser dropping a kept-alive HTTP connection makes
    asyncio's proactor loop log a full ``ConnectionResetError: [WinError
    10054]`` traceback from ``_ProactorBasePipeTransport._call_connection_lost``
    — at ERROR level, in recurring pairs, during entirely normal operation.
    No request fails and no device traffic is affected (that lives in its own
    transports), but the noise buries real ERROR lines in the server log.

    Deliberately narrow: it drops a record only when all three hold — the
    exception is a ConnectionResetError, it came from that specific proactor
    callback, and we're on Windows. A genuine connection-reset logged from
    anywhere else still gets through.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if not isinstance(exc, ConnectionResetError):
            return True
        return "_call_connection_lost" not in record.getMessage()


def _configure_root():
    """Configure the root logger once."""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Console output honors the configured level. This keeps external/terminal
    # output from being flooded by the always-on device traffic captured for
    # the in-app log (see the transport pin below) — that traffic goes to the
    # in-memory buffer, not necessarily to stdout.
    try:
        from server.system_config import get_system_config
        _console_level = getattr(
            logging,
            str(get_system_config().get("logging", "level", "info")).upper(),
            logging.INFO,
        )
    except Exception:
        _console_level = logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(_console_level)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    global _console_handler
    _console_handler = handler

    # Persistent file logging, honoring the configured file_enabled /
    # max_size_mb / max_files settings.
    global _file_handler
    try:
        _file_handler = _build_file_handler(formatter)
        if _file_handler is not None:
            root.addHandler(_file_handler)
    except Exception:
        pass  # Don't fail startup if log dir isn't writable

    # Feed all log output into the in-memory buffer for WebSocket streaming
    from server.utils.log_buffer import get_log_buffer, BufferHandler
    buffer_handler = BufferHandler(get_log_buffer())
    buffer_handler.setLevel(logging.DEBUG)
    root.addHandler(buffer_handler)

    # Device protocol traffic (transport TX/RX) is logged at DEBUG. Pin the
    # transport loggers to DEBUG so that traffic is always captured for the
    # Programmer's per-device log, independent of the global log level (which
    # the console and file handlers still respect). Without this, the default
    # INFO level drops every TX/RX before it reaches the buffer and the device
    # log stays empty.
    logging.getLogger("server.transport").setLevel(logging.DEBUG)

    # Drop the benign Windows proactor connection-reset tracebacks (see the
    # filter's docstring). Attached to the asyncio logger rather than a loop
    # exception handler because the server has three entry paths (TLS, HTTP
    # redirect, and a bare uvicorn.run that owns its own loop) — a logger
    # filter covers all three from one place.
    if sys.platform == "win32":
        logging.getLogger("asyncio").addFilter(_ProactorResetFilter())


def set_log_level(level: str) -> bool:
    """Apply a new console log level at runtime (no restart required).

    Only the console handler tracks the configured ``logging.level``; the file
    handler stays at INFO, and the in-memory buffer plus transport loggers stay
    at DEBUG so per-device traffic is always captured. Returns True if the level
    string was recognized and applied, False otherwise.
    """
    _configure_root()
    resolved = logging.getLevelName(str(level).upper())
    if not isinstance(resolved, int):
        return False
    if _console_handler is not None:
        _console_handler.setLevel(resolved)
    return True


def set_file_logging() -> None:
    """Re-apply the persistent file-logging settings at runtime (no restart).

    Rebuilds the rotating file handler from the current config so toggling
    ``file_enabled`` or changing ``max_size_mb`` / ``max_files`` takes effect
    immediately — the same live-apply contract ``set_log_level`` gives the
    console level.
    """
    _configure_root()
    global _file_handler
    root = logging.getLogger()

    if _file_handler is not None:
        root.removeHandler(_file_handler)
        try:
            _file_handler.close()
        except Exception:
            pass
        _file_handler = None

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    try:
        _file_handler = _build_file_handler(formatter)
        if _file_handler is not None:
            root.addHandler(_file_handler)
    except Exception:
        pass


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
