"""
OpenAVC System Tray Application.

Lightweight tray app that communicates with the OpenAVC server via the
localhost REST API. It does NOT run the server. The Windows service does
that independently.

Right-click menu:
  OpenAVC v0.1.0
  [status indicator]
  Open Programmer IDE
  Open Panel UI
  Check for Updates
  Reset Programmer Password...
  Start/Stop/Restart Service
  Exit

Uses infi.systray (BSD license) for Windows system tray integration.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# Windows-only flag; 0 elsewhere, so the module imports (and is tested) anywhere.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Determine paths
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    APP_DIR = Path(sys.executable).parent
    ICON_PATH = str(APP_DIR / 'openavc.ico')
    NSSM_PATH = str(APP_DIR / 'nssm.exe')
else:
    # Running from source
    APP_DIR = Path(__file__).parent
    ICON_PATH = str(APP_DIR / 'openavc.ico')
    NSSM_PATH = 'nssm.exe'

SERVICE_NAME = 'OpenAVC'
DEFAULT_PORT = 8080
POLL_INTERVAL = 5  # seconds
DATA_DIR = Path(os.environ.get('PROGRAMDATA', 'C:\\ProgramData')) / 'OpenAVC'
# The installer restricts the data folder to the service and administrators,
# and the tray runs as the signed-in user, so the tray reads only status\, the
# one folder every local account can read. The server writes both files there
# (STATUS_DIR_NAME and the file names in openavc/main.py).
STATUS_DIR = DATA_DIR / 'status'
STARTUP_ERROR_FILE = STATUS_DIR / 'startup-error.json'
SERVER_ADDRESS_FILE = STATUS_DIR / 'server.json'
# A file by this name in the data folder makes the server clear the Programmer
# login at its next start (RESET_REQUEST_NAMES in openavc/api/auth.py). The
# folder is administrators-only, so the tray relaunches itself elevated with
# RESET_ARG to write it and restart the service.
RESET_REQUEST_FILE = DATA_DIR / 'reset-programmer-password'
RESET_ARG = '--reset-programmer-password'
# `openavc-tray.exe --open programmer` (or `panel`) opens that page on the port
# the server last recorded, then exits. The installer's desktop and Start menu
# shortcuts go through it, because a fixed http://localhost:8080 link breaks
# as soon as the port is changed in Settings > Network.
OPEN_ARG = '--open'
OPEN_PAGES = {'programmer': '/programmer', 'panel': '/panel'}
# One tray icon per signed-in user: the Start menu has an OpenAVC Tray entry
# for bringing it back after Quit, and clicking it while the icon is up must
# not add a second one.
TRAY_MUTEX = 'Local\\OpenAVCTray'


def _get_server_config() -> dict:
    """Read the ports the server listens on, as it recorded them at its last start.

    Returns: {"http_port", "tls_enabled", "tls_port"}.
    Falls back to defaults on any read/parse failure — the tray must keep
    running before the server has ever started, or if the file is malformed.
    """
    result = {
        "http_port": DEFAULT_PORT,
        "tls_enabled": False,
        "tls_port": 8443,
    }

    # Env-var overrides (mirror openavc/system_config.py ENV_OVERRIDES)
    port_env = os.environ.get('OPENAVC_PORT')
    if port_env:
        try:
            result["http_port"] = int(port_env)
        except ValueError:
            pass
    tls_env = os.environ.get('OPENAVC_TLS_ENABLED', '').lower()
    if tls_env in ('true', '1', 'yes'):
        result["tls_enabled"] = True
    elif tls_env in ('false', '0', 'no'):
        result["tls_enabled"] = False
    tls_port_env = os.environ.get('OPENAVC_TLS_PORT')
    if tls_port_env:
        try:
            result["tls_port"] = int(tls_port_env)
        except ValueError:
            pass

    # The existence check is inside the try too: if status\ is ever refused to
    # the signed-in user, even a stat fails, and that must leave the tray on the
    # defaults rather than stop it starting.
    try:
        if SERVER_ADDRESS_FILE.exists():
            data = json.loads(SERVER_ADDRESS_FILE.read_text(encoding='utf-8'))
            if 'OPENAVC_PORT' not in os.environ:
                result["http_port"] = int(data.get('http_port', DEFAULT_PORT))
            if 'OPENAVC_TLS_ENABLED' not in os.environ:
                result["tls_enabled"] = bool(data.get('tls_enabled', False))
            if 'OPENAVC_TLS_PORT' not in os.environ:
                result["tls_port"] = int(data.get('tls_port', 8443))
    except (json.JSONDecodeError, OSError, ValueError, TypeError, AttributeError):
        pass

    return result


def _get_listen_problem() -> str:
    """The server's own sentence when it is not accepting connections from the
    network it was given (the chosen adapter is unplugged or has no address),
    or ''. Recorded with the ports at every start, so it changes when the
    server restarts on the adapter coming back.
    """
    try:
        data = json.loads(SERVER_ADDRESS_FILE.read_text(encoding='utf-8'))
        problem = data.get('listen', {}).get('problem')
    except (json.JSONDecodeError, OSError, AttributeError, TypeError, ValueError):
        return ''
    return problem if isinstance(problem, str) else ''


def _get_port() -> int:
    """Back-compat shim: HTTP port only."""
    return _get_server_config()["http_port"]


def _base_url(server_cfg: dict) -> str:
    """Browser-facing base URL (scheme + host + port).

    When TLS is on, points at https://localhost:<tls_port>. The browser will
    show a one-time security warning until the CA cert is installed.
    """
    if server_cfg.get("tls_enabled"):
        return f"https://localhost:{server_cfg['tls_port']}"
    return f"http://localhost:{server_cfg['http_port']}"


def _api_get(path: str, server_cfg: dict, timeout: float = 3.0) -> dict | None:
    """Make a GET request to the local OpenAVC API. Returns parsed JSON or None.

    On TLS-enabled installs, hits the TLS port directly with cert verification
    disabled (loopback self-signed cert — same trust model as the rest of
    OpenAVC's internal callers).
    """
    if server_cfg.get("tls_enabled"):
        url = f"https://127.0.0.1:{server_cfg['tls_port']}{path}"
        try:
            import ssl
            ctx = ssl._create_unverified_context()
            req = Request(url, headers={'Accept': 'application/json'})
            with urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except (URLError, OSError, json.JSONDecodeError, TimeoutError):
            return None
    url = f"http://127.0.0.1:{server_cfg['http_port']}{path}"
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def _service_command(command: str) -> bool:
    """Run an NSSM service command (start/stop/restart). Returns True on success.

    Service control requires admin privileges on Windows. First tries without
    elevation; if that fails, requests UAC elevation via ShellExecuteW.
    """
    try:
        # CREATE_NO_WINDOW: the tray is a windowed app with no console, so
        # without it every nssm call flashes a console window on the desktop.
        result = subprocess.run(
            [NSSM_PATH, command, SERVICE_NAME],
            capture_output=True,
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    # Non-zero exit — likely access denied. Retry with UAC elevation.
    try:
        import ctypes
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', NSSM_PATH, f'{command} {SERVICE_NAME}', None, 0,
        )
        # ShellExecuteW returns > 32 on success
        return ret > 32
    except Exception:
        return False


def _open_page(page: str) -> int:
    """The --open half: open one page in the browser and return an exit code."""
    path = OPEN_PAGES.get(page)
    if path is None:
        return 2
    webbrowser.open(f'{_base_url(_get_server_config())}{path}')
    return 0


_tray_mutex = None


def _tray_already_running() -> bool:
    """True when this user's session already shows the tray icon. The mutex
    handle is kept for the life of the process, which is what holds the claim."""
    global _tray_mutex
    try:
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        _tray_mutex = kernel32.CreateMutexW(None, False, TRAY_MUTEX)
        return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def _reset_programmer_password_elevated() -> int:
    """The elevated half of Reset Programmer Password: write the request
    file, restart the service so the server applies it. Returns an exit code."""
    try:
        RESET_REQUEST_FILE.write_text('')
    except OSError:
        return 1
    try:
        result = subprocess.run(
            [NSSM_PATH, 'restart', SERVICE_NAME],
            capture_output=True,
            timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 1
    return 0 if result.returncode == 0 else 1


def _launch_elevated_reset() -> bool:
    """Relaunch this program elevated with RESET_ARG. False when it could not
    start, most often because the Windows prompt was declined."""
    if getattr(sys, 'frozen', False):
        exe, params = sys.executable, RESET_ARG
    else:
        exe, params = sys.executable, f'"{Path(__file__).resolve()}" {RESET_ARG}'
    try:
        import ctypes
        ret = ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe, params, None, 0)
    except Exception:
        return False
    # ShellExecuteW returns > 32 on success
    return ret > 32


def _ask_yes_no(title: str, message: str) -> bool:
    """A Yes/No Windows message box. True for Yes."""
    try:
        import ctypes
        # MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2 | MB_TOPMOST; IDYES == 6
        return ctypes.windll.user32.MessageBoxW(0, message, title, 0x4 | 0x20 | 0x100 | 0x40000) == 6
    except Exception:
        return False


def _show_info_dialog(title: str, message: str) -> None:
    try:
        import ctypes
        # MB_OK | MB_ICONINFORMATION | MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x40000)
    except Exception:
        pass


def _show_error_dialog(title: str, message: str) -> None:
    """Show a Windows message box on a background thread (non-blocking)."""
    try:
        import ctypes
        # MB_OK | MB_ICONWARNING | MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x30 | 0x40000)
    except Exception:
        pass


class OpenAVCTray:
    """OpenAVC system tray application."""

    def __init__(self):
        self._server_cfg = _get_server_config()
        self._port = self._server_cfg["http_port"]  # back-compat
        self._base_url = _base_url(self._server_cfg)
        self._running = True
        self._server_status: str = 'unknown'  # 'running', 'stopped', 'unknown'
        self._version: str = ''
        self._device_info: str = ''
        self._update_available: str = ''
        self._listen_problem: str = ''
        self._startup_error: str = ''
        self._error_shown: bool = False

    def _check_startup_error(self) -> None:
        """Check for a startup error file written by the server."""
        try:
            if not STARTUP_ERROR_FILE.exists():
                self._startup_error = ''
                self._error_shown = False
                return

            data = json.loads(STARTUP_ERROR_FILE.read_text(encoding='utf-8'))
            self._startup_error = data.get('message', 'Unknown startup error')

            if not self._error_shown:
                self._error_shown = True
                t = threading.Thread(
                    target=_show_error_dialog,
                    args=('OpenAVC - Failed to Start', self._startup_error),
                    daemon=True,
                )
                t.start()

        except (json.JSONDecodeError, OSError):
            pass

    def _poll_status(self):
        """Background thread that polls the server health endpoint."""
        while self._running:
            # Read where the server listens at every poll, not once: a port
            # changed in Settings is recorded when the restart applies it.
            self._server_cfg = _get_server_config()
            self._port = self._server_cfg["http_port"]
            self._base_url = _base_url(self._server_cfg)
            health = _api_get('/api/health', self._server_cfg)
            if health and health.get('status') == 'healthy':
                self._server_status = 'running'
                self._version = health.get('version', self._version)
                self._startup_error = ''
                self._error_shown = False
                devices = health.get('devices', {})
                total = devices.get('total', 0)
                connected = devices.get('connected', 0)
                if total > 0:
                    self._device_info = f'{connected}/{total} devices online'
                else:
                    self._device_info = ''
                # Passive update indicator: the server's periodic auto-check
                # caches the available version and reports it here (empty when
                # up to date), so the tooltip can surface it without the tray
                # itself hitting the network.
                self._update_available = health.get('update_available', '') or ''
                self._listen_problem = _get_listen_problem()
            else:
                self._server_status = 'stopped'
                self._device_info = ''
                self._update_available = ''
                self._listen_problem = ''
                self._check_startup_error()

            time.sleep(POLL_INTERVAL)

    def _open_programmer(self, systray):
        webbrowser.open(f'{self._base_url}/programmer')

    def _open_panel(self, systray):
        webbrowser.open(f'{self._base_url}/panel')

    def _check_updates(self, systray):
        """Trigger an update check, then open the Updates view in the browser."""
        # Fire-and-forget: tell the server to check now
        _api_get('/api/system/updates/check', self._server_cfg, timeout=15)
        # Open the Programmer IDE Updates view so the user can see the result.
        # The hash must be '#updates' (no slash): the IDE router strips the
        # leading '#' and matches the bare view id, so '#/updates' falls back
        # to the Dashboard and the update result never shows.
        webbrowser.open(f'{self._base_url}/programmer#updates')

    def _run_service_command(self, command: str) -> None:
        """Run a service command and surface a dialog if it clearly fails.

        Previously a failed command (e.g. the service isn't installed, or the
        user declined the elevation prompt) did nothing at all, which looked
        like the menu item was broken. Now the failure is at least visible.
        """
        if _service_command(command):
            return
        t = threading.Thread(
            target=_show_error_dialog,
            args=(
                'OpenAVC - Service control failed',
                f'Could not {command} the OpenAVC service.\n\n'
                'This needs administrator approval. Try again and accept the '
                'Windows prompt. If the service is missing, reinstall OpenAVC.',
            ),
            daemon=True,
        )
        t.start()

    def _start_service(self, systray):
        self._run_service_command('start')

    def _stop_service(self, systray):
        self._run_service_command('stop')

    def _restart_service(self, systray):
        self._run_service_command('restart')

    def _reset_programmer_password(self, systray):
        # Off the menu thread: the message boxes wait for a click.
        threading.Thread(target=self._reset_programmer_password_flow, daemon=True).start()

    def _reset_programmer_password_flow(self) -> None:
        title = 'OpenAVC - Reset Programmer Password'
        if not _ask_yes_no(
            title,
            'This clears the Programmer username, password and API key, and '
            'restarts OpenAVC. The next time you open the Programmer it asks you '
            'to choose a new username and password.\n\n'
            'Your project, devices, panels and cloud connection are not changed. '
            'Anything that uses the API key will need a new one.\n\n'
            'Reset now?',
        ):
            return
        if not _launch_elevated_reset():
            _show_error_dialog(
                title,
                'The password was not reset.\n\n'
                'This needs administrator approval. Try again and accept the '
                'Windows prompt.',
            )
            return
        _show_info_dialog(
            title,
            'OpenAVC is restarting. In a moment, open the Programmer and choose '
            'a new username and password.',
        )

    def _on_quit(self, systray):
        self._running = False

    def _build_tooltip(self) -> str:
        """Build the tooltip text for the tray icon."""
        title = f'OpenAVC v{self._version}' if self._version else 'OpenAVC'
        parts = [title]
        if self._server_status == 'running':
            parts.append('Running')
            # Ahead of the device count, which it replaces: Windows cuts a
            # tooltip off at 128 characters.
            if self._listen_problem:
                parts.append(self._listen_problem)
            elif self._device_info:
                parts.append(self._device_info)
        elif self._startup_error:
            parts.append('Error - see popup')
        else:
            parts.append('Stopped')
        if self._update_available:
            parts.append(f'Update available: v{self._update_available}')
        return ' - '.join(parts)

    def run(self):
        """Start the tray application."""
        from infi.systray import SysTrayIcon

        # Start status polling thread
        poll_thread = threading.Thread(target=self._poll_status, daemon=True)
        poll_thread.start()

        # Build menu
        # infi.systray auto-appends "Quit" at the bottom
        menu_options = (
            ('Open Programmer IDE', None, self._open_programmer),
            ('Open Panel UI', None, self._open_panel),
            ('Check for Updates', None, self._check_updates),
            ('Reset Programmer Password...', None, self._reset_programmer_password),
            ('Service', None, (
                ('Start Service', None, self._start_service),
                ('Stop Service', None, self._stop_service),
                ('Restart Service', None, self._restart_service),
            )),
        )

        hover_text = self._build_tooltip()

        # Use our icon if it exists, otherwise None (default system icon)
        icon_path = ICON_PATH if os.path.exists(ICON_PATH) else None

        systray = SysTrayIcon(
            icon_path,
            hover_text,
            menu_options=menu_options,
            on_quit=self._on_quit,
            default_menu_index=0,  # Double-click opens Programmer IDE
        )

        systray.start()

        # Keep updating the tooltip while running
        try:
            while self._running:
                time.sleep(POLL_INTERVAL)
                systray.update(hover_text=self._build_tooltip())
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            try:
                systray.shutdown()
            except Exception:
                pass


def main():
    args = sys.argv[1:]
    if RESET_ARG in args:
        sys.exit(_reset_programmer_password_elevated())
    if OPEN_ARG in args:
        at = args.index(OPEN_ARG)
        sys.exit(_open_page(args[at + 1] if at + 1 < len(args) else 'programmer'))
    if _tray_already_running():
        return
    app = OpenAVCTray()
    app.run()


if __name__ == '__main__':
    main()
