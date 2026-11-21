"""
Device connection resolution — what a device in the project actually dials.

A project device declares only part of its connection: the driver supplies
defaults, ``device.config`` holds protocol fields, and the project's
``connections`` table holds host/port/baudrate. On top of that, two bindings
rewrite the result entirely — a bridge-bound device travels through another
device's pass-through port, and a USB-serial device's volatile OS port is
derived from its stable adapter id. The bridge binding is
``core/bridge_binding.py``'s: it records what the binding came to in the
resolved config, and every reader here asks that record.

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
test (``core/device_reachability``) dials that. A caller that holds the
running driver asks ``driver_connection``, the same address read from the
driver's own config: the reconnect check (``core/device_reconnect``) dials
it, and the device card's reason sentence (``core/device_away``) names it.
Either reading names a host written as a URL by the name and port it names
(``transport/endpoint.url_address``).

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

from openavc.core.bridge_binding import ROUTE_KEY, bridge_binding, resolve_bridge_binding
from openavc.core.typed_fields import trim_typed_fields
from openavc.drivers.registry import (
    get_driver_bridge_ports,
    get_device_transport,
    get_driver_default_config,
    get_driver_transport,
)
from openavc.drivers.transport_choice import effective_transport
from openavc.transport.endpoint import UnusablePort, implied_port, url_address
from openavc.transport.serial_transport import resolve_usb_binding
from openavc.utils.logger import get_logger

log = get_logger(__name__)


def layered_config(
    defaults: Mapping[str, Any], device_config: Mapping[str, Any], connection: Mapping[str, Any],
) -> dict[str, Any]:
    """A device's settings layered, later winning: its driver's
    ``default_config``, the device's ``config``, its connection row. Pure, so
    a reader with only a project's data (``core/saved_params``) layers them
    as ``resolve_device_config`` does.

    ``ir_codes`` overlays per code on top of the driver's shipped default set
    rather than the shallow merge's whole-map replace: a device that authors
    a single code (one IrCodesEditor save persists just that code) must not
    wipe every code the driver ships.
    """
    merged = {**defaults, **device_config, **connection}
    default_codes = defaults.get("ir_codes")
    device_codes = device_config.get("ir_codes")
    if isinstance(default_codes, dict) and isinstance(device_codes, dict):
        merged["ir_codes"] = {**default_codes, **device_codes}
    return merged


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
    merged = layered_config(
        get_driver_default_config(cfg.get("driver", "")),
        cfg.get("config", {}),
        project.connections.get(cfg["id"], {}),
    )
    # A space typed around an address or a login comes off before anything
    # dials it, so a project saved with one connects without an edit.
    merged = trim_typed_fields(cfg.get("driver", ""), merged)
    cfg["config"] = resolve_bridge_binding(merged, project, cfg.get("driver", ""))
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


def is_bridge_config(cfg: dict[str, Any]) -> bool:
    """True if a resolved device config belongs to a bridge driver."""
    return bool(get_driver_bridge_ports(cfg.get("driver", "")))


# The mark dialled_record leaves on a record the simulation points at the
# simulator; connection_of reads it into DeviceConnection.simulated.
_SIMULATED = "simulated"


@dataclass(frozen=True)
class DeviceConnection:
    """Where one project device connects, after every layer and rewrite.

    ``port`` is a number for a network transport and a path for serial; a
    transport that falls back to a port of its own (http, ssh, mqtt, snmp)
    reports that port when the device names none. A host written as a URL is
    reported as the name and port it names (``transport/endpoint.url_address``),
    which is where the device's client connects. A serial device has no
    ``host``, whatever its settings kept from another transport. ``bridge`` is the id of the
    bridge the device is set to go through (``core.bridge_binding``): it
    reaches ``host`` through that bridge's pass-through port, or (transport
    ``bridge``) emits through it and holds no connection of its own; or the
    binding cannot be completed, and ``host`` and ``port`` are empty, since the
    device connects nowhere. Empty for a device on no bridge. ``simulated`` is
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


def _port_of(transport: str, cfg: dict[str, Any]) -> int | str | None:
    """The port ``cfg`` dials over ``transport``: a serial port's path, a
    network port's number, or the transport's fallback when none is set."""
    port = cfg.get("port")
    if transport == "serial":
        return str(port) if port is not None else None
    if port is None or port == "":
        return implied_port(transport, cfg)
    # A port that is not a number is reported as no port, never replaced
    # by the transport's fallback: the driver would not dial that either.
    try:
        return int(port)
    except (TypeError, ValueError):
        return None


def _address_of(transport: str, cfg: dict[str, Any]) -> tuple[str, int | str | None]:
    """The host and port ``cfg`` dials over ``transport``. A host written as
    a URL dials the name and port it names; the device's port setting is read
    only when the URL's scheme has no port of its own."""
    if transport == "serial":
        return "", _port_of(transport, cfg)
    host = str(cfg.get("host") or "")
    url = url_address(host)
    if url is None:
        return host, _port_of(transport, cfg)
    try:
        port = url.port()
    except UnusablePort:
        # As a port setting that is not a number: no port, never a fallback.
        return url.name, None
    return url.name, port if port is not None else _port_of(transport, cfg)


def connection_of(
    resolved: dict[str, Any], definition: Mapping[str, Any] | None = None,
) -> DeviceConnection:
    """Where a device connects, from its resolved record.

    ``resolved`` is what :func:`resolve_device_config` returns, which is also
    what the device manager holds for every device it was handed: the device
    as it is set to. Handed :func:`dialled_record`'s answer instead, it is
    where the device connects now. The transport is the one
    ``BaseDriver.connect`` opens (``transport_choice``: the device's own
    choice, else its driver's, else tcp); the driver's is read from
    ``definition`` when given (a running driver's own ``DRIVER_INFO``), else
    from the driver registered under the record's driver id.
    """
    cfg = resolved.get("config") or {}
    driver = str(resolved.get("driver") or "")
    transport = (
        effective_transport(cfg, definition) if definition is not None
        else get_device_transport(driver, cfg)
    )
    # What the resolver recorded of a bridge binding: a pass-through is
    # already rewritten to the bridge's host, and a binding that cannot be
    # completed connects nowhere, whatever address the config still holds.
    binding = bridge_binding(cfg)
    host, port = ("", None) if binding.unresolved else _address_of(transport, cfg)
    device_id = str(resolved.get("id") or "")
    return DeviceConnection(
        device_id=device_id,
        name=str(resolved.get("name") or device_id),
        driver=driver,
        transport=transport,
        host=host,
        port=port,
        bridge=binding.bridge,
        simulated=bool(resolved.get(_SIMULATED)),
    )


def driver_connection(driver: Any) -> DeviceConnection:
    """Where a running driver dials now: :func:`connection_of` over the
    driver's own config and definition.

    For a caller holding the driver (the reconnect check, the device card's
    reason sentence). A running simulation has pointed that config at the
    simulator, so the transport, host and port are the ones
    :func:`dialled_record` names for the same driver, read without the
    device manager's record. Unlike that record it is not marked
    ``simulated``, and it keeps a bridge binding the driver's config carries.
    """
    info = getattr(driver, "DRIVER_INFO", None) or {}
    record = {
        "id": getattr(driver, "device_id", ""),
        "driver": str(info.get("id") or ""),
        "config": getattr(driver, "config", None) or {},
    }
    return connection_of(record, info)


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
        for key in ("bridge", "bridge_port", ROUTE_KEY):
            cfg.pop(key, None)
        return {**record, "config": cfg, _SIMULATED: True}
    return {**record, "config": resolve_usb_binding(cfg, get_driver_transport(driver))}


class RunningDevices(Protocol):
    """What :func:`device_connections` asks of the device manager."""

    def connections_now(self) -> list[DeviceConnection]: ...

    def device_record(self, device_id: str) -> Any: ...


def device_connections(
    project, devices: RunningDevices | None = None,
) -> list[DeviceConnection]:
    """The effective connection of every enabled device in ``project``.

    ``devices`` is the engine's device manager, and its record of each device
    (``device_record``) says whether it connects. Each device it runs is
    answered where it connects now (``connections_now``: its record and the
    simulation redirect on its driver), so a simulated device holds its
    simulator's port and not its own, and a project edit not applied yet
    does not hide the port the running driver holds; a running device the
    project no longer has, or has disabled, still holds it and is answered
    too. One waiting for its driver, or for its driver to set it up, is
    answered as its record sets it; one disabled, or whose settings could not
    be worked out, holds nothing and is left out. A project device the
    manager has no record of yet (an edit not applied) is answered as the
    project sets it. Without ``devices`` every device is answered as it is
    set to in ``project``, a disabled one left out.

    A device whose config cannot be resolved is left out (the reconcile
    reports it; this answers a question about live devices).
    """
    running = (
        {conn.device_id: conn for conn in devices.connections_now()}
        if devices is not None else {}
    )
    out: list[DeviceConnection] = []
    for device in getattr(project, "devices", None) or []:
        now = running.pop(getattr(device, "id", None), None)
        conn = now if now is not None else _connection_not_running(device, project, devices)
        if conn is not None:
            out.append(conn)
    out.extend(running.values())
    return out


def _connection_not_running(
    device, project, devices: RunningDevices | None,
) -> DeviceConnection | None:
    """Where a project device no driver runs would connect, or None when it
    holds nothing: as the device manager's record sets it, or, with no record
    yet, as the project does. Disabled, or whose settings could not be worked
    out, it holds nothing."""
    record = devices.device_record(device.id) if devices is not None else None
    if record is not None:
        return connection_of(record.config) if record.connects else None
    if not getattr(device, "enabled", True):
        return None
    try:
        return connection_of(resolve_device_config(device, project))
    except Exception:  # an unresolvable device is not a live connection
        log.debug("Could not resolve %s's connection", getattr(device, "id", "?"),
                  exc_info=True)
        return None


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
