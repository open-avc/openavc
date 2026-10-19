"""
Driver class registry — the process-wide map of driver id to driver class.

Every driver the server knows about lands here: built-in ``.avcdriver``
definitions, community drivers installed into ``driver_repo/``, Python
drivers, and drivers created live in the Programmer IDE. ``driver_loader``
fills it; everything else reads it — device instantiation, config
resolution, the REST driver list, discovery hints, the cloud AI tools, and
the project's driver-dependency scan.

Kept deliberately small and dependency-free (stdlib plus the logger) so any
of those callers can import it at module scope without dragging in the YAML
parser, the validation stack, or the device manager. Nothing here touches
the disk — loading is ``driver_loader.load_builtin_drivers()``, called once
at engine startup.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.drivers.base import BaseDriver

log = get_logger(__name__)

# Driver registry — maps driver ID strings to driver classes
_DRIVER_REGISTRY: dict[str, type[BaseDriver]] = {}
_RESERVED_DRIVERS = set()


def reserve_driver(driver_class):
    driver_id = driver_class.DRIVER_INFO["id"]
    _DRIVER_REGISTRY[driver_id] = driver_class
    _RESERVED_DRIVERS.add(driver_id)


def is_reserved_driver(driver_id):
    return driver_id in _RESERVED_DRIVERS


def require_mutable_driver(driver_id):
    if is_reserved_driver(driver_id):
        from openavc.core.installed_equipment import InstalledEquipmentError
        raise InstalledEquipmentError("This integration is installed by the manufacturer and cannot be replaced or uninstalled.")


def register_driver(driver_class: type[BaseDriver]) -> None:
    """Register a driver class in the global registry."""
    driver_id = driver_class.DRIVER_INFO.get("id", "")
    require_mutable_driver(driver_id)
    if driver_id:
        _DRIVER_REGISTRY[driver_id] = driver_class
        log.debug(f"Registered driver: {driver_id}")


def unregister_driver(driver_id: str) -> bool:
    """Remove a driver class from the global registry. Returns True if removed."""
    require_mutable_driver(driver_id)
    removed = _DRIVER_REGISTRY.pop(driver_id, None) is not None
    if removed:
        log.info(f"Unregistered driver: {driver_id}")
    return removed


def is_driver_registered(driver_id: str) -> bool:
    """Check if a driver ID is registered in the global registry."""
    return driver_id in _DRIVER_REGISTRY


def get_driver_class(driver_id: str) -> type[BaseDriver] | None:
    """Return the registered driver class for ``driver_id``, or ``None``.

    The one way to reach a driver class: callers that need something off
    ``DRIVER_INFO`` that the typed getters below don't cover (the project's
    dependency scan wants name + version, device instantiation wants the
    class itself) ask here rather than reaching into the dict.
    """
    return _DRIVER_REGISTRY.get(driver_id)


def registered_driver_classes() -> list[tuple[str, type[BaseDriver]]]:
    """Return ``(driver_id, driver_class)`` for every registered driver.

    A snapshot list, not a live view — callers iterate it while installing or
    replacing drivers, which mutates the registry underneath them.
    """
    return list(_DRIVER_REGISTRY.items())


def get_driver_default_config(driver_id: str) -> dict[str, Any]:
    """Return the registered driver's ``default_config``, or ``{}`` if unknown.

    Used by ``core.device_config.resolve_device_config`` to layer driver-declared
    defaults under saved device config. Unknown / orphaned drivers return
    an empty dict so a missing driver behaves the same as today (the
    device will fail to instantiate, but resolution stays well-defined).
    """
    cls = _DRIVER_REGISTRY.get(driver_id)
    if cls is None:
        return {}
    defaults = cls.DRIVER_INFO.get("default_config", {}) or {}
    return dict(defaults)


def get_driver_discovery_port(driver_id: str, evidence: list[dict[str, Any]]) -> int | None:
    """Resolve a control port only when the selected driver explicitly opts in.

    An mDNS fingerprint may describe an admin or media service. Never treat
    an arbitrary advertised/open port as the selected driver's control port.
    The resolver inspects already-collected evidence without network I/O.
    """
    cls = _DRIVER_REGISTRY.get(driver_id)
    resolver = getattr(cls, "discovered_control_port", None)
    if not callable(resolver):
        return None
    port = resolver(evidence)
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ValueError("The discovered control port is invalid. Add the device manually with its configured port.")
    return port


def get_driver_transport(driver_id: str) -> str:
    """Return the registered driver's declared transport (``DRIVER_INFO
    ['transport']``, defaulting to ``tcp`` like the connect path), or ``""``
    if the driver is unknown. Used to resolve a device's effective transport
    when its saved config omits one, so a stray ``usb_serial`` on a network
    device can't hijack its port.
    """
    cls = _DRIVER_REGISTRY.get(driver_id)
    if cls is None:
        return ""
    return cls.DRIVER_INFO.get("transport", "tcp")


def get_driver_bridge_ports(driver_id: str) -> dict[str, dict[str, Any]]:
    """Return a registered bridge driver's advertised ports as
    ``{port_id: {kind, passthrough_port?, label?}}``, or ``{}`` if the driver
    is unknown or not a bridge.

    A *bridge* driver declares ``DRIVER_INFO["bridge"]["ports"]``: a list of
    typed ports (``serial`` / ``ir`` / ``relay``) that other devices connect
    *through*. Serial ports carry a ``passthrough_port`` (the TCP port on the
    bridge host that transparently pipes that serial line, e.g. 4999); IR /
    relay ports route commands through the bridge's command socket instead and
    omit it. Used by ``core.device_config`` to rewrite a
    bridge-bound downstream device's transport, and by the device manager to
    order bridges ahead of their dependents.
    """
    cls = _DRIVER_REGISTRY.get(driver_id)
    if cls is None:
        return {}
    bridge = cls.DRIVER_INFO.get("bridge") or {}
    ports = bridge.get("ports") or []
    result: dict[str, dict[str, Any]] = {}
    for port in ports:
        pid = port.get("id")
        if pid:
            result[pid] = dict(port)
    return result


def list_registered_drivers() -> list[dict[str, Any]]:
    """Return metadata for all registered drivers.

    The driver catalog as every pre-device surface sees it: the REST driver
    list, the driver browser, discovery's hint index, and the cloud AI's
    ``list_drivers``. A projection of ``DRIVER_INFO``, not the registry
    itself — for the class, use :func:`get_driver_class`.
    """
    return [
        {
            "id": driver_class.DRIVER_INFO.get("id", ""),
            "name": driver_class.DRIVER_INFO.get("name", ""),
            "manufacturer": driver_class.DRIVER_INFO.get("manufacturer", ""),
            "category": driver_class.DRIVER_INFO.get("category", ""),
            "description": driver_class.DRIVER_INFO.get("description", ""),
            "version": driver_class.DRIVER_INFO.get("version", ""),
            "author": driver_class.DRIVER_INFO.get("author", ""),
            "transport": driver_class.DRIVER_INFO.get("transport", "tcp"),
            # Multi-transport drivers ([tcp, serial]) and bridge port
            # declarations — the connection picker offers "through a bridge"
            # for serial-capable drivers and lists bridge devices + their ports.
            "transports": driver_class.DRIVER_INFO.get("transports", []),
            "bridge": driver_class.DRIVER_INFO.get("bridge", {}),
            "commands": driver_class.DRIVER_INFO.get("commands", {}),
            "config_schema": driver_class.DRIVER_INFO.get("config_schema", {}),
            "default_config": driver_class.DRIVER_INFO.get("default_config", {}),
            "state_variables": driver_class.DRIVER_INFO.get("state_variables", {}),
            "help": driver_class.DRIVER_INFO.get("help", {}),
            # Present only when the driver declares a block: the hint loader
            # reads a present block as asked-for and warns when it is empty.
            **({"discovery": driver_class.DRIVER_INFO["discovery"]}
               if "discovery" in driver_class.DRIVER_INFO else {}),
            "device_settings": driver_class.DRIVER_INFO.get("device_settings", {}),
            # The models the driver lists (the catalog carries the same list for
            # catalog drivers; this is where an imported driver's comes from).
            "compatible_models": driver_class.DRIVER_INFO.get("compatible_models", []),
            # Action strip + child types, so pre-device UIs (driver browser
            # detail) can show a driver's full surface — device-level views
            # get the resolved form via get_device_info.
            "actions": driver_class.DRIVER_INFO.get("actions", []),
            "quick_actions": driver_class.DRIVER_INFO.get("quick_actions", []),
            "child_entity_types": driver_class.DRIVER_INFO.get("child_entity_types", {}),
            "manufacturer_installed": is_reserved_driver(driver_class.DRIVER_INFO.get("id", "")),
        }
        for driver_class in _DRIVER_REGISTRY.values()
    ]
