"""Ending the work that holds a device's instance before the instance goes:
the part of DeviceManager behind ``_retire``.

An instance goes in three ways: the device is removed (``remove_device``), a
new instance replaces it (an edit, a driver reload, a re-add:
``_replace_device``), and the system stops (``disconnect_all``). Each passes
``_retire`` first, with why (a ``RETIRED_*`` reason).

The rule worth knowing before changing it: work that holds an instance is
ended here, by instance, before anything about the instance changes. Today
that work is a setup run (core/setup_actions.py), which holds the instance it
started on: its settings, its reconnect and its hand-back go to that
instance only, and the runner says the reason to the wizard whose run it
ends.

Mixed into DeviceManager (core/device_manager.py).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openavc.drivers.base import BaseDriver

# Why an instance's work is ended: the setup runner words each for the wizard.
RETIRED_REMOVED = "removed"
RETIRED_REPLACED = "replaced"
RETIRED_STOPPING = "stopping"


class DeviceRetireMixin:
    """THE retire step, for DeviceManager (see the module docstring).

    Its state is set up by ``_init_retire``. From its host it reads
    ``_devices``.
    """

    _devices: dict[str, BaseDriver]

    def _init_retire(self) -> None:
        # Set by the engine to SetupActionRunner.end_run: given a device id,
        # the instance being retired and why (RETIRED_*), ends a setup run
        # holding that instance and waits for it. None in a bare manager.
        self.end_setup_run: Callable[[str, Any, str], Awaitable[None]] | None = None

    async def _retire(self, device_id: str, why: str) -> None:
        """End the work holding the device's live instance, before it goes."""
        driver = self._devices.get(device_id)
        if driver is not None and self.end_setup_run is not None:
            await self.end_setup_run(device_id, driver, why)

    async def _retire_all(self, why: str) -> None:
        """:meth:`_retire` every live instance (the stop)."""
        for device_id in list(self._devices):
            await self._retire(device_id, why)
