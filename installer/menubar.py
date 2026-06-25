"""OpenAVC macOS menu-bar application.

The macOS analog of the Windows tray app (installer/tray.py). A lightweight
status item that talks to the local OpenAVC server over the localhost REST API.
It does NOT run the server — the LaunchDaemon does that independently. This app
just shows status and offers quick actions.

Menu:
  OpenAVC v0.0.0
  [status line]
  Open Programmer IDE
  Open Panel UI
  Check for Updates
  Service > Start / Stop / Restart
  Quit            (added by rumps)

Uses rumps (BSD-3) for the menu bar; service control shells out to launchctl,
elevating through the macOS authentication dialog because the server runs as a
system LaunchDaemon (root).
"""

from __future__ import annotations

import json
import os
import subprocess
import ssl
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import rumps

DEFAULT_PORT = 8080
POLL_INTERVAL = 5  # seconds
SERVICE_LABEL = "com.openavc.server"
DATA_DIR = Path("/Library/Application Support/OpenAVC")
SYSTEM_JSON = DATA_DIR / "system.json"
DAEMON_PLIST = "/Library/LaunchDaemons/com.openavc.server.plist"
# Uninstaller shipped inside the app bundle. The menu item runs it elevated so
# users don't have to find a Terminal command to remove the background service.
UNINSTALL_SCRIPT = "/Applications/OpenAVC.app/Contents/Resources/macos-uninstall.sh"
# Per-user marker: the IDE is auto-opened once, the first time the menu bar runs
# for this user (i.e. right after install). It lives in the user's home, not the
# root-owned system data dir, so the menu bar (running as the user) can create
# it. Removing it makes the next launch auto-open again (the uninstall does).
FIRST_RUN_MARKER = Path.home() / "Library" / "Application Support" / "OpenAVC" / ".ide-autoopened"


def _get_server_config() -> dict:
    """Read the port/TLS subset of system.json (falls back to defaults).

    Mirrors installer/tray.py: env overrides win, then system.json, then
    defaults. The menu bar must keep running even if system.json is missing or
    malformed.
    """
    result = {"http_port": DEFAULT_PORT, "tls_enabled": False, "tls_port": 8443}

    port_env = os.environ.get("OPENAVC_PORT")
    if port_env:
        try:
            result["http_port"] = int(port_env)
        except ValueError:
            pass
    tls_env = os.environ.get("OPENAVC_TLS_ENABLED", "").lower()
    if tls_env in ("true", "1", "yes"):
        result["tls_enabled"] = True
    elif tls_env in ("false", "0", "no"):
        result["tls_enabled"] = False

    if SYSTEM_JSON.exists():
        try:
            data = json.loads(SYSTEM_JSON.read_text(encoding="utf-8"))
            if "OPENAVC_PORT" not in os.environ:
                result["http_port"] = data.get("network", {}).get("http_port", DEFAULT_PORT)
            tls = data.get("tls", {}) if isinstance(data.get("tls"), dict) else {}
            if "OPENAVC_TLS_ENABLED" not in os.environ:
                result["tls_enabled"] = bool(tls.get("enabled", False))
            result["tls_port"] = int(tls.get("port", 8443))
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    return result


def _base_url(cfg: dict) -> str:
    if cfg.get("tls_enabled"):
        return f"https://localhost:{cfg['tls_port']}"
    return f"http://localhost:{cfg['http_port']}"


def _api_get(path: str, cfg: dict, timeout: float = 3.0) -> dict | None:
    """GET the local OpenAVC API. Returns parsed JSON or None.

    On TLS installs, hits the loopback TLS port with verification disabled
    (self-signed cert — same trust model as OpenAVC's other local callers).
    """
    if cfg.get("tls_enabled"):
        url = f"https://127.0.0.1:{cfg['tls_port']}{path}"
        ctx = ssl._create_unverified_context()
    else:
        url = f"http://127.0.0.1:{cfg['http_port']}{path}"
        ctx = None
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def _service_command(action: str) -> None:
    """Start/stop/restart the system LaunchDaemon, elevating via the macOS auth
    dialog (the daemon runs as root, so a plain launchctl call would be denied).
    """
    if action == "start":
        cmd = f"launchctl bootstrap system {DAEMON_PLIST}"
    elif action == "stop":
        cmd = f"launchctl bootout system/{SERVICE_LABEL}"
    else:  # restart
        cmd = f"launchctl kickstart -k system/{SERVICE_LABEL}"
    script = f'do shell script "{cmd}" with administrator privileges'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


class OpenAVCMenuBar(rumps.App):
    def __init__(self):
        super().__init__("OpenAVC", quit_button="Quit")
        self._cfg = _get_server_config()
        self._base = _base_url(self._cfg)
        self._status_item = rumps.MenuItem("Starting...")
        self._status_item.set_callback(None)  # non-clickable status line
        self.menu = [
            self._status_item,
            None,
            rumps.MenuItem("Open Programmer IDE", callback=self._open_programmer),
            rumps.MenuItem("Open Panel UI", callback=self._open_panel),
            rumps.MenuItem("Check for Updates", callback=self._check_updates),
            None,
            ("Service", [
                rumps.MenuItem("Start", callback=lambda _: _service_command("start")),
                rumps.MenuItem("Stop", callback=lambda _: _service_command("stop")),
                rumps.MenuItem("Restart", callback=lambda _: _service_command("restart")),
            ]),
            None,
            rumps.MenuItem("Uninstall OpenAVC...", callback=self._uninstall),
            None,
        ]
        rumps.Timer(self._poll, POLL_INTERVAL).start()

        # First launch after install: open the IDE once, in the user's default
        # browser. Done here (user context) rather than in the installer's root
        # postinstall, which would force Safari regardless of the default.
        if not FIRST_RUN_MARKER.exists():
            threading.Thread(target=self._first_run_open, daemon=True).start()

    def _first_run_open(self) -> None:
        """Wait for the server's socket to come up, then open the Programmer and
        write the first-run marker. Polls /api/startup-status (200 the instant
        uvicorn binds, even mid-boot) so the browser lands on the startup splash
        without waiting for full engine init."""
        for _ in range(120):  # up to ~2 min for the daemon to start
            if _api_get("/api/startup-status", self._cfg, timeout=1) is not None:
                break
            time.sleep(1)
        webbrowser.open(f"{self._base}/programmer")
        try:
            FIRST_RUN_MARKER.parent.mkdir(parents=True, exist_ok=True)
            FIRST_RUN_MARKER.write_text("")
        except OSError:
            pass

    def _poll(self, _timer) -> None:
        health = _api_get("/api/health", self._cfg)
        if health and health.get("status") == "healthy":
            version = health.get("version", "")
            self.title = "OpenAVC"
            devices = health.get("devices", {})
            total, connected = devices.get("total", 0), devices.get("connected", 0)
            label = f"v{version} - Running" if version else "Running"
            if total:
                label += f" - {connected}/{total} devices online"
            self._status_item.title = label
        else:
            self.title = "OpenAVC"
            self._status_item.title = "Stopped"

    def _open_programmer(self, _) -> None:
        webbrowser.open(f"{self._base}/programmer")

    def _open_panel(self, _) -> None:
        webbrowser.open(f"{self._base}/panel")

    def _check_updates(self, _) -> None:
        _api_get("/api/system/updates/check", self._cfg, timeout=15)
        webbrowser.open(f"{self._base}/programmer#/updates")

    def _uninstall(self, _) -> None:
        """Fully remove OpenAVC: stop the server LaunchDaemon and this menu-bar
        agent, delete the app + plists, and forget the install receipt. Projects
        and settings are kept. Elevates via the macOS auth dialog (the daemon and
        plists are root-owned)."""
        ok = rumps.alert(
            title="Uninstall OpenAVC?",
            message=(
                "This stops the OpenAVC background service and this menu-bar app, "
                "then removes the application. Your projects and settings are kept "
                "in /Library/Application Support/OpenAVC.\n\n"
                "You can reinstall any time to pick up where you left off."
            ),
            ok="Uninstall",
            cancel="Cancel",
        )
        if not ok:
            return
        # Run the bundled uninstaller synchronously and elevated — the same
        # `do shell script ... with administrator privileges` pattern the Start/
        # Stop/Restart items use. (An earlier `nohup ... &` attempt failed: the
        # elevated context has no controlling terminal, so nohup aborted with
        # "can't detach from console" and the script never ran.) The script does
        # all its removal before stopping this menu-bar agent as its last step,
        # so the work is complete even if that final step tears us down.
        script = f"do shell script \"bash '{UNINSTALL_SCRIPT}'\" with administrator privileges"
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Confirm it actually ran before quitting: a removed daemon plist means
        # success. This avoids quitting when the user cancelled the password
        # prompt, and still works if our process was torn down at the end.
        if not os.path.exists(DAEMON_PLIST):
            rumps.quit_application()
        else:
            rumps.alert(
                title="Uninstall did not complete",
                message=(
                    "OpenAVC was not removed (you may have cancelled the password "
                    "prompt). Try again, or run the uninstaller from Terminal:\n\n"
                    f"sudo bash {UNINSTALL_SCRIPT}"
                ),
            )


def main():
    OpenAVCMenuBar().run()


if __name__ == "__main__":
    main()
