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
without touching anything an administrator added by hand. They are found by
reading the firewall's own rule store, never by reading ``netsh``'s listing:
netsh prints its labels in the language Windows is installed in, so a listing
read for "Rule Name:" finds nothing on a German or French install, and every
sync then adds the rules again and closes none.
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


#: Where Windows Firewall keeps its local rules: one string value per rule,
#: shaped ``v2.30|Action=Allow|Dir=In|Protocol=17|LPort=8189|Name=...|``. The
#: field names are a fixed grammar (published in MS-GPFAS) and do not change
#: with the language Windows is installed in.
_RULES_KEY = (
    r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters"
    r"\FirewallPolicy\FirewallRules"
)


def _decode(raw: bytes | None) -> str:
    """netsh writes the OEM code page, not the ANSI one Python reads text in.

    Read as ANSI, a German install's ``ü`` is a byte cp1252 has no character
    for, and the ``UnicodeDecodeError`` escaped the sync. What is decoded here
    only ever reaches a log line, so a byte that means nothing is replaced.
    """
    if not raw:
        return ""
    try:
        return raw.decode("oem", errors="replace")
    except LookupError:  # the "oem" codec exists only on Windows
        return raw.decode(errors="replace")


def _netsh(args: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["netsh", *args],
            capture_output=True, timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (_decode(proc.stderr) or _decode(proc.stdout)).strip()
    return True, _decode(proc.stdout).strip()


def _rule_string_name(value: str) -> str | None:
    """The ``Name=`` field of one stored rule string, or None if it has none."""
    for field in value.split("|"):
        if field.startswith("Name="):
            return field[len("Name="):]
    return None


def _existing_rule_names() -> set[str]:
    """The name of every rule in the local firewall store.

    Empty when the store cannot be read. ``sync`` then tries to add what is
    wanted, which is where a failed listing always left it.
    """
    try:
        import winreg
    except ImportError:
        return set()
    names: set[str] = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _RULES_KEY) as key:
            index = 0
            while True:
                try:
                    _, value, kind = winreg.EnumValue(key, index)
                except OSError:  # past the last value
                    break
                index += 1
                if kind != winreg.REG_SZ or not isinstance(value, str):
                    continue
                name = _rule_string_name(value)
                if name is not None:
                    names.add(name)
    except OSError:
        return set()
    return names


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

    # What we opened before. Found by name so an administrator's own rules for
    # the same port are never mistaken for ours. A rule somebody disabled still
    # counts: it is there, and adding a second one would not enable the first.
    existing = set()
    for name in _existing_rule_names():
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
