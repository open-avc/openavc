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

``connection_of`` is THE answer to where one device connects, read from a
resolved record. Handed the record as it is set to (what
``resolve_device_config`` returns), it names the equipment's own address:
what a device's info reports and the AI's project summary lists. Handed the
record as its driver dials it now (``dialled_record``: the record the device
manager was handed, with the simulation redirect on that same driver laid
over it), it names where the device really connects, the simulator on this
computer while a simulation points it there (``DeviceConnection.simulated``).
The device manager asks ``dialled_record`` for its running devices
(``DeviceManager.dialled_record``, ``connections_now``), and the connection
test (``core/device_reachability``) dials that.

``device_connections`` turns that around for anything that asks "which
project devices would a connection to this address meet?". Handed the device
manager, it answers each running device where it connects now and a project
device nothing runs yet as it is set to, so the Driver Builder's test panel
finds the session or port a driver holds, and never one a simulated device
let go. Without it every device is answered as it is set to: a device audit
asks that way, since it pauses every device set to the equipment, a
simulated one too, which stopping the simulation sends back there. Either
way a device on its driver's default port, a transport override, or a serial
device reached through a bridge's pass-through port is found where it
connects.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from openavc.core.typed_fields import trim_typed_fields
from openavc.drivers.registry import (
    get_driver_bridge_ports,
    get_device_transport,
    get_driver_default_config,
    get_driver_transport,
)
from openavc.transport.endpoint import implied_port
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

    The merge then loses any space typed around an address, a port or a
    login (``core.typed_fields``), before the bridge and USB rewrites.
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
    # A space typed around an address or a login comes off before anything
    # dials it, so a project saved with one connects without an edit.
    merged = trim_typed_fields(cfg.get("driver", ""), merged)
    cfg["config"] = resolve_bridge_binding(merged, project)
    # Rewrite a USB-serial device's volatile port from its stable adapter id,
    # the local-serial analog of the bridge rewrite above. An adapter that is
    # not plugged in leaves the stored port in the record; the device manager
    # asks the same rule (require_usb_adapter) at every dial, which refuses it.
    driver_transport = get_driver_transport(cfg.get("driver", ""))
    cfg["config"] = resolve_usb_binding(cfg["config"], driver_transport)
    return cfg


def split_connection_fields(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``config`` as the project stores it: (protocol config, connection fields).

    THE rule for where a device setting lives. Connection fields
    (``CONNECTION_FIELDS`` in ``drivers/spec.py``: host, port, credentials,
    serial line, bridge, USB id) go in ``project.connections[device_id]``,
    everything else in the device's ``config``. Each half keeps the order the
    keys came in, and a ``None`` stays in its half: whether it removes a field
    is the caller's rule.
    """
    from openavc.drivers.spec import CONNECTION_FIELDS

    protocol: dict[str, Any] = {}
    connection: dict[str, Any] = {}
    for key, value in config.items():
        (connection if key in CONNECTION_FIELDS else protocol)[key] = value
    return protocol, connection


def apply_config_delta(project, device_id: str, delta: dict[str, Any]) -> bool:
    """Write a config delta for one device into ``project`` in place.

    THE one place a delta coming from the runtime (a setup action, a value the
    device reported) is split the way the project stores it: connection fields
    (host, port, credentials, serial line, bridge, USB id) into
    ``project.connections[device_id]``, everything else into the device's
    protocol ``config``. Returns False when the device is not in the project.

    The caller decides how the project is then persisted (a reconcile through
    ``Engine.apply_project``, or a bookkeeping persist), and is responsible for
    moving the live driver too (``DeviceManager.merge_live_config``) so the next
    reconcile does not read the change as an edit.
    """
    dev = next((d for d in project.devices if d.id == device_id), None)
    if dev is None:
        return False
    protocol_delta, connection_delta = split_connection_fields(delta)
    conn = {**project.connections.get(device_id, {}), **connection_delta}
    protocol = {**dev.config, **protocol_delta}
    if conn:
        project.connections[device_id] = conn
    else:
        project.connections.pop(device_id, None)
    dev.config = protocol
    return True


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
        bridge_host = trim_typed_fields(bridge_dev.driver, {
            **get_driver_default_config(bridge_dev.driver),
            **bridge_cfg,
            **bridge_conn,
        }).get("host")
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


# The mark dialled_record leaves on a record the simulation points at the
# simulator; connection_of reads it into DeviceConnection.simulated.
_SIMULATED = "simulated"


@dataclass(frozen=True)
class DeviceConnection:
    """Where one project device connects, after every layer and rewrite.

    ``port`` is a number for a network transport and a path for serial; a
    transport that falls back to a port of its own (http, ssh, mqtt, snmp)
    reports that port when the device names none. ``bridge`` is the bridge
    device's id when the device reaches ``host`` through that bridge's
    pass-through port, or (transport ``bridge``) emits through that bridge
    and holds no connection of its own; empty otherwise. ``simulated`` is
    True when the record was the one its driver dials now and a running
    simulation points it at the simulator on this computer: the transport,
    host and port are then the simulator's, not the equipment's.
    """

    device_id: str
    name: str
    driver: str
    transport: str
    host: str
    port: int | str | None
    bridge: str = ""
    simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.name,
            "driver": self.driver,
            **self.address(),
        }

    def address(self) -> dict[str, Any]:
        """Just where it connects: what a device's info and the connection
        test report back."""
        return {
            "transport": self.transport,
            "host": self.host,
            "port": self.port,
            "bridge": self.bridge,
        }


def connection_of(resolved: dict[str, Any]) -> DeviceConnection:
    """Where a device connects, from its resolved record.

    ``resolved`` is what :func:`resolve_device_config` returns, which is also
    what the device manager holds for every device it was handed: the device
    as it is set to. Handed :func:`dialled_record`'s answer instead, it is
    where the device connects now. The transport is the one
    ``BaseDriver.connect`` opens (``transport_choice``: the device's own
    choice, else its driver's, else tcp).
    """
    cfg = resolved.get("config") or {}
    driver = str(resolved.get("driver") or "")
    transport = get_device_transport(driver, cfg)
    port = cfg.get("port")
    if transport == "serial":
        port = str(port) if port is not None else None
    elif port is None or port == "":
        port = implied_port(transport, cfg)
    else:
        # A port that is not a number is reported as no port, never replaced
        # by the transport's fallback: the driver would not dial that either.
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = None
    # resolve_bridge_binding turns a serial device bound to a bridge's
    # pass-through port into a TCP connection to the bridge's host, and an IR
    # device bound to an emitter port into transport "bridge".
    bridged = (
        bool(cfg.get("bridge") and cfg.get("bridge_port"))
        and transport in ("tcp", "bridge")
    )
    device_id = str(resolved.get("id") or "")
    return DeviceConnection(
        device_id=device_id,
        name=str(resolved.get("name") or device_id),
        driver=driver,
        transport=transport,
        host=str(cfg.get("host") or ""),
        port=port,
        bridge=str(cfg["bridge"]) if bridged else "",
        simulated=bool(resolved.get(_SIMULATED)),
    )


def dialled_record(
    record: dict[str, Any], redirect: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The record as its driver dials it now.

    ``record`` is the one the device manager was handed for the driver
    running; ``redirect`` is the simulation redirect on that same driver
    (``SimulationManager.redirect_of``), or None. Pairing the two is the
    caller's part: a redirect laid over a record of another generation (a
    project edit not applied yet) would name a connection nothing opened.

    With a redirect the device connects to the simulator on this computer:
    the redirect's settings replace the record's connection, a bridge binding
    no longer applies, and the record is marked simulated. An IR device,
    which sends through its bridge and dials nothing, is left as it is set to.
    Without one, a USB-serial device's port is the one its adapter has now
    (``resolve_usb_binding``), as ``resolve_device_config`` reads it on every
    ask.
    """
    cfg = dict(record.get("config") or {})
    driver = str(record.get("driver") or "")
    if redirect and get_device_transport(driver, cfg) != "bridge":
        cfg.update(redirect)
        cfg.pop("bridge", None)
        cfg.pop("bridge_port", None)
        return {**record, "config": cfg, _SIMULATED: True}
    return {**record, "config": resolve_usb_binding(cfg, get_driver_transport(driver))}


class RunningDevices(Protocol):
    """What :func:`device_connections` asks of the device manager."""

    def connections_now(self) -> list[DeviceConnection]: ...


def device_connections(
    project, devices: RunningDevices | None = None,
) -> list[DeviceConnection]:
    """The effective connection of every enabled device in ``project``.

    ``devices`` is the engine's device manager. Each device it runs is
    answered where it connects now (``connections_now``: its record and the
    simulation redirect on its driver), so a simulated device holds its
    simulator's port and not its own, and a project edit not applied yet
    does not hide the port the running driver holds; a running device the
    project no longer has, or has disabled, still holds it and is answered
    too. A project device nothing runs (its driver not installed, or not yet
    applied) is answered as it is set to. Without ``devices`` every device is
    answered as it is set to in ``project``.

    A disabled device has no driver running, so it holds no connection and
    is left out. A device whose config cannot be resolved is left out too
    (the reconcile reports it; this answers a question about live devices).
    """
    running = (
        {conn.device_id: conn for conn in devices.connections_now()}
        if devices is not None else {}
    )
    out: list[DeviceConnection] = []
    for device in getattr(project, "devices", None) or []:
        now = running.pop(getattr(device, "id", None), None)
        if now is not None:
            out.append(now)
            continue
        if not getattr(device, "enabled", True):
            continue
        try:
            resolved = resolve_device_config(device, project)
        except Exception:  # an unresolvable device is not a live connection
            log.debug("Could not resolve %s's connection", getattr(device, "id", "?"),
                      exc_info=True)
            continue
        out.append(connection_of(resolved))
    out.extend(running.values())
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


def devices_at_host(
    project, names: Iterable[str], devices: RunningDevices | None = None,
) -> list[DeviceConnection]:
    """Every enabled device whose connection goes to one of ``names``.

    ``names`` are the ways the caller refers to one host (what was typed and
    the address it resolved to, say); a device matches when its effective
    host is any of them. Serial devices have no host and never match.
    ``devices`` is :func:`device_connections`' own.
    """
    wanted = [n for n in names if n and n.strip()]
    return [
        conn for conn in device_connections(project, devices)
        if conn.transport != "serial" and conn.host
        and any(_same_host(conn.host, n) for n in wanted)
    ]
