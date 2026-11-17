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

How the device is described (listed, its details) is core/device_info.py.
Mixed into DeviceManager (core/device_manager.py). Logs under the device
manager's name, which is how the log view files a line as the device's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from openavc.core.connection_fault import (
    DRIVER_FAILED,
    ConnectionFault,
    card_detail,
    simulator_error_text,
)
from openavc.core.device_errors import DriverBuildError
from openavc.core.device_logins import SAVED_LOGIN
from openavc.core.installed_equipment import driver_label
from openavc.utils.log_redaction import (
    collect_identity_values,
    collect_secret_values,
    get_secret_registry,
)
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
    # The marker is not a login, and is masked nowhere.
    config = {k: v for k, v in config.items() if v != SAVED_LOGIN}
    get_secret_registry().set_config_secrets(
        device_id,
        collect_secret_values(config, schema),
        identities=collect_identity_values(config, schema),
    )


class DeviceBuildFailuresMixin:
    """Building a device's driver, keeping a device its driver could not set
    up, and trying it again.

    Its state is set up by ``_init_build_failures``. From its host
    (DeviceManager) it reads ``state``, ``events``, ``installed_equipment``,
    ``_device_configs`` and ``request_bringup``, and asks ``add_device``,
    ``remove_device`` and ``_publish_offline`` (core/device_away.py).
    """

    state: StateStore
    events: EventBus
    installed_equipment: Any
    _device_configs: dict[str, dict[str, Any]]
    request_bringup: Callable[[], None] | None

    def _init_build_failures(self) -> None:
        # Device id -> the device config its driver could not set up.
        self._build_failures: dict[str, dict[str, Any]] = {}

    def build_failed(self, device_id: str) -> bool:
        """True while ``device_id`` is kept because its driver could not set
        it up."""
        return device_id in self._build_failures

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
        self._build_failures[device_id] = device_config
        self._device_configs[device_id] = device_config
        prefix = f"device.{device_id}."
        for key in self.state.get_namespace(prefix):
            self.state.delete(f"{prefix}{key}")
        name = device_config.get("name", device_id)
        self.state.set(f"{prefix}name", name, source="config")
        self.state.set(f"{prefix}enabled", True, source="config")
        self.state.set(f"{prefix}connected", False, source="device_manager")
        self.state.set(f"{prefix}reconnect_failed", True, source="device_manager")
        product = bool(self.installed_equipment and self.installed_equipment.owns(device_id))
        self._publish_offline(device_id, None, driver_failed_fault(driver_id, error, product=product))

    async def _retry_build(self, device_id: str) -> None:
        """Reconnect for a device its driver could not set up: set it up
        again, and leave dialing it to a bring-up round. One that fails again
        is kept with the new error, which is what the device page shows."""
        config = self._build_failures[device_id]
        ask_for_round = self.request_bringup is not None
        await self.remove_device(device_id)
        try:
            await self.add_device(config, defer_connect=ask_for_round)
        except DriverBuildError:
            return
        if ask_for_round:
            self.request_bringup()
