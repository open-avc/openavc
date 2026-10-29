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
  Reset Programmer Password...
  Service > Start / Stop / Restart
  Stop Server and Quit
  Quit Menu Bar App   (added by rumps)

The two quit items are separate on purpose. The server is a LaunchDaemon that
outlives this app, so on a Mac driving a real space, quitting a status icon
must not take the room offline — but "shut it all down" is a fair thing to
want on a desk, and it should not be buried a submenu deep. Reopening
OpenAVC.app restarts this agent, which is the way back to the icon.

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
# Where the server records, at every start, where it listens (openavc/main.py).
# The menu bar runs as the signed-in user and the server as root, so this is
# the one file in the data folder the menu bar can read: the server writes it
# readable by everyone, and system.json readable by root only.
SERVER_ADDRESS_FILE = DATA_DIR / "status" / "server.json"
DAEMON_PLIST = "/Library/LaunchDaemons/com.openavc.server.plist"
# A file by this name in the data folder makes the server clear the Programmer
# login at its next start (RESET_REQUEST_NAMES in openavc/api/auth.py). The
# folder is root's, so the menu item writes it through the same administrator
# prompt the Service items use, then restarts the daemon.
RESET_REQUEST_FILE = DATA_DIR / "reset-programmer-password"
# Uninstaller shipped inside the app bundle. The menu item runs it elevated so
# users don't have to find a Terminal command to remove the background service.
UNINSTALL_SCRIPT = "/Applications/OpenAVC.app/Contents/Resources/macos-uninstall.sh"
# Per-user marker: the IDE is auto-opened once, the first time the menu bar runs
# for this user (i.e. right after install). It lives in the user's home, not the
# root-owned system data dir, so the menu bar (running as the user) can create
# it. Removing it makes the next launch auto-open again (the uninstall does).
FIRST_RUN_MARKER = Path.home() / "Library" / "Application Support" / "OpenAVC" / ".ide-autoopened"


def _get_server_config() -> dict:
    """Read the ports the server listens on, as it recorded them at its last start.

    Mirrors installer/tray.py: env overrides win, then server.json, then
    defaults. The menu bar must keep running before the server has ever
    started, or if the file is malformed.
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
    tls_port_env = os.environ.get("OPENAVC_TLS_PORT")
    if tls_port_env:
        try:
            result["tls_port"] = int(tls_port_env)
        except ValueError:
            pass

    try:
        if SERVER_ADDRESS_FILE.exists():
            data = json.loads(SERVER_ADDRESS_FILE.read_text(encoding="utf-8"))
            if "OPENAVC_PORT" not in os.environ:
                result["http_port"] = int(data.get("http_port", DEFAULT_PORT))
            if "OPENAVC_TLS_ENABLED" not in os.environ:
                result["tls_enabled"] = bool(data.get("tls_enabled", False))
            if "OPENAVC_TLS_PORT" not in os.environ:
                result["tls_port"] = int(data.get("tls_port", 8443))
    except (json.JSONDecodeError, OSError, ValueError, TypeError, AttributeError):
        pass

    return result


def _get_listen_problem() -> str:
    """The server's own sentence when it is not accepting connections from the
    network it was given (the chosen adapter is unplugged or has no address),
    or "". Recorded at every start, so it changes when the server restarts on
    the adapter coming back.
    """
    try:
        data = json.loads(SERVER_ADDRESS_FILE.read_text(encoding="utf-8"))
        problem = data.get("listen", {}).get("problem")
    except (json.JSONDecodeError, OSError, AttributeError, TypeError, ValueError):
        return ""
    return problem if isinstance(problem, str) else ""


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


def _reset_command() -> str:
    """The shell line Reset Programmer Password runs as root: write the request
    file, then restart the daemon (or start it, when it is not loaded)."""
    return (
        f"touch '{RESET_REQUEST_FILE}' && "
        f"(launchctl kickstart -k system/{SERVICE_LABEL} || "
        f"launchctl bootstrap system {DAEMON_PLIST})"
    )


class OpenAVCMenuBar(rumps.App):
    def __init__(self):
        # "Quit Menu Bar App", not "Quit": this quits a status icon and leaves
        # the server running, and a button labelled Quit in an app called
        # OpenAVC reads as "quit OpenAVC" to everyone who has not read the
        # plists. Naming it for what it does is the whole fix.
        super().__init__("OpenAVC", quit_button="Quit Menu Bar App")
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
            rumps.MenuItem("Reset Programmer Password...", callback=self._reset_programmer_password),
            None,
            ("Service", [
                rumps.MenuItem("Start", callback=lambda _: _service_command("start")),
                rumps.MenuItem("Stop", callback=lambda _: _service_command("stop")),
                rumps.MenuItem("Restart", callback=lambda _: _service_command("restart")),
            ]),
            None,
            rumps.MenuItem("Stop Server and Quit", callback=self._stop_and_quit),
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
        # Read where the server listens at every poll, not once: a port
        # changed in Settings is recorded when the restart applies it.
        self._cfg = _get_server_config()
        self._base = _base_url(self._cfg)
        health = _api_get("/api/health", self._cfg)
        if health and health.get("status") == "healthy":
            version = health.get("version", "")
            self.title = "OpenAVC"
            devices = health.get("devices", {})
            total, connected = devices.get("total", 0), devices.get("connected", 0)
            label = f"v{version} - Running" if version else "Running"
            problem = _get_listen_problem()
            if problem:
                label += f" - {problem}"
            elif total:
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
        # '#updates', not '#/updates': the IDE matches the bare view id after
        # the '#', and anything else opens the Dashboard (as in tray.py).
        webbrowser.open(f"{self._base}/programmer#updates")

    def _stop_and_quit(self, _) -> None:
        """Stop the server LaunchDaemon, then quit — the "shut the whole thing
        down" action for a desk machine. Plain quit leaves the server running
        by design, so without this the only way all the way off is Service >
        Stop followed by a separate quit, and the person who reached for Quit
        first has no reason to think there is anything left to do."""
        _service_command("stop")
        # Confirm it actually stopped before quitting. Cancelling the password
        # prompt is silent, and quitting anyway would leave someone certain the
        # server was off while it kept serving. Poll the same health endpoint
        # the status line uses; a stop takes a moment to release the socket.
        for _ in range(10):
            if _api_get("/api/health", self._cfg, timeout=1) is None:
                rumps.quit_application()
                return
            time.sleep(0.5)
        rumps.alert(
            title="OpenAVC is still running",
            message=(
                "The server could not be stopped (you may have cancelled the "
                "password prompt). The menu bar app has been left open so you "
                "can try again from Service > Stop."
            ),
        )

    def _reset_programmer_password(self, _) -> None:
        ok = rumps.alert(
            title="Reset Programmer Password?",
            message=(
                "This clears the Programmer username, password and API key, and "
                "restarts OpenAVC. The next time you open the Programmer it asks "
                "you to choose a new username and password.\n\n"
                "Your project, devices, panels and cloud connection are not "
                "changed. Anything that uses the API key will need a new one."
            ),
            ok="Reset",
            cancel="Cancel",
        )
        if not ok:
            return
        script = f'do shell script "{_reset_command()}" with administrator privileges'
        try:
            done = subprocess.run(
                ["osascript", "-e", script], capture_output=True, timeout=60,
            ).returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            done = False
        if done:
            rumps.alert(
                title="OpenAVC is restarting",
                message=(
                    "In a moment, open the Programmer and choose a new username "
                    "and password."
                ),
            )
        else:
            rumps.alert(
                title="The password was not reset",
                message=(
                    "This needs your Mac's administrator password. Try again and "
                    "enter it when asked."
                ),
            )

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
