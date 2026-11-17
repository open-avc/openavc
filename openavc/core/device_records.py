"""The device manager's one record per project device: which devices the
system has, and in what state. Every reader lists and counts from it.

A project device is in one of five states (``DeviceRecord.status``, and the
``status`` of each device in the listing and in a device's details):

- ``running``: a driver runs it, connected or not.
- ``disabled``: the project turns it off, so nothing runs it.
- ``orphaned``: its driver is not installed (core/device_orphans.py).
- ``driver_failed``: its driver raised setting it up
  (core/device_build_failures.py).
- ``settings_failed``: its settings could not be worked out, because its
  driver's declarations or its connection row hold a value of the wrong kind
  (core/device_sync.py, ``UnresolvedDevice``). Kept here with
  ``offline_reason`` ``invalid_config`` and why in ``offline_detail``, and
  worked out again at every device sync, by Reconnect and when its driver is
  reloaded (through ``work_out``, which the engine sets).

The rule worth knowing before changing it: a running device is one with a live
driver (``_devices``), and every other state is written into ``_not_running``
by the door that decided it (``add_device``'s disabled path,
``_register_orphan``, ``_keep_build_failure``, ``keep_unresolved``) and dropped
when the device is taken down (``remove_device``, or a replacement). Readers ask ``device_record``, ``device_records`` and
``runs``, never the stores, so "disabled" is decided once, where the
project's switch is read, and never as whatever is left over.

A device whose settings could not be worked out has no resolved record, so it
is the one state kept out of ``_device_configs``: its record holds the
project's id, name, driver and enabled alone, and it is never simulated or
dialled.

Mixed into DeviceManager (core/device_manager.py). Logs under the device
manager's name, which is how the log view files a line as the device's.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from openavc.core.connection_fault import (
    INVALID_CONFIG,
    ConnectionFault,
    card_detail,
    simulator_error_text,
)
from openavc.core.device_errors import DriverBuildError
from openavc.core.installed_equipment import missing_driver_reason
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.device_sync import UnresolvedDevice
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver

log = get_logger("openavc.core.device_manager")

RUNNING = "running"
DISABLED = "disabled"
ORPHANED = "orphaned"
DRIVER_FAILED = "driver_failed"
SETTINGS_FAILED = "settings_failed"

# The listing's order: the running devices, then each other state in turn.
_LISTING_ORDER = (RUNNING, ORPHANED, DRIVER_FAILED, SETTINGS_FAILED, DISABLED)


@dataclass(frozen=True)
class DeviceRecord:
    """One project device as the device manager has it.

    ``config`` is the record the device was handed (what
    ``resolve_device_config`` answers: id, name, driver, enabled, config and
    the rest), except for ``settings_failed``, whose settings could not be
    worked out: then the project's id, name, driver and enabled alone.
    ``reason`` is why a device that failed is not running."""

    device_id: str
    status: str
    config: dict[str, Any]
    reason: ConnectionFault | None = None

    @property
    def resolved(self) -> bool:
        """True when ``config`` is a worked-out record: every state but
        ``settings_failed``."""
        return self.status != SETTINGS_FAILED

    @property
    def connects(self) -> bool:
        """True when the record says where the device connects: every state
        but ``disabled`` and ``settings_failed``. A running one connects
        there now; one waiting for its driver, or for its driver to set it
        up, will when it can."""
        return self.resolved and self.status != DISABLED


def settings_failed_fault(
    driver_id: str, error: BaseException, *, product: bool = False,
) -> ConnectionFault:
    """A device whose settings could not be worked out, so nothing runs it.

    THE wording of it, with the first line of what raised. The value of the
    wrong kind can be in the device's own settings or in what its driver
    declares, so both are named; ``product`` is manufacturer-owned equipment,
    whose installed integration the programmer cannot edit, so it is never
    told to change the driver.
    """
    because = f" ({card_detail(simulator_error_text(error.__cause__ or error))})"
    if product or not driver_id:
        return ConnectionFault(INVALID_CONFIG, f"OpenAVC could not work out this device's settings{because}.")
    return ConnectionFault(INVALID_CONFIG, (
        f"OpenAVC could not work out this device's settings{because}. Check them "
        f"under Edit, or fix the driver '{driver_id}'."
    ))


class DeviceRecordsMixin:
    """The record of every project device, and keeping one whose settings
    could not be worked out.

    Its state is set up by ``_init_records``. From its host (DeviceManager)
    it reads ``state``, ``installed_equipment``, ``request_bringup``,
    ``_devices`` and ``_device_configs``, and asks ``_replace_device``,
    ``_take_down`` and ``_publish_offline`` (core/device_away.py).
    """

    state: StateStore
    installed_equipment: Any
    request_bringup: Callable[[], None] | None
    _devices: dict[str, BaseDriver]
    _device_configs: dict[str, dict[str, Any]]

    def _init_records(self) -> None:
        # Device id -> the record of a device no driver runs.
        self._not_running: dict[str, DeviceRecord] = {}
        # Set by the engine: one project device worked out again by its id,
        # as a start or a save works each out (core/device_sync.py
        # ``work_out``). Raises UnresolvedDevice when it still cannot be, and
        # DeviceNotFoundError when the project has no such device. None in a
        # bare manager, which then leaves such a device as it is.
        self.work_out: Callable[[str], dict[str, Any]] | None = None

    # --- reading the record ---------------------------------------------

    def device_record(self, device_id: str) -> DeviceRecord | None:
        """THE answer to whether the system has this device, and in what
        state; None when it has no device by that id."""
        if device_id in self._devices:
            config = self._device_configs.get(device_id) or {"id": device_id}
            return DeviceRecord(device_id, RUNNING, config)
        return self._not_running.get(device_id)

    def device_records(self) -> list[DeviceRecord]:
        """Every device the system has, in the listing's order: the running
        ones, then each other state in turn."""
        rest = sorted(self._not_running.values(), key=lambda r: _LISTING_ORDER.index(r.status))
        return [DeviceRecord(did, RUNNING, self._device_configs.get(did) or {"id": did})
                for did in self._devices] + rest

    def runs(self, device_id: str) -> bool:
        """True while a driver runs this device, connected or not."""
        return device_id in self._devices

    def status_of(self, device_id: str) -> str | None:
        """The device's state, or None when the system has no such device."""
        record = self.device_record(device_id)
        return record.status if record is not None else None

    def why_not_running(self, device_id: str) -> tuple[str, str] | None:
        """Why no driver runs a device the project has not turned off: its
        state (``orphaned``, ``driver_failed`` or ``settings_failed``) and
        the sentence its card shows. None while a driver runs it, while the
        project turns it off, and when the system has no such device."""
        record = self.device_record(device_id)
        if record is None or record.status in (RUNNING, DISABLED):
            return None
        if record.reason is not None:
            return record.status, record.reason.message
        return record.status, missing_driver_reason(str(record.config.get("driver") or ""))

    def _records_in(self, status: str) -> dict[str, dict[str, Any]]:
        """Each device in ``status`` that no driver runs: id -> its config."""
        return {did: r.config for did, r in self._not_running.items() if r.status == status}

    # --- writing the record ---------------------------------------------

    def _keep_record(
        self, device_id: str, status: str, config: dict[str, Any],
        reason: ConnectionFault | None = None,
    ) -> None:
        """Record a device no driver runs, in ``status``. Its config is kept
        with every other device's, for the reconcile to compare, unless its
        settings could not be worked out."""
        self._not_running[device_id] = DeviceRecord(device_id, status, config, reason)
        if status == SETTINGS_FAILED:
            self._device_configs.pop(device_id, None)
        else:
            self._device_configs[device_id] = config

    def _forget_record(self, device_id: str) -> None:
        """Drop a device's record, for a device nothing ran."""
        self._not_running.pop(device_id, None)
        self._device_configs.pop(device_id, None)

    async def keep_unresolved(self, failure: UnresolvedDevice) -> bool:
        """Keep a project device whose settings could not be worked out,
        listed and counted with why on its card. Whatever the device was
        before is replaced, except a device a driver runs: that one keeps
        running with the settings it has, since it is still in the project
        and nothing better is known, and False is returned. One kept already
        for the same reason is left as it is."""
        device_id = failure.device_id
        if self.runs(device_id):
            return False
        entry = failure.entry
        product = bool(self.installed_equipment and self.installed_equipment.owns(device_id))
        fault = settings_failed_fault(str(entry.get("driver") or ""), failure, product=product)
        kept = DeviceRecord(device_id, SETTINGS_FAILED, entry, fault)
        if self._not_running.get(device_id) == kept:
            return True
        if device_id in self._not_running:
            # The same device, kept in another state: what it was is taken
            # down, never forgotten (core/device_manager.py _replace_device).
            await self._take_down(device_id)
        self._keep_record(device_id, SETTINGS_FAILED, entry, fault)
        prefix = f"device.{device_id}."
        self.state.set(f"{prefix}name", entry.get("name") or device_id, source="config")
        self.state.set(f"{prefix}enabled", entry.get("enabled", True) is not False, source="config")
        self.state.set(f"{prefix}connected", False, source="device_manager")
        self.state.set(f"{prefix}reconnect_failed", True, source="device_manager")
        self._publish_offline(device_id, None, fault)
        return True

    # --- trying again ---------------------------------------------------

    async def _retry_settings(self, device_id: str) -> bool:
        """Reconnect, or a reload of its driver, for a device whose settings
        could not be worked out: work them out again and set it up, leaving
        dialing it to a bring-up round. One that still cannot be is kept with
        the new reason. True when a driver runs it after."""
        from openavc.core.device_sync import UnresolvedDevice

        if self.work_out is None:
            return False
        try:
            config = self.work_out(device_id)
        except UnresolvedDevice as failure:
            await self.keep_unresolved(failure)
            return False
        return await self._set_up_again(config)

    async def _retry_settings_on(self, driver_id: str) -> list[str]:
        """Work out again every device on ``driver_id`` whose settings could
        not be worked out; the ids a driver runs after."""
        waiting = [did for did, entry in self._records_in(SETTINGS_FAILED).items()
                   if entry.get("driver") == driver_id]
        return [did for did in waiting if await self._try_settings(did)]

    async def _try_settings(self, device_id: str) -> bool:
        """:meth:`_retry_settings`, with a failure logged as False, so one
        device never costs the others their retry."""
        try:
            return await self._retry_settings(device_id)
        except Exception:
            log.exception(f"Failed to work out device '{device_id}' again")
            return False

    async def _set_up_again(self, config: dict[str, Any]) -> bool:
        """Set a device no driver runs up again from ``config`` (a
        replacement, so it keeps what one keeps), leaving dialing it to a
        bring-up round when one can be asked for. One its driver cannot set
        up is kept with why. True when a driver runs it after."""
        device_id = config["id"]
        ask_for_round = self.request_bringup is not None
        try:
            await self._replace_device(device_id, config, defer_connect=ask_for_round)
        except DriverBuildError:
            return False
        if ask_for_round:
            self.request_bringup()
        return self.runs(device_id)
