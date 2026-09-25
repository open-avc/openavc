"""
Device connection resolution — what a device in the project actually dials.

A project device declares only part of its connection: the driver supplies
defaults, ``device.config`` holds protocol fields, and the project's
``connections`` table holds host/port/baudrate. On top of that, two bindings
rewrite the result entirely — a bridge-bound device travels through another
device's pass-through port, and a USB-serial device's volatile OS port is
derived from its stable adapter id.

Everything that hands a device config to the device manager goes through
``resolve_device_config`` (engine startup, project reconcile, the device
routes, the AI tools), so the layering lives here once rather than in the
orchestrator.

``device_connections`` turns that around for anything that asks "which
project devices would a connection to this address meet?": the Driver
Builder's test panel before it opens a competing session, and a device audit
before it sends anything. Both read the same resolved config the device
manager dials, so a device on its driver's default port, a transport
override, or a serial device reached through a bridge's pass-through port is
found where it really connects.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Iterable

from openavc.drivers.registry import (
    get_driver_bridge_ports,
    get_driver_default_config,
    get_driver_transport,
)
from openavc.transport.serial_transport import resolve_usb_binding
from openavc.utils.logger import get_logger

log = get_logger(__name__)


def resolve_device_config(device, project) -> dict:
    """Get device config dict with driver defaults and connection table merged in.

    Layering (later wins):
      1. ``driver.DRIVER_INFO["default_config"]`` — driver-declared
         defaults (e.g. control-protocol port). Ensures discovery /
         AI-tool add paths inherit the right defaults even when the
         caller only supplied ``host``.
      2. ``device.config`` — protocol fields saved in the project.
      3. ``project.connections[id]`` — connection-table overrides
         (host, port, baudrate, etc.) saved separately.
    """
    cfg = device.model_dump() if hasattr(device, "model_dump") else dict(device)
    defaults = get_driver_default_config(cfg.get("driver", ""))
    device_config = cfg.get("config", {})
    conn = project.connections.get(cfg["id"], {})
    merged = {**defaults, **device_config, **conn}
    # ir_codes overlays per code on top of the driver's shipped default set
    # rather than the shallow merge's whole-map replace: a device that
    # authors a single code (one IrCodesEditor save persists just that code)
    # must not wipe every code the driver ships.
    default_codes = defaults.get("ir_codes")
    device_codes = device_config.get("ir_codes")
    if isinstance(default_codes, dict) and isinstance(device_codes, dict):
        merged["ir_codes"] = {**default_codes, **device_codes}
    cfg["config"] = resolve_bridge_binding(merged, project)
    # Rewrite a USB-serial device's volatile port from its stable adapter id —
    # the local-serial analog of the bridge rewrite above. The logic lives in
    # the transport module so the device manager can re-resolve on every
    # reconnect attempt too: the path can change when the adapter is replugged
    # mid-run.
    driver_transport = get_driver_transport(cfg.get("driver", ""))
    cfg["config"] = resolve_usb_binding(cfg["config"], driver_transport)
    return cfg


def resolve_bridge_binding(config: dict, project) -> dict:
    """Rewrite a bridge-bound device's effective connection to its bridge's port.

    When a device's connection carries ``bridge`` (a bridge device id) +
    ``bridge_port`` (a port the bridge advertises), the device's bytes
    travel *through* that bridge rather than to a host of its own. For a
    serial pass-through port this is a pure config rewrite: point the
    downstream at the bridge's transparent TCP pass-through endpoint
    (``transport=tcp``, ``host=<bridge host>``, ``port=<passthrough_port>``)
    and reuse the existing TCP transport unchanged. The serial params
    (baudrate/parity/...) stay in the config so the bridge driver can push
    them to the hardware via ``prepare_bridge_port`` before bytes flow.

    Unresolvable bindings (unknown bridge, unknown port, missing host) are
    left untouched and logged — the device then fails to connect with a
    clear error rather than silently dialing the wrong place. IR / relay
    ports are not transport rewrites (commands route through the bridge at
    send time, Phase 2/3) and are left as-is for that path.
    """
    bridge_id = config.get("bridge")
    bridge_port_id = config.get("bridge_port")
    if not bridge_id or not bridge_port_id:
        return config

    bridge_dev = next(
        (d for d in project.devices if d.id == bridge_id), None
    )
    if bridge_dev is None:
        log.warning(
            "Bridge '%s' referenced by a device's connection is not in the "
            "project — leaving the binding unresolved", bridge_id,
        )
        return config

    port_def = get_driver_bridge_ports(bridge_dev.driver).get(bridge_port_id)
    if port_def is None:
        log.warning(
            "Bridge '%s' (driver '%s') does not advertise port '%s' — "
            "leaving the binding unresolved",
            bridge_id, bridge_dev.driver, bridge_port_id,
        )
        return config

    passthrough_port = port_def.get("passthrough_port")
    if port_def.get("kind") == "serial" and passthrough_port:
        # Resolve the bridge's own host the same layered way every device's
        # connection is (driver defaults < device.config < connections
        # table). Reading the connections table alone misses a host that
        # comes from a driver default or sits in the bridge's device.config
        # (e.g. an imported or template project) — which would leave the
        # binding unresolved and the downstream device wrongly offline.
        bridge_cfg = getattr(bridge_dev, "config", None) or {}
        bridge_conn = project.connections.get(bridge_id, {})
        bridge_host = {
            **get_driver_default_config(bridge_dev.driver),
            **bridge_cfg,
            **bridge_conn,
        }.get("host")
        if not bridge_host:
            log.warning(
                "Bridge '%s' has no host configured — leaving the serial "
                "binding for '%s' unresolved", bridge_id, bridge_port_id,
            )
            return config
        resolved = dict(config)
        resolved["transport"] = "tcp"
        resolved["host"] = bridge_host
        resolved["port"] = passthrough_port
        return resolved

    if port_def.get("kind") == "ir":
        # An IR device has no transport of its own: it emits through the
        # live bridge instance at send time (base.emit_via_bridge). Mark it
        # bridge-routed so connect() opens no socket; the bridge/bridge_port
        # markers stay in config for the router to resolve.
        resolved = dict(config)
        resolved["transport"] = "bridge"
        return resolved

    # Any other non-pass-through kind: no transport rewrite.
    return config


def is_bridge_config(cfg: dict[str, Any]) -> bool:
    """True if a resolved device config belongs to a bridge driver."""
    return bool(get_driver_bridge_ports(cfg.get("driver", "")))


def bridge_first(
    device_ids: list[str], resolved: dict[str, dict]
) -> list[list[str]]:
    """Split ``device_ids`` into ``[bridges, others]`` (each batch included
    only if non-empty), preserving order within each, so bridge devices are
    added and connected before the devices that route through them — a
    bridge-bound device's connect path needs its bridge live to prep the
    port (push serial baud/parity) first.
    """
    bridges: list[str] = []
    others: list[str] = []
    for did in device_ids:
        (bridges if is_bridge_config(resolved[did]) else others).append(did)
    return [batch for batch in (bridges, others) if batch]


@dataclass(frozen=True)
class DeviceConnection:
    """Where one project device connects, after every layer and rewrite.

    ``port`` is a number for a network transport and a path for serial.
    ``bridge`` is the bridge device's id when the device reaches ``host``
    through that bridge's pass-through port, empty otherwise.
    """

    device_id: str
    name: str
    driver: str
    transport: str
    host: str
    port: int | str | None
    bridge: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.name,
            "driver": self.driver,
            "transport": self.transport,
            "host": self.host,
            "port": self.port,
            "bridge": self.bridge,
        }


def device_connections(project) -> list[DeviceConnection]:
    """The effective connection of every enabled device in ``project``.

    A disabled device has no driver running, so it holds no connection and
    is left out. A device whose config cannot be resolved is left out too
    (the reconcile reports it; this answers a question about live devices).
    """
    out: list[DeviceConnection] = []
    for device in getattr(project, "devices", None) or []:
        if not getattr(device, "enabled", True):
            continue
        try:
            resolved = resolve_device_config(device, project)
        except Exception:  # an unresolvable device is not a live connection
            log.debug("Could not resolve %s's connection", getattr(device, "id", "?"),
                      exc_info=True)
            continue
        cfg = resolved.get("config") or {}
        driver = str(resolved.get("driver") or "")
        transport = str(
            cfg.get("transport") or get_driver_transport(driver) or "tcp"
        ).lower()
        port = cfg.get("port")
        if transport != "serial":
            try:
                port = int(port) if port is not None and port != "" else None
            except (TypeError, ValueError):
                port = None
        elif port is not None:
            port = str(port)
        # resolve_bridge_binding turns a serial device bound to a bridge's
        # pass-through port into a TCP connection to the bridge's host.
        bridged = bool(cfg.get("bridge") and cfg.get("bridge_port")) and transport == "tcp"
        out.append(DeviceConnection(
            device_id=str(resolved.get("id") or device.id),
            name=str(resolved.get("name") or device.id),
            driver=driver,
            transport=transport,
            host=str(cfg.get("host") or ""),
            port=port,
            bridge=str(cfg["bridge"]) if bridged else "",
        ))
    return out


def _same_host(a: str, b: str) -> bool:
    return a.strip().rstrip(".").lower() == b.strip().rstrip(".").lower()


def same_serial_port(a: str, b: str) -> bool:
    """Whether two serial port names are the same port on this host.

    Windows names its ports case-insensitively (``COM3`` is ``com3``); a POSIX
    device path is case-sensitive.
    """
    a, b = a.strip(), b.strip()
    if sys.platform == "win32":
        return a.lower() == b.lower()
    return a == b


def devices_at_host(project, names: Iterable[str]) -> list[DeviceConnection]:
    """Every enabled device whose connection goes to one of ``names``.

    ``names`` are the ways the caller refers to one host (what was typed and
    the address it resolved to, say); a device matches when its effective
    host is any of them. Serial devices have no host and never match.
    """
    wanted = [n for n in names if n and n.strip()]
    return [
        conn for conn in device_connections(project)
        if conn.transport != "serial" and conn.host
        and any(_same_host(conn.host, n) for n in wanted)
    ]
