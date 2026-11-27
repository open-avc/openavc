"""Devices whose driver could not set them up: the part of DeviceManager that
builds a device's driver, keeps the device when the driver raises, and tries
it again.

A driver can be installed and still fail to set a device up: its constructor
raises on a declaration it cannot read, or on the device's settings. Such a
device is kept: listed and counted like every other, connecting to nothing,
with ``device.<id>.offline_reason`` ``driver_failed`` and the driver's error
in ``offline_detail`` (worded here, ``driver_failed_fault``),
and ``reconnect_failed`` set, because nothing heals it on its own. It is tried
again when its driver is reloaded (``reload_driver``), at every device sync
(``device_sync.update_changed_devices``), by an edit, and by Reconnect, and
its removal clears it like any device.

The rule worth knowing before changing it: what the driver's constructor
created before it raised (a state key for every declared reading, the
device's logins registered for masking) belongs to the record. Its keys are
cleared before the record publishes its own, and its logins are registered
again from the device's settings, since a constructor can raise before it
registers them and the error's text can carry one.

Its record (the ``driver_failed`` state, with the fault as its reason) is
core/device_records.py; how the device is described (listed, its details) is
core/device_info.py.
Mixed into DeviceManager (core/device_manager.py). Logs under the device
manager's name, which is how the log view files a line as the device's.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openavc.core import device_records
from openavc.core.connection_fault import (
    DRIVER_FAILED,
    ConnectionFault,
    card_detail,
    simulator_error_text,
)
from openavc.core.device_errors import DriverBuildError
from openavc.core.installed_equipment import driver_label
from openavc.utils.log_redaction import register_config_secrets
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver

log = get_logger("openavc.core.device_manager")


def driver_failed_fault(
    driver_id: str, error: BaseException, *, product: bool = False,
) -> ConnectionFault:
    """A device whose driver raised while setting it up, so nothing runs it.

    THE wording of it, with the error's first line (its type, and its text
    when it has any). ``product`` is manufacturer-owned equipment, whose
    installed integration the programmer cannot edit, so it is never told to
    change the driver.
    """
    because = f" ({card_detail(simulator_error_text(error))})"
    if product or not driver_id:
        return ConnectionFault(DRIVER_FAILED, f"OpenAVC could not set up this device{because}.")
    return ConnectionFault(DRIVER_FAILED, (
        f"The driver '{driver_id}' could not set up this device{because}. Fix the "
        f"driver, or check this device's settings under Edit."
    ))


def _register_logins(device_id: str, config: dict[str, Any], driver_class: type) -> None:
    """Register the device's logins for masking, as ``BaseDriver`` does at
    the end of its constructor (``_register_config_secrets``), for a
    constructor that raised before it got there."""
    schema = (getattr(driver_class, "DRIVER_INFO", None) or {}).get("config_schema")
    register_config_secrets(device_id, config, schema)


class DeviceBuildFailuresMixin:
    """Building a device's driver, keeping a device its driver could not set
    up, and trying it again.

    Holds no state of its own: such a device is a record in the
    ``driver_failed`` state (core/device_records.py). From its host
    (DeviceManager) it reads ``state``, ``events`` and
    ``installed_equipment``, and asks ``status_of``, ``_keep_record``,
    ``_set_up_again``, ``_set_device_state`` and ``_publish_offline``
    (core/device_away.py).
    """

    state: StateStore
    events: EventBus
    installed_equipment: Any

    def build_failed(self, device_id: str) -> bool:
        """True while ``device_id`` is kept because its driver could not set
        it up."""
        return self.status_of(device_id) == device_records.DRIVER_FAILED

    def _build_driver(
        self, device_config: dict[str, Any], driver_class: type, config: dict[str, Any],
    ) -> BaseDriver:
        """Construct the device's driver from ``config`` (its own copy) and
        hand it the project's child entities. When either raises, the device
        is kept with why and :class:`DriverBuildError` is raised."""
        device_id = device_config["id"]
        try:
            driver = driver_class(device_id, config, self.state, self.events)
            driver.set_project_child_entities(device_config.get("child_entities") or {})
        except Exception as error:  # Catch-all: whatever a driver raises, the device is kept with it
            self._keep_build_failure(device_config, driver_class, config, error)
            raise DriverBuildError(
                device_id, driver_label(device_config["driver"]), simulator_error_text(error),
            ) from error
        return driver

    def _keep_build_failure(
        self,
        device_config: dict[str, Any],
        driver_class: type,
        config: dict[str, Any],
        error: BaseException,
    ) -> None:
        device_id = device_config["id"]
        driver_id = device_config["driver"]
        # First, so every line about this failure has the device's logins masked.
        _register_logins(device_id, config, driver_class)
        log.error(
            f"The {driver_label(driver_id)} could not set up device '{device_id}': "
            f"{simulator_error_text(error)}",
            exc_info=error,
        )
        product = bool(self.installed_equipment and self.installed_equipment.owns(device_id))
        fault = driver_failed_fault(driver_id, error, product=product)
        self._keep_record(device_id, device_records.DRIVER_FAILED, device_config, fault)
        prefix = f"device.{device_id}."
        for key in self.state.get_namespace(prefix):
            self.state.delete(f"{prefix}{key}")
        name = device_config.get("name", device_id)
        self.state.set(f"{prefix}name", name, source="config")
        self._set_device_state(device_id, "enabled", True, source="config")
        self._set_device_state(device_id, "connected", False)
        self.state.set(f"{prefix}reconnect_failed", True, source="device_manager")
        self._publish_offline(device_id, None, fault)

    async def _retry_build(self, device_id: str) -> bool:
        """Reconnect for a device its driver could not set up: set it up
        again, and leave dialing it to a bring-up round. One that fails again
        is kept with the new error, which is what the device page shows.
        True when a driver runs it after."""
        return await self._set_up_again(self.device_record(device_id).config)
