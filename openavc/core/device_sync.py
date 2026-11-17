"""How the project's devices reach the device manager, for a start and for
a project change (``Engine._sync_devices``): adding them, re-adding the
ones whose settings changed, and cleaning up after one that left.

The rules worth knowing before changing it. Each device is worked out on its
own (``resolve_each``) and split into its batch on its own: one whose settings
cannot be worked out, or whose driver cannot say whether it is a bridge, is
reported failed with why (``UnresolvedDevice``) and never costs the others
theirs. Devices are added bridges first and then the devices that route
through them, each batch in parallel, and a device that fails to add never
stops the others. A running device is
re-added only when a field ``add_device`` acts on changed (``needs_readd``):
not just its name, driver or connection, but whether it is enabled, its
child entities and its pending settings too, with a missing ``enabled``
read as on and no map the same as an empty one.

Functions of the device manager and the state, so they need no engine.
Logs under the engine's name, which is how the log view files these lines.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from openavc.core import device_config
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


class UnresolvedDevice(Exception):
    """A project device that could not be handed to the device manager,
    because working it out raised: its settings, or whether its driver is a
    bridge. ``device_id`` names it; the message says which of the two failed,
    then what raised (also its ``__cause__``)."""

    def __init__(self, device_id: str, step: str, cause: BaseException) -> None:
        super().__init__(f"{step}: {cause}")
        self.device_id = device_id
        self.__cause__ = cause


@dataclass(frozen=True)
class ResolvedDevices:
    """The project's devices, each worked out on its own: ``configs`` holds
    the resolved record of every device that could be, by id in project
    order, and ``failed`` why each other one could not."""

    configs: dict[str, dict[str, Any]]
    failed: dict[str, UnresolvedDevice]


def resolve_each(devices: Iterable[Any], resolve: Callable[[Any], dict[str, Any]]) -> ResolvedDevices:
    """Resolve each of ``devices`` with ``resolve`` inside its own guard.

    Resolving reads the device's driver (its defaults, the ports of the
    bridge it is bound to) and its connection row, any of which can hold a
    value of the wrong kind: a driver the load kept with a notice, a project
    file written by hand. Whatever that raises is that device's failure.
    """
    configs: dict[str, dict[str, Any]] = {}
    failed: dict[str, UnresolvedDevice] = {}
    for device in devices:
        try:
            configs[device.id] = resolve(device)
        except Exception as error:  # Catch-all: one device's settings never cost the others theirs
            failed[device.id] = UnresolvedDevice(
                device.id, "its settings could not be worked out", error,
            )
    return ResolvedDevices(configs, failed)


def bridges_first(resolved: dict[str, dict[str, Any]]) -> tuple[list[list[str]], dict[str, UnresolvedDevice]]:
    """Split the devices in ``resolved`` (id -> resolved config, in the order
    given) into ``[bridges, others]`` (each batch only if non-empty), keeping
    the order within each, so a bridge is added and connected before the
    devices that route through it: a bridge-bound device's connect needs its
    bridge live to prep the port (push serial baud/parity) first.

    Each device is asked on its own; one whose driver cannot say whether it is
    a bridge (a malformed ``bridge:`` block) is in no batch, and comes back
    with why.
    """
    bridges: list[str] = []
    others: list[str] = []
    failed: dict[str, UnresolvedDevice] = {}
    for did, cfg in resolved.items():
        try:
            is_bridge = device_config.is_bridge_config(cfg)
        except Exception as error:  # Catch-all: one driver's bridge block never costs the others their start
            failed[did] = UnresolvedDevice(
                did, "its driver could not say whether it is a bridge", error,
            )
            continue
        (bridges if is_bridge else others).append(did)
    return [batch for batch in (bridges, others) if batch], failed


def log_failed_sync_add(device_id: str, error: Exception) -> None:
    log.error(f"Failed to add device '{device_id}' during sync: {error}")


def log_unresolved_at_sync(failed: dict[str, UnresolvedDevice], running_ids: set[str]) -> None:
    """Say why each device a sync could not work out was left as it is: one
    not running is not added, one running keeps the settings it runs with,
    since it is still in the project and nothing better is known."""
    for did, error in failed.items():
        if did in running_ids:
            log.error(f"Device '{did}' keeps running with the settings it had: {error}")
        else:
            log_failed_sync_add(did, error)


async def add_devices(devices, resolved: dict[str, dict], *, defer_connect, on_failure) -> None:
    """Add the devices in ``resolved`` (id -> resolved config, in the order
    given) to the device manager: bridges first, then the devices that route
    through them (``bridges_first``), so a bridge-bound device finds its live
    bridge to prep the port.

    Each batch is added in parallel. One at a time would serialize connect
    timeouts (5 s each), so N offline devices would hold a start for N x 5 s;
    each add writes only its own device's keys, so gathering them is safe. A
    device that fails, to be split into its batch or to be added, goes to
    ``on_failure(device_id, error)``, and the rest are still added.
    """
    batches, unsplit = bridges_first(resolved)
    for did, error in unsplit.items():
        on_failure(did, error)
    for batch in batches:
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
    (``needs_readd``), and each its driver could not set up, which every sync
    tries again; ``update_device`` is remove + add."""
    changed_ids = [
        did for did in device_ids
        if devices.build_failed(did)
        or needs_readd(devices.get_device_config(did) or {}, project_devices[did])
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
