"""An audit started from a project device's page: where it points, and what it may reuse.

"Audit this device" on a device page fills the wizard in from the project: the
address the device really connects to, its driver, and its saved connection
settings. The address comes from the same resolved config the device manager
dials (``core.device_config.resolve_device_config``), so a host from the
connection table or a driver default is found as it is in production.

An audit starts from a network address, so a device without one is refused
in words: one on a serial port of this computer, one reached through a
bridge (its bytes go to the bridge's address, and the bridge's own set-up
steps are not part of an audit), and one with no address entered.

The session remembers the device it was started from (its **origin**) when
the address was not changed on the way. The origin's saved settings are
offered on the Connection step even when the audit paused nothing (a disabled
device holds no connection, so there is nothing to pause), and only while
that device still connects to the audited address: its credentials are never
sent to any other device.
"""

from __future__ import annotations

from typing import Any, Iterable

from openavc.core.device_config import resolve_device_config
from openavc.drivers.registry import get_driver_transport
from openavc.utils.logger import get_logger

log = get_logger(__name__)

SERIAL = (
    "An audit needs the device's network address, and {name} is connected to a "
    "serial port."
)
BRIDGED = (
    "{name} is reached through {bridge}. An audit needs a device OpenAVC connects "
    "to directly."
)
NO_ADDRESS = "{name} has no address yet. Enter one with Edit, then audit it."
UNRESOLVED = "{name}'s connection settings could not be read. Check them with Edit."


def _find(project: Any, device_id: str) -> Any:
    return next(
        (d for d in getattr(project, "devices", None) or [] if d.id == device_id), None,
    )


def device_target(project: Any, device_id: str) -> dict[str, Any] | None:
    """What an audit of project device ``device_id`` starts from, or None
    when the project has no such device.

    ``auditable`` is False with ``reason`` the sentence the person reads.
    """
    device = _find(project, device_id)
    if device is None:
        return None
    name = device.name or device.id
    out: dict[str, Any] = {
        "device_id": device.id,
        "name": name,
        "driver": device.driver,
        "address": "",
        "transport": "",
        "auditable": False,
        "reason": "",
    }
    try:
        cfg = resolve_device_config(device, project).get("config") or {}
    except Exception:
        log.debug("Could not resolve %s for an audit", device_id, exc_info=True)
        out["reason"] = UNRESOLVED.format(name=name)
        return out
    transport = str(cfg.get("transport") or get_driver_transport(device.driver) or "tcp").lower()
    out["transport"] = transport
    bridge_id = cfg.get("bridge")
    if bridge_id:
        bridge = _find(project, str(bridge_id))
        out["reason"] = BRIDGED.format(
            name=name, bridge=(bridge.name or bridge.id) if bridge is not None else bridge_id,
        )
        return out
    if transport == "serial":
        out["reason"] = SERIAL.format(name=name)
        return out
    host = str(cfg.get("host") or "").strip()
    if not host:
        out["reason"] = NO_ADDRESS.format(name=name)
        return out
    out["address"] = host
    out["auditable"] = True
    return out


def connects_to(project: Any, device_id: str, names: Iterable[str]) -> bool:
    """Whether the device's effective address is one of ``names``."""
    target = device_target(project, device_id)
    if not target or not target["auditable"]:
        return False
    host = target["address"].rstrip(".").lower()
    return any(host == str(n).strip().rstrip(".").lower() for n in names if n)


def origin_for(
    project: Any, device_id: str | None, names: Iterable[str],
) -> dict[str, str] | None:
    """The origin record for a session on ``names``, or None when the device
    no longer connects there (the person changed the address, say)."""
    if not device_id or project is None:
        return None
    names = list(names)
    if not connects_to(project, device_id, names):
        return None
    device = _find(project, device_id)
    return {"device_id": device.id, "name": device.name or device.id, "driver": device.driver}
