"""THE rule for a port a plugin needs open in the host firewall.

A plugin that bundles a sidecar listens on ports the platform knows nothing
about. The Video Panel's MediaMTX binds **UDP 8189** for WebRTC media, and
neither installer covered it: Windows scopes its firewall rule to
``openavc-server.exe`` and the port belongs to ``mediamtx.exe``, while the Linux
helper only ever opened TCP. Every test of that feature ran loopback to
loopback, where no firewall is consulted, so nothing caught it -- the failure
lands on a real wall panel, and on Windows it lands silently, because a service
has no session for the firewall to prompt in.

So a plugin **declares** what it needs, in ``PLUGIN_INFO``:

    "network_ports": [
        {"port": 8189, "protocol": "udp", "reason": "WebRTC video to panels"},
    ]

Declared rather than requested at runtime, for a reason that is not style: the
Linux helper runs as ``ExecStartPre``, *before* the server exists, so it cannot
ask a running plugin anything. It reads what the last run left behind. That also
means a newly enabled plugin's port opens on the next service start on Linux,
which the caller is told rather than left to discover.

``reason`` is required and is not decoration -- it is what an administrator reads
in ``plugin_ports.json`` or a firewall rule comment when they find a port open
and want to know who asked for it. A port with no stated reason is one nobody
can safely close later.

This module decides WHAT is declared and writes it down. Opening the port is the
platform's job and differs per OS: Linux's ``installer/firewall-sync.sh`` reads
the file this writes, and Windows is handled by :mod:`openavc.system.firewall`.
"""

from __future__ import annotations

import json
from pathlib import Path

from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: The file the Linux firewall helper reads. Beside system.json in the data
#: directory, because that is the pair of files describing "what this instance
#: listens on" and the helper already reads the other one.
PORTS_FILENAME = "plugin_ports.json"

#: Only these. A plugin does not get to ask for a raw socket family.
VALID_PROTOCOLS = ("tcp", "udp")

#: Ports the platform itself owns or that no plugin has any business asking the
#: firewall to open. Refused at validation so a typo cannot open SSH.
_REFUSED_PORTS = frozenset({22, 23, 25, 135, 137, 138, 139, 445, 3389, 5900})


def validate_declaration(entries) -> tuple[bool, str]:
    """Check a ``network_ports`` declaration, returning (valid, error).

    Strict, and refuses rather than dropping a bad entry: a plugin that asked
    for a port and silently did not get one would fail in the field, on a
    network we cannot see, with the symptom appearing somewhere else entirely.
    """
    if not isinstance(entries, (list, tuple)):
        return False, "network_ports must be a list"
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return False, f"network_ports entry must be a dict, got {type(entry).__name__}"
        port = entry.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
            return False, f"network_ports entry has an invalid port: {port!r}"
        if port in _REFUSED_PORTS:
            return False, (
                f"network_ports may not request port {port} -- it belongs to the "
                "host, not to a plugin"
            )
        proto = entry.get("protocol", "tcp")
        if proto not in VALID_PROTOCOLS:
            return False, (
                f"network_ports entry for port {port} has protocol {proto!r}; "
                f"must be one of {', '.join(VALID_PROTOCOLS)}"
            )
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return False, (
                f"network_ports entry for port {port}/{proto} needs a `reason` -- "
                "it is what an administrator reads when deciding whether the rule "
                "may be removed"
            )
        key = (port, proto)
        if key in seen:
            return False, f"network_ports declares {port}/{proto} twice"
        seen.add(key)
    return True, ""


def declared_ports(plugin_class) -> list[dict]:
    """The validated declaration on a plugin class, or [] when it makes none."""
    info = getattr(plugin_class, "PLUGIN_INFO", None) or {}
    entries = info.get("network_ports") or []
    ok, _ = validate_declaration(entries)
    if not ok:
        return []
    return [
        {
            "port": e["port"],
            "protocol": e.get("protocol", "tcp"),
            "reason": e["reason"].strip(),
        }
        for e in entries
    ]


def collect(running: dict) -> list[dict]:
    """Merge the declarations of every running plugin, sorted and de-duplicated.

    ``running`` maps plugin id -> plugin class (or instance). Two plugins asking
    for the same port is not an error -- they may genuinely share one, or one may
    have replaced the other -- so the reasons are joined and the port opened once.
    """
    merged: dict[tuple[int, str], set[str]] = {}
    owners: dict[tuple[int, str], set[str]] = {}
    for plugin_id, obj in sorted(running.items()):
        cls = obj if isinstance(obj, type) else type(obj)
        for entry in declared_ports(cls):
            key = (entry["port"], entry["protocol"])
            merged.setdefault(key, set()).add(entry["reason"])
            owners.setdefault(key, set()).add(plugin_id)
    return [
        {
            "port": port,
            "protocol": proto,
            "plugins": sorted(owners[(port, proto)]),
            "reason": " / ".join(sorted(merged[(port, proto)])),
        }
        for port, proto in sorted(merged)
    ]


def write(data_dir: Path, entries: list[dict]) -> Path | None:
    """Persist the declared set beside system.json. Returns the path, or None.

    Written even when empty, and that is the point: an empty list is how the
    helper learns to CLOSE a port whose plugin has been disabled. Skipping the
    write on empty would leave the last non-empty list standing forever.
    """
    path = Path(data_dir) / PORTS_FILENAME
    payload = {
        "comment": (
            "Ports declared by installed plugins. Written by OpenAVC; read by "
            "the host firewall helper. Editing this file does not change what a "
            "plugin listens on."
        ),
        "ports": entries,
    }
    try:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path
    except OSError as exc:
        # Never fatal. A plugin that cannot get its port opened still runs, and
        # on the LAN it may well work anyway -- the failure is a firewall that
        # was not updated, not a broken plugin.
        log.warning(f"Could not write {path}: {exc}")
        return None


def read(data_dir: Path) -> list[dict]:
    """What was last written, or [] if there is nothing readable there."""
    path = Path(data_dir) / PORTS_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ports = payload.get("ports") if isinstance(payload, dict) else None
    return ports if isinstance(ports, list) else []
