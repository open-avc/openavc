"""Devices waiting for their driver: the part of DeviceManager that keeps a
device whose driver is not installed, and activates it when the driver arrives.

A project can name a driver this system does not have (a project brought from
another system, a community driver not installed yet). Such a device is an
orphan: its record is kept, ``device.<id>.orphaned`` and ``orphan_reason`` say
why it waits (core/installed_equipment.py words the reason), and it connects to
nothing. ``retry_all_orphans``, run after the driver loader, and
``reload_driver`` activate the ones whose driver is now registered.

The rule worth knowing before changing it: activating an orphan registers the
device and leaves dialing it to a bring-up round. The round puts a running
simulation's redirect in place first, so a device activated while simulating
reaches the simulator on its first attempt, never its real address.

How an orphan is described (its summary, its config with every login masked) is
core/device_info.py. Mixed into DeviceManager (core/device_manager.py). Logs
under the device manager's name, which is how the log view files a line as the
device's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from openavc.core.device_errors import DeviceNotFoundError, DeviceNotOrphanedError
from openavc.core.device_records import ORPHANED
from openavc.core.installed_equipment import missing_driver_reason
from openavc.drivers.registry import is_driver_registered
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

log = get_logger("openavc.core.device_manager")


class DeviceOrphansMixin:
    """Keeping a device whose driver is missing, and activating it later.

    Holds no state of its own: an orphan is a record in the ``orphaned``
    state (core/device_records.py). From its host (DeviceManager) it reads
    ``state``, ``events`` and ``request_bringup``, and asks
    ``_replace_device``, ``device_record``, ``status_of``, ``_records_in``,
    ``_keep_record`` and ``_set_device_state`` (core/device_away.py).
    """

    state: StateStore
    events: EventBus
    request_bringup: Callable[[], None] | None

    async def _register_orphan(
        self, device_id: str, device_config: dict[str, Any], name: str,
    ) -> None:
        """Keep a device whose driver is not installed, and say why it waits.
        It activates when the driver arrives (retry_all_orphans)."""
        driver_id = device_config["driver"]
        log.warning(f"Device '{device_id}' is orphaned: {missing_driver_reason(driver_id)}")
        self._keep_record(device_id, ORPHANED, device_config)
        self.state.set(f"device.{device_id}.name", name, source="config")
        self._set_device_state(device_id, "connected", False, source="config")
        self._set_device_state(device_id, "orphaned", True, source="config")
        self.state.set(
            f"device.{device_id}.orphan_reason",
            missing_driver_reason(driver_id),
            source="config",
        )
        await self.events.emit("device.orphaned", {"device_id": device_id, "driver": driver_id})

    async def retry_orphaned_device(
        self, device_id: str, *, defer_connect: bool = False
    ) -> bool:
        """Re-attempt adding an orphaned device (e.g., after installing its driver).

        Returns True if the device was successfully activated, False if still
        orphaned. "Activated" means registered: the connection is opened by
        the bring-up round asked for below, not before this returns, so a
        caller that needs the device on the wire awaits
        ``Engine.wait_for_device_bringup``.
        """
        config = self._orphan_record(device_id)
        driver_id = config.get("driver", "")

        # Check if the driver is now available
        if not is_driver_registered(driver_id):
            return False

        # Remove from orphan tracking and re-add normally.
        #
        # Registered, not dialed, whenever a bring-up round can open it: the
        # round re-applies the simulation redirect to the new instance before
        # anything is dialed, so a device activated while simulation is
        # running reaches the simulator on its FIRST attempt. Dialing here
        # sent it to its REAL address and left it reconnect-looping against
        # hardware that is not there, with no recovery an integrator could
        # guess (``/api/simulation/start`` refuses, "already active"; a
        # reconnect re-dials the real address). Same rule as reload_driver,
        # for the same reason.
        #
        # A caller that asked for the deferral owns its own round (the engine's
        # reconcile does); one that did not gets it asked for here, or the
        # device would sit registered and never dialed.
        #
        # It is the same device on a fresh instance, so it keeps what a
        # replacement keeps (a sleep recorded before a restart, its queue).
        ask_for_round = not defer_connect and self.request_bringup is not None
        await self._replace_device(
            device_id, config, defer_connect=defer_connect or ask_for_round,
        )
        activated = self.status_of(device_id) != ORPHANED
        if activated and ask_for_round:
            self.request_bringup()
        return activated

    def _orphan_record(self, device_id: str) -> dict[str, Any]:
        """An orphaned device's record.

        The two ways to ask about the wrong device are separate answers,
        because the door above them is: an id nobody has is not found, while a
        device that is running perfectly well has nothing to retry. Each has
        its own type, because the retry that follows builds the device, and a
        driver's constructor can raise a ValueError of its own.
        """
        record = self.device_record(device_id)
        if record is None:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        if record.status != ORPHANED:
            raise DeviceNotOrphanedError(device_id)
        return record.config

    async def retry_all_orphans(self, *, defer_connect: bool = False) -> list[str]:
        """Promote every orphan whose driver is now in the registry.

        Called after the driver loader runs (project reload, community
        install) so devices that were stuck in orphan state because their
        driver wasn't loaded yet come online without a server restart.
        Returns the list of device IDs that successfully activated.
        """
        return await self._activate_orphans(
            is_driver_registered, defer_connect=defer_connect,
            note="(driver '{driver}' now installed)",
        )

    async def _activate_orphans(
        self, wanted: Callable[[str], bool], *, defer_connect: bool = False, note: str,
    ) -> list[str]:
        """Activate every orphan waiting for a driver id ``wanted`` accepts.

        The ids activated. One that fails is logged and the rest still run.
        ``note`` ends each "Activated" log line, with ``{driver}`` filled in.
        """
        activated: list[str] = []
        # A snapshot: activating one changes the records.
        for device_id, config in self._records_in(ORPHANED).items():
            driver_id = config.get("driver", "")
            if wanted(driver_id) and await self._try_orphan(device_id, defer_connect):
                activated.append(device_id)
                log.info(f"Activated orphaned device '{device_id}' " + note.format(driver=driver_id))
        return activated

    async def _try_orphan(self, device_id: str, defer_connect: bool) -> bool:
        """:meth:`retry_orphaned_device`, with a failure logged as False."""
        try:
            return await self.retry_orphaned_device(device_id, defer_connect=defer_connect)
        except Exception:
            log.exception(f"Failed to activate orphaned device '{device_id}'")
            return False

    def get_missing_drivers(self) -> list[str]:
        """Return the unique driver IDs that orphaned devices are waiting for."""
        seen: set[str] = set()
        result: list[str] = []
        for cfg in self._records_in(ORPHANED).values():
            driver_id = cfg.get("driver", "")
            if driver_id and driver_id not in seen:
                seen.add(driver_id)
                result.append(driver_id)
        return result
