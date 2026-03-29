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


def _get_port() -> int:
    """Read the configured HTTP port from system.json or fallback to default."""
    # Check env var first
    port_env = os.environ.get('OPENAVC_PORT')
    if port_env:
        try:
            return int(port_env)
        except ValueError:
            pass

    # Try reading system.json from ProgramData
    system_json = Path(os.environ.get('PROGRAMDATA', 'C:\\ProgramData')) / 'OpenAVC' / 'system.json'
    if system_json.exists():
        try:
            data = json.loads(system_json.read_text(encoding='utf-8'))
            return data.get('network', {}).get('http_port', DEFAULT_PORT)
        except (json.JSONDecodeError, OSError):
            pass

    return DEFAULT_PORT


def _api_get(path: str, port: int, timeout: float = 3.0) -> dict | None:
    """Make a GET request to the local OpenAVC API. Returns parsed JSON or None."""
    url = f'http://127.0.0.1:{port}{path}'
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def _service_command(command: str) -> bool:
    """Run an NSSM service command (start/stop/restart). Returns True on success."""
    try:
        result = subprocess.run(
            [NSSM_PATH, command, SERVICE_NAME],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _is_service_installed() -> bool:
    """Check if the OpenAVC service is installed via NSSM."""
    try:
        result = subprocess.run(
            [NSSM_PATH, 'status', SERVICE_NAME],
            capture_output=True,
            timeout=10,
        )
        # NSSM returns 0 for running, 3 for stopped, non-zero error for not installed
        return result.returncode in (0, 3)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


class OpenAVCTray:
    """OpenAVC system tray application."""

    def __init__(self):
        self._port = _get_port()
        self._running = True
        self._server_status: str = 'unknown'  # 'running', 'stopped', 'unknown'
        self._version: str = '0.1.0'
        self._device_info: str = ''
        self._update_available: str = ''

    def _poll_status(self):
        """Background thread that polls the server health endpoint."""
        while self._running:
            health = _api_get('/api/health', self._port)
            if health and health.get('status') == 'healthy':
                self._server_status = 'running'
                self._version = health.get('version', self._version)
                devices = health.get('devices', {})
                total = devices.get('total', 0)
                connected = devices.get('connected', 0)
                if total > 0:
                    self._device_info = f'{connected}/{total} devices online'
                else:
                    self._device_info = ''
            else:
                self._server_status = 'stopped'
                self._device_info = ''

            time.sleep(POLL_INTERVAL)

    def _open_programmer(self, systray):
        webbrowser.open(f'http://localhost:{self._port}/programmer')

    def _open_panel(self, systray):
        webbrowser.open(f'http://localhost:{self._port}/panel')

    def _check_updates(self, systray):
        result = _api_get('/api/system/updates/check', self._port, timeout=15)
        if result is None:
            # Server not reachable
            return
        if result.get('update_available'):
            version = result.get('available_version', '?')
            self._update_available = version
            # Update the tooltip
            systray.update(hover_text=self._build_tooltip())

    def _start_service(self, systray):
        _service_command('start')

    def _stop_service(self, systray):
        _service_command('stop')

    def _restart_service(self, systray):
        _service_command('restart')

    def _on_quit(self, systray):
        self._running = False

    def _build_tooltip(self) -> str:
        """Build the tooltip text for the tray icon."""
        parts = [f'OpenAVC v{self._version}']
        if self._server_status == 'running':
            parts.append('Running')
            if self._device_info:
                parts.append(self._device_info)
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
