"""Opening a plugin-declared port on Windows, where nothing else will.

Linux has a helper that runs as root before the service starts
(``installer/firewall-sync.sh``); it reads the file
:mod:`openavc.core.plugin_ports` writes and needs nothing from this module.

Windows has no equivalent, and its installer rule cannot cover this case: it is
scoped to ``openavc-server.exe`` by program, and a bundled sidecar is a
different executable. So the server opens the port itself when it can.

**When it can** is the whole subtlety. Installed as a service the process runs
as SYSTEM and ``netsh`` succeeds. Run from a checkout it is an ordinary user and
``netsh`` refuses -- which is fine and expected, and must not look like a fault.
In that case we say exactly what is missing and the one command that fixes it,
because the alternative is a wall panel showing a black rectangle and nobody
able to connect that to a firewall.

Rules are named with a stable prefix so they can be found, replaced and removed
without touching anything an administrator added by hand.
"""

from __future__ import annotations

import subprocess
import sys

from openavc.utils.logger import get_logger
from openavc.utils.spawn import CREATE_NO_WINDOW

log = get_logger(__name__)

#: Every rule this module creates starts with this. Anything else in the
#: firewall is somebody else's and is never touched.
RULE_PREFIX = "OpenAVC plugin"


def rule_name(port: int, protocol: str) -> str:
    return f"{RULE_PREFIX} {protocol.upper()} {port}"


def _netsh(args: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["netsh", *args],
            capture_output=True, text=True, timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()
    return True, (proc.stdout or "").strip()


def manual_command(port: int, protocol: str) -> str:
    """The exact line an administrator can paste. Handed to them on failure."""
    return (
        f'netsh advfirewall firewall add rule name="{rule_name(port, protocol)}" '
        f"dir=in action=allow protocol={protocol.upper()} localport={port}"
    )


def sync(entries: list[dict]) -> dict:
    """Make the OpenAVC-plugin rules match ``entries``. Windows only.

    Returns a small report rather than raising: a firewall that could not be
    updated is a degraded system, not a broken one, and the caller decides how
    loudly to say so.
    """
    report = {"platform": sys.platform, "opened": [], "closed": [], "refused": []}
    if not sys.platform.startswith("win"):
        return report

    wanted = {(int(e["port"]), str(e.get("protocol", "tcp")).lower()) for e in entries}

    # What we opened before. Listed by name so an administrator's own rules for
    # the same port are never mistaken for ours.
    ok, out = _netsh(["advfirewall", "firewall", "show", "rule", "name=all"])
    existing = set()
    if ok:
        for line in out.splitlines():
            if not line.lower().startswith("rule name:"):
                continue
            name = line.split(":", 1)[1].strip()
            if not name.startswith(RULE_PREFIX + " "):
                continue
            parts = name.split()
            try:
                existing.add((int(parts[-1]), parts[-2].lower()))
            except (ValueError, IndexError):
                continue

    for port, proto in sorted(wanted - existing):
        added, err = _netsh([
            "advfirewall", "firewall", "add", "rule",
            f"name={rule_name(port, proto)}", "dir=in", "action=allow",
            f"protocol={proto.upper()}", f"localport={port}",
        ])
        if added:
            report["opened"].append(f"{port}/{proto}")
            log.info(f"Opened {port}/{proto} in Windows Firewall for a plugin")
        else:
            report["refused"].append(f"{port}/{proto}")
            log.warning(
                f"Could not open {port}/{proto} in Windows Firewall ({err or 'access denied'}). "
                f"A plugin needs it and panels on the network will not receive its "
                f"traffic until it is open. Run this as an administrator:\n  "
                f"{manual_command(port, proto)}"
            )

    for port, proto in sorted(existing - wanted):
        removed, _ = _netsh([
            "advfirewall", "firewall", "delete", "rule", f"name={rule_name(port, proto)}",
        ])
        if removed:
            report["closed"].append(f"{port}/{proto}")
            log.info(f"Closed {port}/{proto} in Windows Firewall (no plugin asks for it now)")

    return report
