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
STARTUP_ERROR_FILE = DATA_DIR / 'startup-error.json'


def _get_server_config() -> dict:
    """Read the relevant subset of system.json at tray startup.

    Returns: {"http_port", "tls_enabled", "tls_port"}.
    Falls back to defaults on any read/parse failure — the tray must keep
    running even if system.json is malformed.
    """
    result = {
        "http_port": DEFAULT_PORT,
        "tls_enabled": False,
        "tls_port": 8443,
    }

    # Env-var overrides (mirror server/system_config.py ENV_OVERRIDES)
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

    system_json = Path(os.environ.get('PROGRAMDATA', 'C:\\ProgramData')) / 'OpenAVC' / 'system.json'
    if system_json.exists():
        try:
            data = json.loads(system_json.read_text(encoding='utf-8'))
            if 'OPENAVC_PORT' not in os.environ:
                result["http_port"] = data.get('network', {}).get('http_port', DEFAULT_PORT)
            tls_section = data.get('tls', {}) if isinstance(data.get('tls'), dict) else {}
            if 'OPENAVC_TLS_ENABLED' not in os.environ:
                result["tls_enabled"] = bool(tls_section.get('enabled', False))
            if 'OPENAVC_TLS_PORT' not in os.environ:
                result["tls_port"] = int(tls_section.get('port', 8443))
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    return result


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
            creationflags=subprocess.CREATE_NO_WINDOW,
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
            else:
                self._server_status = 'stopped'
                self._device_info = ''
                self._update_available = ''
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

    def _on_quit(self, systray):
        self._running = False

    def _build_tooltip(self) -> str:
        """Build the tooltip text for the tray icon."""
        title = f'OpenAVC v{self._version}' if self._version else 'OpenAVC'
        parts = [title]
        if self._server_status == 'running':
            parts.append('Running')
            if self._device_info:
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
    app = OpenAVCTray()
    app.run()


if __name__ == '__main__':
    main()
