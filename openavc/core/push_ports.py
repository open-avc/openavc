"""THE record of the ports a project's devices push to, for the host firewall.

Some drivers have the device send its changes to a port OpenAVC listens on,
rather than to the main HTTP port: ``push: tcp_listener`` opens a TCP port the
device dials back to (a camera reporting its position), ``push: multicast``
joins a UDP group the device sends to (a mixer's level meters), and
``push: udp_listener`` opens a UDP port the device sends packets to (an SNMP
agent's traps). On Linux,
``installer/firewall-sync.sh`` opens the instance's own listeners and the ports
plugins declare (``core/plugin_ports.py``) in ufw or firewalld, and nothing
else; with a host firewall on, these pushes were dropped. Polling still worked,
slower, so nothing looked broken except a feature that arrives only by push.

So the server writes the ports here, beside ``plugin_ports.json``, and the
helper opens them the same way, closing only what it opened itself. Each port
comes from the project, never from the listeners open right now: a listener
exists only while its device is connected, and every device disconnects when
the server stops, so a record of live listeners would tell the next boot to
close every one of them. An enabled device whose driver declares a push port
needs it open whether or not it is connected at the moment.

The port is the one the listener binds: the push block's ``port`` resolved
from the device's resolved config by ``drivers.base.push_port``, the same
function the listener uses. A ``tcp_listener`` on port 0 (the operating system
picks one each time) cannot be opened ahead of time and is left out, and so is
a multicast block whose group does not resolve to a multicast address (no
listener opens for it) and a port the host owns (``plugin_ports.HOST_PORTS``:
SSH, Telnet, SMB, RDP and the like, which a mistyped field must not open). HTTP push (``http_listener``, ``sse``) reaches the main
HTTP port, which the helper already opens.

Like a plugin port, a new one opens at the next service start: the helper runs
as root before the server, reading what the last run wrote. Windows needs none
of this: the installer's firewall rule covers the server program, whatever port
it listens on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from openavc.core.plugin_ports import HOST_PORTS
from openavc.drivers.registry import get_driver_class
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: The file the Linux firewall helper reads, beside plugin_ports.json.
PORTS_FILENAME = "push_ports.json"


def push_listen_port(driver_info: dict[str, Any], config: dict[str, Any]) -> tuple[int, str] | None:
    """``(port, protocol)`` a device on this driver listens on for pushes, or None."""
    from openavc.drivers.base import push_port

    push_def = driver_info.get("push")
    if not isinstance(push_def, dict):
        return None
    ptype = push_def.get("type")
    port = push_port(push_def, config)
    if port is None or not (0 < port < 65536) or port in HOST_PORTS:
        return None
    if ptype == "tcp_listener":
        return port, "tcp"
    if ptype == "udp_listener":
        return port, "udp"
    if ptype == "multicast":
        from openavc.drivers.base import resolve_push_value
        from openavc.transport.multicast_listener import is_multicast_group

        group = str(resolve_push_value(push_def.get("group", ""), config) or "")
        return (port, "udp") if is_multicast_group(group) else None
    return None


def collect(devices: Iterable[Any], resolve: Callable[[Any], dict[str, Any]]) -> list[dict]:
    """Every push port the enabled ``devices`` listen on, sorted, one entry a port.

    ``resolve`` turns a project device into its resolved record (what
    ``Engine.resolved_device_config`` returns). A device whose driver is not
    installed, or whose config cannot be resolved, has no port to record.
    """
    owners: dict[tuple[int, str], list[tuple[str, str]]] = {}
    for device in devices:
        if not getattr(device, "enabled", True):
            continue
        cls = get_driver_class(getattr(device, "driver", ""))
        if cls is None:
            continue
        try:
            resolved = resolve(device)
        except Exception:  # an unresolvable device opens no listener either
            log.debug("Could not resolve %s's push port", getattr(device, "id", "?"),
                      exc_info=True)
            continue
        found = push_listen_port(cls.DRIVER_INFO, resolved.get("config") or {})
        if found is not None:
            owners.setdefault(found, []).append(
                (str(device.id), str(getattr(device, "name", "") or device.id)),
            )
    return [
        {
            "port": port,
            "protocol": proto,
            "devices": sorted(did for did, _name in owners[(port, proto)]),
            "reason": "Pushes from " + ", ".join(sorted(name for _did, name in owners[(port, proto)])),
        }
        for port, proto in sorted(owners)
    ]


def write(data_dir: Path, entries: list[dict]) -> Path | None:
    """Persist the set beside system.json. Returns the path, or None.

    Written even when empty: an empty list is how the helper learns to close
    a port no device pushes to any more.
    """
    path = Path(data_dir) / PORTS_FILENAME
    payload = {
        "comment": (
            "Ports this project's devices send updates to. Written by OpenAVC; "
            "read by the host firewall helper. Editing this file does not "
            "change what a device listens on."
        ),
        "ports": entries,
    }
    try:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path
    except OSError as exc:
        # Never fatal: the device still connects and polls.
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


def record(data_dir: Path, entries: list[dict]) -> None:
    """Write ``entries`` when they differ from what is there, and say what is new.

    A port the last run did not record is open only after the next service
    start on a Linux install with a host firewall on, so the log says so then,
    rather than leaving a person to wonder why the pushes have not arrived.
    In Docker the helper never runs (the container does not manage the host's
    firewall), so the log says what to do on the host instead; an install
    with neither (Windows, macOS, a source checkout) says nothing.
    """
    path = Path(data_dir) / PORTS_FILENAME
    previous = read(data_dir)
    if path.exists() and previous == entries:
        return
    if write(data_dir, entries) is None:
        return
    before = {(e.get("port"), e.get("protocol")) for e in previous if isinstance(e, dict)}
    new = [e for e in entries if (e["port"], e["protocol"]) not in before]
    if not new:
        return
    note = _firewall_note(new)
    if note:
        log.info(
            "Devices send updates to "
            + ("port " if len(new) == 1 else "ports ")
            + ", ".join(f"{e['port']}/{e['protocol']}" for e in new)
            + ". "
            + note
        )


def _deployment_type() -> str:
    from openavc.updater.platform import detect_deployment_type

    try:
        return detect_deployment_type().value
    except Exception:  # never fatal: the record is written either way
        return "unknown"


def _firewall_note(new: list[dict]) -> str | None:
    """What a person has to know about the host firewall for the new ports,
    on this install, or None where nothing applies."""
    it = "it" if len(new) == 1 else "them"
    first = f"{new[0]['port']}:{new[0]['port']}/{new[0]['protocol']}"
    kind = _deployment_type()
    if kind == "linux_package":
        return (
            "If this computer has a host firewall on (ufw or firewalld), "
            f"OpenAVC opens {it} the next time its service starts."
        )
    if kind == "docker":
        return (
            f"In Docker, open {it} in the host's firewall yourself, and with "
            f"bridge networking publish {it} with the protocol, for example "
            f'"{first}".'
        )
    return None
