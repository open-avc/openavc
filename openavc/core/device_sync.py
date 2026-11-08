"""How the project's devices reach the device manager, for a start and for
a project change (``Engine._sync_devices``): adding them, re-adding the
ones whose settings changed, and cleaning up after one that left.

The rules worth knowing before changing it. Devices are added bridges first
and then the devices that route through them, each batch in parallel, and a
device that fails to add never stops the others. A running device is
re-added only when a field ``add_device`` acts on changed (``needs_readd``):
not just its name, driver or connection, but whether it is enabled, its
child entities and its pending settings too, with a missing ``enabled``
read as on and no map the same as an empty one.

Functions of the device manager and the state, so they need no engine.
Logs under the engine's name, which is how the log view files these lines.
"""

from __future__ import annotations

import asyncio

from openavc.core.device_config import bridge_first
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


# The resolved-config fields add_device acts on, each with what a missing one
# reads as. A change to any of them re-adds the device, not just a name, driver
# or connection change: enabled gates connect/poll, child_entities seeds child
# labels, and pending_settings is applied on (re)connect, so leaving one out
# left that edit inert on the hot-reload path until a restart.
_READD_FIELDS = {"name": None, "driver": None, "enabled": True, "config": {}}
# The same, for the two where no map and an empty map mean the same thing.
_READD_MAPS = ("child_entities", "pending_settings")


def needs_readd(old: dict, new: dict) -> bool:
    """Whether a device running with ``old`` must be re-added to run ``new``."""
    if any(old.get(field, missing) != new.get(field, missing)
           for field, missing in _READD_FIELDS.items()):
        return True
    return any((old.get(field) or {}) != (new.get(field) or {}) for field in _READD_MAPS)


def log_failed_sync_add(device_id: str, error: Exception) -> None:
    log.error(f"Failed to add device '{device_id}' during sync: {error}")


async def add_devices(devices, resolved: dict[str, dict], *, defer_connect, on_failure) -> None:
    """Add the devices in ``resolved`` (id -> resolved config, in the order
    given) to the device manager: bridges first, then the devices that route
    through them (``bridge_first``), so a bridge-bound device finds its live
    bridge to prep the port.

    Each batch is added in parallel. One at a time would serialize connect
    timeouts (5 s each), so N offline devices would hold a start for N x 5 s;
    each add writes only its own device's keys, so gathering them is safe. A
    device that fails goes to ``on_failure(device_id, error)`` as its batch
    finishes, and the rest are still added.
    """
    for batch in bridge_first(list(resolved), resolved):
        results = await asyncio.gather(
            *(devices.add_device(
                resolved[did], defer_connect=defer_connect,
            ) for did in batch),
            return_exceptions=True,
        )
        for did, result in zip(batch, results):
            if isinstance(result, Exception):
                on_failure(did, result)


def delete_device_keys(state, device_id: str) -> None:
    """Clean up a removed device's orphaned device.{id}.* state keys (14.6)."""
    prefix = f"device.{device_id}."
    orphaned = state.get_namespace(prefix)
    for suffix in orphaned:
        state.delete(f"{prefix}{suffix}")
    if orphaned:
        log.info(f"Cleaned up {len(orphaned)} orphaned state key(s) for removed device '{device_id}'")


async def update_changed_devices(devices, device_ids, project_devices, *, defer_connect) -> None:
    """Re-add, in parallel, each of ``device_ids`` whose running config
    differs from its project one in a field add_device acts on
    (``needs_readd``); ``update_device`` is remove + add."""
    changed_ids = [
        did for did in device_ids
        if needs_readd(devices.get_device_config(did) or {}, project_devices[did])
    ]
    if not changed_ids:
        return
    update_results = await asyncio.gather(
        *(devices.update_device(
            did, project_devices[did], defer_connect=defer_connect
        ) for did in changed_ids),
        return_exceptions=True,
    )
    for did, result in zip(changed_ids, update_results):
        if isinstance(result, Exception):
            log.error(f"Failed to update device '{did}' during sync: {result}")
