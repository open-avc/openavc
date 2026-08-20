"""Privileged host actions via a root-owned helper.

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
sshd on a general-purpose box).

Request shapes written to ``{data_dir}/priv-requests/<id>.json``:
- ``{"action": "set_password", "password": str}`` — the helper runs ``chpasswd``
  for the ``openavc`` user with the password carried in the request (an empty
  one re-locks the account).
- ``{"action": "set_ssh", "enabled": bool, "want_result": true}``
- ``{"action": "reboot"}``

Only requests with ``"want_result": true`` get a ``{data_dir}/priv-results/
<id>.json`` written back, so fire-and-forget actions don't accumulate files.

**The password travels in the request, and that is the point.** The helper used
to read ``auth.programmer_password`` out of ``system.json`` itself, which is
exactly why that value could not be hashed. Inverting the handoff — the server
passes the plaintext once, at the moment the user typed it — is what let the
stored form become an ``openavc.utils.password_hash`` digest. So the plaintext
now exists on disk for the moment between this write and the helper's ``rm``,
instead of permanently. Request files are written 0600 and stale ones are swept,
but the defence is the transience, not the mode: a plugin runs as the same user
and could read either.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

from openavc.system_config import get_system_config
from openavc.utils.spawn import CREATE_NO_WINDOW
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# Capability marker: the Pi image installs this path unit. Its presence is the
# single gate for every OS-credential action — true only on the Pi appliance.
_PATH_UNIT = Path("/etc/systemd/system/openavc-privileged.path")

# The helper script itself, and the marker that says it understands a
# password-carrying request. The helper ships in the Pi IMAGE, so an appliance
# flashed before this change keeps its old copy until an update refreshes it
# (installer/update-helper.sh does that now, from the same start that applies
# the update). An old copy would read the stored value out of system.json and
# set the OS account password to the literal digest — silently, as root. So the
# server asks the script what it speaks before handing it one.
_HELPER_SCRIPT = Path("/usr/local/sbin/openavc-privileged-helper.sh")
_PASSWORD_PROTOCOL_MARKER = "PROTOCOL: set_password-in-request"

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


def helper_takes_password() -> bool:
    """Whether the installed helper reads the password from the request file.

    False for a helper predating that protocol — see ``_HELPER_SCRIPT``. Also
    False if the script cannot be read at all, which is the safe answer: not
    syncing leaves the OS account on its previous password, where sending a
    request an old helper misreads would replace it with a digest.
    """
    try:
        return _PASSWORD_PROTOCOL_MARKER in _HELPER_SCRIPT.read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False


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
    # Clear both spools of anything the helper never collected before adding to
    # them. Requests matter more than results here: a set_password request the
    # helper never drained is a plaintext password sitting on disk, which is the
    # one thing this whole path exists to avoid.
    _sweep_stale(req_dir)
    _sweep_stale(res_dir)
    # Write to the (un-watched) result dir, then atomically rename into the
    # watched request dir so the path unit only ever sees a complete *.json.
    # Created 0600 explicitly rather than at the umask's mercy — set_password
    # carries the admin password, and on an appliance that is the OS login too.
    try:
        req_dir.mkdir(parents=True, exist_ok=True)
        res_dir.mkdir(parents=True, exist_ok=True)
        tmp = res_dir / f".req-{req_id}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(body).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, req_dir / f"{req_id}.json")
    except OSError as e:
        log.warning("Could not submit privileged request %s: %s", action, e)
        return None
    return req_id


def _sweep_stale(spool: Path) -> None:
    """Delete spool files nothing collected — a result a timed-out caller left,
    or a request the helper never drained because it isn't running."""
    try:
        now = time.time()
        for f in spool.glob("*.json"):
            try:
                if now - f.stat().st_mtime > _RESULT_STALE_SECONDS:
                    f.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def sync_os_password(password: str) -> bool:
    """Sync the OS ``openavc`` account password to the web admin password.

    Takes the plaintext, because this is the one moment the server legitimately
    holds it: the user just typed it. Nothing reads it back afterwards — what
    persists is a digest. An empty string re-locks the account.

    Fire-and-forget. A no-op when the helper is absent (every deployment except
    the Pi appliance) or too old to understand the request. Returns True if a
    request was submitted.
    """
    if not helper_available():
        return False
    if not helper_takes_password():
        log.warning(
            "Skipping OS account password sync: the privileged helper at %s "
            "predates the request-carried password. It refreshes on the next "
            "service start; change the password again after that to sync it.",
            _HELPER_SCRIPT,
        )
        return False
    return _write_request("set_password", {"password": password}) is not None


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
