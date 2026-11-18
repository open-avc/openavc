"""Where a device's queue of settings waiting for it is kept while the engine
runs: in the project, each write a change through the change door.

THE keeper of the queue in the project is ``ProjectQueue``, which the engine
gives its device manager (``DeviceManager.keep_queue_with``). Its two doors
are the only writers of a device's ``pending_settings`` besides a whole
project that comes in. The intake (``add``) merges the checked values into
the project's queue under the change lock and saves it, and the running
queue takes them once the file holds them; a refused or failed save raises
and the running queue never had them. A clear (``drop``) removes from the
project's queue each entry the pass took that is still the value it took,
never the queue as it was when the pass ran, so a newer value queued before
the clear is saved survives it; the running queue drops the same entries
once the file holds the change, so a clear whose save fails leaves the entry
queued in both, sent again on the next connect and cleared then.

A clear can be made while a project change holds the lock (a device
connects inside a reconcile), so it is always queued
(``schedule_bookkeeping_change``); the intake comes from a request and waits
for the lock. Which entries a pass took, and the rule that drops them, are
``core/pending_settings.py``'s.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from openavc.core.device_errors import DeviceNotFoundError
from openavc.core.pending_settings import PendingClear, remaining_after
from openavc.core.project_changes import ProjectNotAppliedError
from openavc.core.project_loader import ProjectSaveError
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.engine import Engine

log = get_logger("openavc.core.engine")


def _device_in(project: Any, device_id: str) -> Any:
    """The project's device by ``device_id``, or None."""
    return next((device for device in project.devices if device.id == device_id), None)


def _add_to_project(device_id: str, settings: dict[str, Any], project: Any) -> None:
    device = _device_in(project, device_id)
    if device is None:
        raise DeviceNotFoundError(f"Device '{device_id}' not found")
    device.pending_settings = {**(device.pending_settings or {}), **settings}


def _drop_from_project(clear: PendingClear, project: Any) -> None:
    # Never raises: a queued change fails with everything queued beside it,
    # and a device removed since has no queue left to clear.
    device = _device_in(project, clear.device_id)
    if device is not None and device.pending_settings:
        device.pending_settings = remaining_after(device.pending_settings, clear.taken)


class ProjectQueue:
    """The device manager's queue keeper while the engine runs: every queue
    kept in the project first, then in the running system."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def add(self, device_id: str, settings: dict[str, Any]) -> None:
        """Queue checked ``settings`` for the device: saved to the project,
        then taken by the running queue. A refusal or a failed save raises
        (a failed save as ``ProjectNotAppliedError``) with nothing queued."""
        engine = self._engine
        try:
            await engine.persist_bookkeeping_change(
                partial(_add_to_project, device_id, settings),
                runtime_write=partial(engine.devices.take_pending, device_id, settings),
            )
        except ProjectSaveError as failure:
            raise ProjectNotAppliedError(failure) from failure
        # A script may wait on this event for a saved change, and queuing a
        # setting has always sent it; the bookkeeping door does not.
        await engine.events.emit("system.project.reloaded")

    async def drop(self, clear: PendingClear) -> None:
        """Clear the entries ``clear`` took, queued to run once the change
        lock is free: the project's first, then the running queue's, then
        ``device.pending_settings_applied`` says what it cleared."""
        devices = self._engine.devices
        self._engine.schedule_bookkeeping_change(
            partial(_drop_from_project, clear),
            runtime_write=partial(devices.drop_taken, clear),
            on_saved=partial(devices.announce_cleared, clear),
        )
