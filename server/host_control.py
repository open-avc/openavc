"""Privileged host actions via a root-owned helper (C10).

The server runs unprivileged (the Pi/Linux ``openavc.service`` sets
``NoNewPrivileges=true``, which makes setuid bits — and therefore ``sudo`` —
ineffective for the server process and anything it forks). OS-level actions
that need root — syncing the ``openavc`` account password to the web admin
password, toggling SSH, rebooting — are performed by a root-owned systemd
``.path`` unit + oneshot service. This module is the unprivileged half: it
drops a request file in a spool directory and (optionally) waits for the root
helper's result.

Availability is gated on the helper being installed (``helper_available()``).
The Pi appliance image installs it (see ``installer/pi-image``); every other
target — generic Linux ``install.sh``, Docker, Windows, dev — does not, so all
of these calls are clean no-ops there (an admin manages their own OS account /
sshd on a general-purpose box). Design notes: ``openavc-auth-posture-plan.md``
(C10 section).

Request shapes written to ``{data_dir}/priv-requests/<id>.json``:
- ``{"action": "set_password"}`` — helper reads ``auth.programmer_password``
  from ``system.json`` and runs ``chpasswd`` for the ``openavc`` user (empty
  password re-locks the account). The password is never put in the request.
- ``{"action": "set_ssh", "enabled": bool, "want_result": true}``
- ``{"action": "reboot"}``

Only requests with ``"want_result": true`` get a ``{data_dir}/priv-results/
<id>.json`` written back, so fire-and-forget actions don't accumulate files.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

from server.system_config import get_system_config
from server.utils.spawn import CREATE_NO_WINDOW
from server.utils.logger import get_logger

log = get_logger(__name__)

# Capability marker: the Pi image installs this path unit. Its presence is the
# single gate for every OS-credential action — true only on the Pi appliance.
_PATH_UNIT = Path("/etc/systemd/system/openavc-privileged.path")

# How long an interactive caller (SSH toggle) waits for the root helper's
# result before giving up. The path unit fires near-instantly; this is slack.
_RESULT_TIMEOUT = 6.0
_RESULT_POLL = 0.1
# Results older than this are swept on the next interactive call (covers a
# caller that timed out and never collected its result).
_RESULT_STALE_SECONDS = 120.0


def helper_available() -> bool:
    """Whether the privileged helper is installed (Pi appliance only)."""
    return _PATH_UNIT.exists()


def _request_dir() -> Path:
    return get_system_config().data_dir / "priv-requests"


def _result_dir() -> Path:
    return get_system_config().data_dir / "priv-results"


def _write_request(action: str, payload: dict | None = None, *, want_result: bool = False) -> str | None:
    """Drop a request file for the root helper. Returns the request id, or None
    if the helper isn't available or the write failed (never raises)."""
    if not helper_available():
        return None
    req_dir = _request_dir()
    res_dir = _result_dir()
    body: dict = {"action": action}
    if payload:
        body.update(payload)
    if want_result:
        body["want_result"] = True
    req_id = secrets.token_hex(8)
    # Write to the (un-watched) result dir, then atomically rename into the
    # watched request dir so the path unit only ever sees a complete *.json.
    try:
        req_dir.mkdir(parents=True, exist_ok=True)
        res_dir.mkdir(parents=True, exist_ok=True)
        tmp = res_dir / f".req-{req_id}.tmp"
        tmp.write_text(json.dumps(body), encoding="utf-8")
        os.replace(tmp, req_dir / f"{req_id}.json")
    except OSError as e:
        log.warning("Could not submit privileged request %s: %s", action, e)
        return None
    return req_id


def _sweep_stale_results() -> None:
    """Delete result files a timed-out caller never collected."""
    try:
        now = time.time()
        for f in _result_dir().glob("*.json"):
            try:
                if now - f.stat().st_mtime > _RESULT_STALE_SECONDS:
                    f.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def sync_os_password() -> bool:
    """Sync the OS ``openavc`` account password to the web admin password.

    Fire-and-forget: the helper reads the password from ``system.json``. Safe to
    call on every claim / password change; a no-op when the helper is absent.
    Returns True if a request was submitted.
    """
    return _write_request("set_password") is not None


def request_reboot() -> bool:
    """Ask the root helper to reboot the host. Returns True if submitted."""
    return _write_request("reboot") is not None


async def set_ssh(enabled: bool) -> dict:
    """Enable or disable SSH and wait for the helper's result.

    Returns ``{"ok": bool, "error": str, "pending": bool}``. ``pending`` is True
    if the request was submitted but no result arrived before the timeout.
    """
    if not helper_available():
        return {"ok": False, "error": "not_supported", "pending": False}
    _sweep_stale_results()
    req_id = _write_request("set_ssh", {"enabled": bool(enabled)}, want_result=True)
    if req_id is None:
        return {"ok": False, "error": "submit_failed", "pending": False}

    result_path = _result_dir() / f"{req_id}.json"
    deadline = time.monotonic() + _RESULT_TIMEOUT
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {"ok": False, "error": "bad_result"}
            finally:
                result_path.unlink(missing_ok=True)
            return {"ok": bool(data.get("ok")), "error": data.get("error", ""), "pending": False}
        await asyncio.sleep(_RESULT_POLL)
    return {"ok": False, "error": "timeout", "pending": True}


def ssh_status() -> dict:
    """Report SSH availability and current state for the Settings toggle.

    ``supported`` is True only on a Pi appliance (helper installed). ``enabled``
    reflects whether sshd is running now; None if it couldn't be determined.
    """
    supported = helper_available()
    enabled: bool | None = None
    if supported:
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", "ssh"],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            enabled = proc.stdout.strip() == "active"
        except (OSError, subprocess.SubprocessError):
            enabled = None
    return {"supported": supported, "enabled": enabled}
