"""Where the OpenAVC server is in its life, as it recorded it in the status
folder: the one reader the Windows tray app (tray.py) and the macOS menu bar
app (menubar.py) share.

Standalone like them: it imports nothing from the server, and PyInstaller
bundles it into each from this folder. The server's side is
openavc/status_folder.py (write_run_state), and the phases are the run-state
record's (PHASES in openavc/middleware/startup_splash.py);
tests/test_status_folder.py holds the two ends together.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

STARTING, READY, FAILED, STOPPING = 'starting', 'ready', 'failed', 'stopping'
# The apps' own word for no server running.
STOPPED = 'stopped'


def process_running(pid: int) -> bool:
    """Whether a process with this id is running. The server runs as the
    service's account (root on macOS) and the apps as the signed-in user, so
    the question may be refused, and a refusal means it is there.
    ``os.kill`` asks only on macOS and Linux: on Windows it ends the process."""
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    process_query_limited_information, error_access_denied, still_active = 0x1000, 5, 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def computer_started_at() -> float:
    """When this computer last started, in seconds since the epoch. Windows
    counts the time since then, and so does macOS's monotonic clock (Linux's
    boot clock), sleep included."""
    if os.name == 'nt':
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32')
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return time.time() - kernel32.GetTickCount64() / 1000
    return time.time() - time.clock_gettime(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))


def startup_error(error_file: Path) -> str:
    """Why the server's last start failed, as it recorded it, or ''."""
    try:
        message = json.loads(error_file.read_text(encoding='utf-8')).get('message')
    except (json.JSONDecodeError, OSError, AttributeError):
        return ''
    return message if isinstance(message, str) else ''


def read_run_state(run_state_file: Path, error_file: Path) -> dict:
    """Where the server is in its life, as it recorded it: ``phase``,
    ``version``, ``error`` (a failed start's reason, else '') and ``running``,
    whether the process that recorded it still runs.

    A file the process left behind when it ended or was killed reads as
    STOPPED: its process is gone, or it was written before this computer last
    started (an ended process's id goes to another in time, soon on Windows).
    A failed start stands either way, since its process can end by itself,
    and its reason is in the startup-error file. No file, or one that cannot
    be read, reads as STOPPED.
    """
    stopped = {'phase': STOPPED, 'version': '', 'error': '', 'running': False}
    try:
        recorded = json.loads(run_state_file.read_text(encoding='utf-8'))
        phase, pid = recorded['phase'], int(recorded['pid'])
        written = datetime.fromisoformat(recorded['timestamp']).timestamp()
        version = str(recorded.get('version') or '')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return stopped
    running = written >= computer_started_at() and process_running(pid)
    if phase == FAILED:
        return {'phase': FAILED, 'version': version, 'error': startup_error(error_file), 'running': running}
    if phase not in (STARTING, READY, STOPPING) or not running:
        return dict(stopped, version=version)
    return {'phase': phase, 'version': version, 'error': '', 'running': True}


def reason_summary(message: str) -> str:
    """A failed start's reason on one line: its first paragraph (the remedy
    after it is for a dialog)."""
    return ' '.join(message.split('\n\n', 1)[0].split())
