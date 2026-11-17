"""Whether a device goes through a bridge, and what came of it.

A device's connection can name a bridge (``bridge``, the bridge device's id)
and a port on it (``bridge_port``). What that comes to is worked out once,
by :func:`resolve_bridge_binding` when the device's config is resolved
(``core.device_config.resolve_device_config``), and recorded in the resolved
config under ``bridge_route``:

- ``passthrough``: a serial port. The connection is rewritten to the bridge's
  transparent TCP pass-through (``transport`` tcp, the bridge's host, the
  port's ``passthrough_port``), and the bridge pushes the serial line settings
  to its hardware before bytes flow.
- ``emitter``: an IR port. The device has no connection of its own
  (``transport`` bridge): it sends through the live bridge, and is online
  exactly while its bridge is.
- anything else: the connection cannot be completed, and the word says why.
  The device is refused before anything is dialled, with the sentence
  :meth:`BridgeBinding.problem` gives, never left to dial an address or a
  serial port of its own.

THE rule: every reader of whether a device goes through a bridge asks
:func:`bridge_binding` for the record, never the two markers. The connection
it reports (``device_config.connection_of``), the connect door
(``DeviceManager._connect_driver``), Test Connection, a device audit, the USB
adapter follow-up, and the bridge's own bookkeeping of the devices it carries
(``core/device_bridges.py``) all read it. A config that never passed the
resolver carries no record and goes through no bridge; a record that found its
way into a saved project is dropped by the next resolve, which is its only
writer. The save doors still read the markers as typed
(``core.required_config``), since there is no resolved record yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openavc.core.connection_fault import bridge_port_missing, bridge_port_missing_message
from openavc.core.typed_fields import trim_typed_fields
from openavc.drivers.registry import (
    get_device_transport,
    get_driver_bridge_ports,
    get_driver_default_config,
    is_driver_registered,
)
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: The resolved config key the outcome is recorded under.
ROUTE_KEY = "bridge_route"

# Resolved.
PASSTHROUGH = "passthrough"
EMITTER = "emitter"

# Not resolved, by why.
NO_BRIDGE = "no_bridge"  # a device that sends through a bridge names none
NO_PORT = "no_port"  # a bridge named with no port chosen on it
BRIDGE_MISSING = "bridge_missing"  # the bridge is not in the project
BRIDGE_DRIVER_MISSING = "bridge_driver_missing"  # the bridge's driver is not installed
PORT_UNKNOWN = "port_unknown"  # the bridge's driver advertises no such port
NO_HOST = "no_host"  # a serial pass-through on a bridge with no address
NO_ROUTE = "no_route"  # a port that carries no connection (relay, no pass-through)

_THROUGH = (PASSTHROUGH, EMITTER)

_PROBLEMS = {
    NO_BRIDGE: (
        "No bridge is chosen for this device. In Edit Device, choose the bridge "
        "and the port the device is wired to."
    ),
    BRIDGE_MISSING: (
        "This device connects through the bridge '{bridge}', which is not in "
        "the project. In Edit Device, choose another bridge."
    ),
    BRIDGE_DRIVER_MISSING: (
        "The driver for the bridge '{bridge}' is not installed. Install it from "
        "the bridge's device page."
    ),
    PORT_UNKNOWN: (
        "The bridge '{bridge}' has no port '{port}'. In Edit Device, choose the "
        "port on the bridge that the device is wired to."
    ),
    NO_HOST: "The bridge '{bridge}' has no IP address. Enter one in the bridge's Edit Device.",
    NO_ROUTE: (
        "The port '{port}' on the bridge '{bridge}' cannot carry this device's "
        "connection. In Edit Device, choose another port on the bridge."
    ),
}

_ROUTES = frozenset((*_THROUGH, NO_PORT, *_PROBLEMS))


@dataclass(frozen=True)
class BridgeBinding:
    """A resolved config's bridge binding, as the resolver recorded it.

    ``route`` is the recorded word, "" for a device that goes through no
    bridge. ``bridge`` and ``port`` are the ids the binding names.
    """

    bridge: str = ""
    port: str = ""
    route: str = ""

    @property
    def bound(self) -> bool:
        """Set to go through a bridge, whether or not it resolved."""
        return bool(self.route)

    @property
    def through(self) -> bool:
        """Its connection goes through the bridge: a pass-through or an emitter."""
        return self.route in _THROUGH

    @property
    def passthrough(self) -> bool:
        """Dials the bridge's serial pass-through itself."""
        return self.route == PASSTHROUGH

    @property
    def emits(self) -> bool:
        """Sends through the live bridge and has no connection of its own."""
        return self.route == EMITTER

    @property
    def unresolved(self) -> bool:
        """Set to go through a bridge, and the connection cannot be completed."""
        return self.bound and not self.through

    def problem(self, bridge_label: str = "") -> str:
        """The sentence the device card and Test Connection show for a binding
        that did not resolve, naming the bridge by ``bridge_label`` (its name)
        or else its id; "" for one that did."""
        if not self.unresolved:
            return ""
        if self.route == NO_PORT:
            return bridge_port_missing_message()
        return _PROBLEMS[self.route].format(
            bridge=bridge_label or self.bridge, port=self.port,
        )


def bridge_binding(config: dict[str, Any] | None) -> BridgeBinding:
    """The bridge binding :func:`resolve_bridge_binding` recorded in ``config``.

    THE question every reader asks. No record (a config the resolver never
    saw) is no binding.
    """
    config = config or {}
    route = config.get(ROUTE_KEY)
    if route not in _ROUTES:
        return BridgeBinding()
    return BridgeBinding(
        bridge=str(config.get("bridge") or ""),
        port=str(config.get("bridge_port") or ""),
        route=str(route),
    )


def _recorded(config: dict, route: str, **rewrite: Any) -> dict:
    return {**config, **rewrite, ROUTE_KEY: route}


def resolve_bridge_binding(config: dict, project, driver: str = "") -> dict:
    """``config`` with its bridge binding resolved and the outcome recorded.

    ``config`` is a device's merged config, ``project`` the project it is in,
    and ``driver`` the device's driver id, which says whether the device sends
    through a bridge with no connection of its own (``transport`` bridge).
    A config that names no bridge is returned with no record, unless its
    transport is bridge, which records that no bridge is chosen. Never raises,
    and never rewrites a binding it could not resolve: the record says why, and
    the connect door refuses it.
    """
    config = {k: v for k, v in config.items() if k != ROUTE_KEY}
    if bridge_port_missing(config):
        return _recorded(config, NO_PORT)
    bridge_id = config.get("bridge")
    if not bridge_id or not config.get("bridge_port"):
        if get_device_transport(driver, config) == "bridge":
            return _recorded(config, NO_BRIDGE)
        return config
    return _resolve_named(config, project, str(bridge_id), str(config["bridge_port"]))


def _resolve_named(config: dict, project, bridge_id: str, port_id: str) -> dict:
    """A binding that names both a bridge and a port on it."""
    bridge_dev = next((d for d in project.devices if d.id == bridge_id), None)
    if bridge_dev is None:
        log.warning("Bridge '%s' named by a device's connection is not in the project", bridge_id)
        return _recorded(config, BRIDGE_MISSING)
    if not is_driver_registered(bridge_dev.driver):
        return _recorded(config, BRIDGE_DRIVER_MISSING)
    port_def = get_driver_bridge_ports(bridge_dev.driver).get(port_id)
    if port_def is None:
        log.warning(
            "Bridge '%s' (driver '%s') does not advertise port '%s'",
            bridge_id, bridge_dev.driver, port_id,
        )
        return _recorded(config, PORT_UNKNOWN)
    return _through_port(config, port_def, bridge_dev, project)


def _through_port(config: dict, port_def: dict, bridge_dev, project) -> dict:
    """A binding to a port the bridge advertises, by the port's kind."""
    kind = port_def.get("kind")
    if kind == "ir":
        # Sends through the live bridge instance at send time
        # (BaseDriver.emit_via_bridge); connect() opens no socket.
        return _recorded(config, EMITTER, transport="bridge")
    passthrough_port = port_def.get("passthrough_port")
    if kind != "serial" or not passthrough_port:
        return _recorded(config, NO_ROUTE)
    bridge_host = _bridge_host(bridge_dev, project)
    if not bridge_host:
        return _recorded(config, NO_HOST)
    # The serial line settings stay in the config, so the bridge driver can
    # push them to the hardware (prepare_bridge_port) before bytes flow.
    return _recorded(
        config, PASSTHROUGH, transport="tcp", host=bridge_host, port=passthrough_port,
    )


def _bridge_host(bridge_dev, project) -> str:
    """The bridge's own host, layered the way every device's connection is
    (driver defaults < device.config < connections table): a host from a
    driver default or the bridge's device.config counts as much as one in the
    connections table."""
    bridge_conn = project.connections.get(bridge_dev.id, {})
    merged = trim_typed_fields(bridge_dev.driver, {
        **get_driver_default_config(bridge_dev.driver),
        **(getattr(bridge_dev, "config", None) or {}),
        **bridge_conn,
    })
    return str(merged.get("host") or "")
