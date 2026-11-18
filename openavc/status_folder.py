"""What the server writes for the Windows tray app and the macOS menu bar.

The data folder's status/ subfolder holds what they read. Both run as the
signed-in user. On Windows the installer leaves this one folder readable (and
only readable) by every local account, while the rest of the data folder
belongs to the service and administrators (installer/secure-data-dir.bat); on
macOS the server runs as root, so server.json and run-state.json are written
readable by everyone (STATUS_FILE_MODE). Nothing secret may be written here.
installer/tray.py and installer/menubar.py are standalone scripts that name
the same folder and files; tests/test_status_folder.py holds the two ends
together.

Three files: where the server listens (server.json, at every start once bound),
where it is in its life (run-state.json, at each phase the run-state record
reaches, and at a port's or TLS's failure, which comes before any record), and
why its start failed (startup-error.json, written only with that phase).
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from openavc import config

STATUS_DIR_NAME = "status"
STARTUP_ERROR_FILE_NAME = "startup-error.json"
SERVER_ADDRESS_FILE_NAME = "server.json"
RUN_STATE_FILE_NAME = "run-state.json"
STATUS_FILE_MODE = 0o644
# What a start that a stage of its own stopped is filed under (a port or TLS
# failure names its own).
START_FAILED = "start_failed"


def _status_dir():
    from openavc.system_config import get_data_dir
    return get_data_dir() / STATUS_DIR_NAME


def write_run_state(
    phase: str, version: str, error: str | None = None, error_type: str = START_FAILED,
) -> None:
    """Record where this process is in its life, for the tray app and the
    menu bar: the phase (the run-state record's, middleware/startup_splash.py),
    the version that started, and ``pid`` and ``timestamp``, so a reader can
    tell a file the process left behind when it ended. A start that failed
    passes its reason, ``error``, which goes to the startup-error file the
    tray's dialog reads first, so a reader that sees the phase finds it.
    Never raises."""
    from openavc.utils.fileio import atomic_write_text
    if error is not None:
        _write_startup_error(error_type, error)
    try:
        status_dir = _status_dir()
        status_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(status_dir / RUN_STATE_FILE_NAME, json.dumps({
            "phase": phase,
            "version": version,
            "pid": os.getpid(),
            "timestamp": datetime.now().isoformat(),
        }), mode=STATUS_FILE_MODE)
    except OSError:
        pass


def _write_startup_error(error_type: str, message: str) -> None:
    """Why the start failed, for the tray app (or other monitors) to report;
    written only with the phase (``write_run_state``)."""
    try:
        status_dir = _status_dir()
        status_dir.mkdir(parents=True, exist_ok=True)
        error_file = status_dir / STARTUP_ERROR_FILE_NAME
        error_file.write_text(json.dumps({
            "error": error_type,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }), encoding="utf-8")
    except OSError:
        pass


def clear_startup_error() -> None:
    """Remove the startup error file on successful start."""
    try:
        error_file = _status_dir() / STARTUP_ERROR_FILE_NAME
        if error_file.exists():
            error_file.unlink()
    except OSError:
        pass


def write_server_address() -> None:
    """Record where this server listens, for the tray app and the menu bar.

    The tray cannot read system.json, and it cannot ask the server without
    already knowing the port. These are the values the server binds,
    environment overrides included, rewritten at every start once the
    sockets are bound, so a port or a listen choice changed in Settings
    reaches the tray with the restart that applies it. ``listen`` carries the
    addresses listened on and, when the chosen adapter is missing, the
    sentence that says so.
    """
    from openavc.utils.fileio import atomic_write_text
    plan = config.listen_plan()
    try:
        status_dir = _status_dir()
        status_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(status_dir / SERVER_ADDRESS_FILE_NAME, json.dumps({
            "http_port": config.HTTP_PORT,
            "tls_enabled": config.TLS_ENABLED,
            "tls_port": config.TLS_PORT,
            "listen": {
                "mode": plan.mode,
                "addresses": list(plan.hosts),
                "problem": plan.problem.message if plan.problem else None,
            },
        }), mode=STATUS_FILE_MODE)
    except OSError:
        pass
