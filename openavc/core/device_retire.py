"""Ending the work that holds a device's instance before the instance goes:
the part of DeviceManager behind ``_retire``, and the rebuild a file command
holds back.

An instance goes in three ways: the device is removed (``remove_device``), a
new instance replaces it (an edit, a driver reload, a re-add:
``_replace_device``), and the system stops (``disconnect_all``). Each passes
``_retire`` first, with why (a ``RETIRED_*`` reason).

The rule worth knowing before changing it: work that holds an instance is
ended here, by instance, before anything about the instance changes. Today
that work is three things. A queued setting's read-back watcher
(core/pending_settings.py) watches the instance its setting was sent to, and
is ended first, at once, so it can never judge an instance being closed: the
setting stays queued for the instance that runs the device next. A setup run
(core/setup_actions.py) holds the instance it started on: its settings, its
reconnect and its hand-back go to that instance only, and the runner says the
reason to the wizard whose run it ends. A file command's run
(core/command_runs.py) is ended the same way, with why.

A file run is the one piece of work a replacement waits for instead of
ending: a firmware image cut off half way can leave the device unusable. So
while one runs, a replacement of its device is held (``_hold_rebuild``, asked
first by ``_replace_device``): the change is taken at once, the run goes on
through the instance it started on, and the device is rebuilt with the change
when the run ends, however it ends (``file_run_ended``). Only the newest held
change is kept; a driver reload asks with the running settings, so it keeps a
held change's settings and picks up the reloaded driver with them. Removing
the device or stopping the system drops what is held and ends the run.

Mixed into DeviceManager (core/device_manager.py).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.drivers.base import BaseDriver

log = get_logger(__name__)

# Why an instance's work is ended: the setup runner and the file runs word
# each for the page whose run it ends.
RETIRED_REMOVED = "removed"
RETIRED_REPLACED = "replaced"
RETIRED_STOPPING = "stopping"


class FileRuns(Protocol):
    """What the retire step asks of the file runs (core/command_runs.py)."""

    def hold_change(self, device_id: str) -> bool:
        """True, and the run says a change is waiting, when a file run is
        going on the device."""

    async def end(self, device_id: str, why: str) -> None:
        """End the device's file run with why (``RETIRED_*``), and wait for it."""


class DeviceRetireMixin:
    """THE retire step, for DeviceManager (see the module docstring).

    Its state is set up by ``_init_retire``. From its host it reads
    ``_devices`` and ``_device_configs``, and asks ``_cancel_pending_confirm``
    (core/pending_settings.py), ``device_record`` and ``_set_up_again``.
    """

    _devices: dict[str, BaseDriver]
    _device_configs: dict[str, dict[str, Any]]

    def _init_retire(self) -> None:
        # Set by the engine to SetupActionRunner.end_run: given a device id,
        # the instance being retired and why (RETIRED_*), ends a setup run
        # holding that instance and waits for it. None in a bare manager.
        self.end_setup_run: Callable[[str, Any, str], Awaitable[None]] | None = None
        # Set by the engine to its CommandRuns. None in a bare manager.
        self.file_runs: FileRuns | None = None
        # Device id -> the settings a replacement held for a file run asked for.
        self._held_rebuilds: dict[str, dict[str, Any]] = {}

    async def _retire(self, device_id: str, why: str) -> None:
        """End the work holding the device's live instance, before it goes:
        its read-back watcher before anything here waits, then its setup run
        and its file run, each waited for."""
        driver = self._devices.get(device_id)
        if driver is None:
            return
        self._cancel_pending_confirm(device_id)
        self._held_rebuilds.pop(device_id, None)
        if self.end_setup_run is not None:
            await self.end_setup_run(device_id, driver, why)
        if self.file_runs is not None:
            await self.file_runs.end(device_id, why)

    async def _retire_all(self, why: str) -> None:
        """:meth:`_retire` every live instance (the stop)."""
        for device_id in list(self._devices):
            await self._retire(device_id, why)

    # --- the rebuild a file run holds ---------------------------------------

    def _hold_rebuild(self, device_id: str, config: dict[str, Any]) -> bool:
        """True when a file run is going on the device's live instance: its
        replacement with ``config`` is held until the run ends. Decided with
        no wait, so the run cannot end between the question and the hold."""
        if device_id not in self._devices or self.file_runs is None:
            return False
        if not self.file_runs.hold_change(device_id):
            return False
        held = self._held_rebuilds.get(device_id)
        if held is None or config != self._device_configs.get(device_id):
            self._held_rebuilds[device_id] = config
        log.info(
            f"Device '{device_id}' changed while a file command runs on it; "
            "the change applies when it finishes"
        )
        return True

    async def file_run_ended(self, device_id: str) -> None:
        """A file run on the device ended: apply the change it held, if any."""
        config = self._held_rebuilds.pop(device_id, None)
        if config is None or self.device_record(device_id) is None:
            return
        try:
            await self._set_up_again(config)
        except Exception:
            log.exception(f"Applying the change held for '{device_id}' failed")
