"""
Setup-action runner — the platform side of driver-declared provisioning wizards.

A setup action (an action of ``kind:"setup"``, see ``openavc/drivers/actions.py``)
is a driver-declared wizard that can run while a device is offline, brings its
own out-of-band transport, reports multi-step progress, and may rewrite the
device's connection config and reconnect on success. This module orchestrates a
run generically — it never knows what an action *does*:

  - suppress auto-reconnect for the duration (so it can't race the handler's own
    transport), then re-enable it afterward;
  - install a per-run context that backs ``driver.request_config_update`` /
    ``driver.request_reconnect``;
  - stream the handler's ``progress(step, pct)`` calls to the UI as
    ``action.progress`` WebSocket events, plus a final ``done`` / ``error``
    event — a handler that returns ``{"success": False, ...}`` terminates as
    ``error`` just like one that raises, so ``status`` is the whole answer to
    "did this run work" and no client has to know the return convention;
  - run as a background task so a client disconnecting mid-flight can't abort a
    hardware-mutating provisioning step;
  - hold the driver instance it started on (``SetupLease``): its settings,
    its reconnect and the hand-back at its end go to that instance only, and
    the device manager's retire step (core/device_retire.py: a removal,
    an edit or driver reload that builds a new instance, the system
    stopping) ends the run before the instance goes, through
    :meth:`SetupActionRunner.end_run`. A handler that ignores the cancel has
    its later writes and reconnects refused;
  - keep each device's last run result in memory, so a wizard whose
    connection dropped asks how its run ended (:meth:`SetupActionRunner.outcome`).
    A run id names the process that started it: a run of an earlier process
    answers that it was interrupted.

All device/protocol specifics (which transport, which commands, the config
delta) live in the driver's ``run_setup_action`` handler.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from uuid import uuid4

from openavc.core.client_origin import client_origin, request_client_id
from openavc.core.device_config import apply_config_delta
from openavc.core.device_manager import DeviceNotFoundError
from openavc.core.device_retire import RETIRED_REMOVED, RETIRED_REPLACED, RETIRED_STOPPING
from openavc.core.project_changes import ProjectNotAppliedError
from openavc.core.project_loader import ProjectSaveError
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# How long the retire step waits for a run it cancelled to finish. A handler
# that ignores the cancel is let go after this; its writes are refused.
RUN_END_SECONDS = 5.0
# How many devices' last run results are kept, oldest dropped first.
KEPT_RESULTS = 256

RUNNING = "running"
DONE = "done"
ERROR = "error"

INTERRUPTED = "The setup was interrupted. Check the device and run it again."
NOT_KEPT = "That setup run has ended and is no longer kept. Check the device."
_ENDED_BECAUSE = {
    RETIRED_REMOVED: "The setup stopped because the device was removed.",
    RETIRED_REPLACED: (
        "The setup stopped because the device's settings or driver changed. Run it again."
    ),
    RETIRED_STOPPING: (
        "The setup stopped because the system is shutting down. "
        "Run it again once the system is back."
    ),
}


def _without_unset_connections(device_id: str, delta: dict[str, Any]) -> dict[str, Any]:
    """``delta`` without a connection field set to ``None``, with a warning
    naming each one dropped (``SetupActionContext.apply_config_update``)."""
    from openavc.core.project_migration import CONNECTION_FIELDS

    unset = sorted(k for k, v in delta.items() if k in CONNECTION_FIELDS and v is None)
    if not unset:
        return delta
    log.warning(
        "[%s] Setup action tried to unset connection field(s) %s — "
        "ignored (unsetting connection fields is not supported)",
        device_id, unset,
    )
    return {k: v for k, v in delta.items() if k not in unset}


class SetupActionInProgress(Exception):
    """Raised when a setup action is requested for a device that already has
    one running (one run per device at a time)."""

    def __init__(self, device_id: str, run_id: str):
        self.device_id = device_id
        self.run_id = run_id
        super().__init__(
            f"A setup action is already running on device '{device_id}'"
        )


class SetupRunEnded(Exception):
    """A setup run's write, reconnect or progress after its device's instance
    was retired: removed, replaced by an edit or a driver reload, or the
    system stopping."""


@dataclass(eq=False)
class SetupLease:
    """A setup run's hold on the driver instance it started on.

    Held while ``ended_by`` is None and the device manager still runs
    ``driver`` under ``device_id``. The retire step sets ``ended_by`` (a
    ``RETIRED_*`` reason) before it cancels the run, so a handler that ignores
    the cancel finds its lease gone. ``saving`` is the run's save once it is
    inside the change door: the retire lets it finish before the cancel.
    """

    device_id: str
    driver: Any
    ended_by: str | None = None
    saving: asyncio.Future | None = None

    def require(self, devices: Any) -> None:
        """Raise :class:`SetupRunEnded` unless the lease is held. An instance
        swapped by a path that did not retire the run ends the lease here."""
        if self.ended_by is None:
            live = devices.get_driver(self.device_id)
            if live is self.driver:
                return
            self.ended_by = RETIRED_REMOVED if live is None else RETIRED_REPLACED
        raise SetupRunEnded(_ENDED_BECAUSE[self.ended_by])


class SetupActionContext:
    """Per-run handle the platform installs on a driver for the duration of a
    ``run_setup_action`` call. Backs ``request_config_update`` /
    ``request_reconnect`` so the handler can persist new connection settings and
    bring the device back online without reaching into platform internals.
    Both go to the instance the run holds (``SetupLease``) and are refused
    once the lease has ended.
    """

    def __init__(self, engine: Any, lease: SetupLease):
        self._engine = engine
        self._lease = lease
        self._device_id = lease.device_id

    async def apply_config_update(self, delta: dict[str, Any]) -> None:
        """Persist a connection/config delta and merge it into the live driver.

        Connection fields (host, port, transport, credentials, …) go to the
        project's connections table; anything else goes to the device's protocol
        config. The live driver instance's ``self.config`` is updated in place so
        the next ``connect()`` uses the new settings — the same instance keeps
        running, so the handler's ``self`` stays valid.

        A bookkeeping change through the change door
        (``Engine.persist_bookkeeping_change``): the delta is written into the
        project current when the save runs, so an edit saved meanwhile is
        kept, and merged into the live driver only once the file holds it,
        under the same lock. No reconcile runs: it would compare the running
        device with the new settings before the merge and restart it under
        the handler. A save that fails leaves the device, the project and
        the file as they were, and is said as every door says a change that
        was not applied.

        A ``None``-valued connection field is ignored (with a warning): the
        connections table never stores None, so honoring it would drop the key
        from the persisted table while the live driver kept ``key=None`` — a
        skew the device reconcile reads as a config change, tearing down the
        device mid-action and invalidating the running handler.
        """
        if not isinstance(delta, dict) or not delta:
            return
        if getattr(self._engine, "project", None) is None:
            raise RuntimeError("No project loaded")
        delta = _without_unset_connections(self._device_id, delta)
        if not delta:
            return
        await self._save(delta)
        log.info("[%s] Setup action applied config update: %s",
                 self._device_id, sorted(delta.keys()))

    async def _save(self, delta: dict[str, Any]) -> None:
        """``delta`` through the change door, taken live once the file holds
        it. Runs in the action's background task, never under the reconcile
        lock, so awaiting the door directly is safe. The door announces it
        as every saved change is (open tabs learn the revision, so a stale
        IDE PUT gets a 409 instead of silently reverting what the wizard
        just wrote, and a script hears ``system.project.reloaded``); a
        notice that cannot be sent is logged there, never this run's error."""
        engine = self._engine
        self._lease.require(engine.devices)
        door = asyncio.ensure_future(engine.persist_bookkeeping_change(
            partial(self._put_in_project, delta, lambda: door),
            runtime_write=partial(engine.take_device_settings, self._device_id, delta),
        ))
        door.add_done_callback(self._door_done)
        try:
            await asyncio.shield(door)
        except asyncio.CancelledError:
            # Ended while the save waited for the lock: it is not begun, so
            # it is dropped. One already inside is let finish (end_run).
            if self._lease.saving is not door:
                door.cancel()
            raise
        except ProjectSaveError as failure:
            # Everything was put back, so the change was not applied.
            raise ProjectNotAppliedError(failure) from failure

    def _put_in_project(self, delta: dict[str, Any], door: Any, project: Any) -> None:
        # Under the door's lock, which every reconcile that retires the
        # instance holds too: the lease is checked where it cannot move
        # before the file and the running device take the change, and from
        # here the save is the run's own to finish.
        self._lease.require(self._engine.devices)
        if not apply_config_delta(project, self._device_id, delta):
            raise RuntimeError(f"Device '{self._device_id}' not found in project")
        self._lease.saving = door()

    def _door_done(self, door: asyncio.Future) -> None:
        if self._lease.saving is door:
            self._lease.saving = None
        if not door.cancelled() and door.exception() is not None:
            # Read here, so a save the run no longer waits for is not left
            # unread; one it still waits for raises in the run as well.
            log.debug("[%s] Setup action save ended: %s", self._device_id, door.exception())

    async def reconnect(self) -> None:
        """Reconnect the instance the run holds, using its current config."""
        self._lease.require(self._engine.devices)
        await self._engine.devices.reconnect_in_place(self._device_id, self._lease.driver)


@dataclass(frozen=True)
class _Line:
    """One line of a run's progress: what the wizard shows, how far, and the
    run's status, with what else the message carries (``error``, ``result``)."""

    step: str
    pct: int | None
    status: str
    extra: dict[str, Any] = field(default_factory=dict)


def _ended_line(why: str) -> _Line:
    """The last line of a run whose lease was ended for ``why``."""
    sentence = _ENDED_BECAUSE[why]
    return _Line(sentence, None, ERROR, {"error": sentence})


@dataclass(eq=False)
class _SetupRun:
    run_id: str
    action_id: str
    lease: SetupLease
    task: asyncio.Task | None = None
    # The last action.progress message sent for the run, without its type.
    latest: dict[str, Any] = field(default_factory=dict)

    @property
    def device_id(self) -> str:
        return self.lease.device_id

    def ended(self) -> bool:
        return self.latest.get("status") in (DONE, ERROR)

    def holds(self, driver: Any) -> bool:
        """Whether this run still holds ``driver``, unended."""
        return self.lease.driver is driver and self.lease.ended_by is None

    def message(self, line: _Line) -> dict[str, Any]:
        """``line`` as the run's action.progress message, without its type."""
        return {
            "device_id": self.device_id,
            "action_id": self.action_id,
            "run_id": self.run_id,
            "step": line.step,
            "pct": line.pct,
            "status": line.status,
            **line.extra,
        }


async def _let_the_save_finish(lease: SetupLease) -> None:
    """Wait (up to :data:`RUN_END_SECONDS`) for a save the run has begun:
    cancelled halfway, the project would hold it while the file or the
    running device did not."""
    if lease.saving is not None:
        await asyncio.wait({lease.saving}, timeout=RUN_END_SECONDS)


def _waits_on(task: asyncio.Task | None) -> bool:
    """Whether ending a run should wait for ``task``: it is still going, and
    it is not the task asking (a run cannot wait for itself)."""
    return task is not None and not task.done() and task is not asyncio.current_task()


class SetupActionRunner:
    """Owns running setup actions: one per device at a time, each as a tracked
    background task that streams progress over WebSocket, holding the
    instance it started on until it ends.
    """

    def __init__(self, engine: Any):
        self._engine = engine
        # Names this process in every run id it hands out, so a run id from
        # an earlier process is told apart from one this process has lost.
        self._start_id = secrets.token_hex(4)
        self._active: dict[str, _SetupRun] = {}  # device_id -> its running run
        # device_id -> the last message of its last run that ended, oldest first.
        self._ended: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._tasks: set[asyncio.Task] = set()

    def is_running(self, device_id: str) -> bool:
        return device_id in self._active

    async def start(
        self, device_id: str, action: dict[str, Any], params: dict[str, Any],
        files: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Kick off a setup action as a background task. Returns immediately with
        a ``run_id``; progress streams over the ``action.progress`` WS channel.

        Raises DeviceNotFoundError if the device has no live driver, or
        SetupActionInProgress if one is already running on it.
        """
        driver = self._engine.devices.get_driver(device_id)
        if driver is None:
            raise DeviceNotFoundError(
                f"Device '{device_id}' not found or has no live driver"
            )
        if device_id in self._active:
            raise SetupActionInProgress(device_id, self._active[device_id].run_id)

        from openavc.core.setup_files import take_setup_files

        params, uploads = take_setup_files(
            getattr(self._engine, "file_staging", None), device_id, action, params or {}, files or {},
        )
        run = _SetupRun(
            run_id=f"{self._start_id}-{uuid4().hex}",
            action_id=action["id"],
            lease=SetupLease(device_id, driver),
        )
        run.latest = run.message(_Line("Starting…", 0, RUNNING))
        self._active[device_id] = run
        # The run is the asking tab's own and outlives its request: the config
        # it saves names that tab, as the request itself would, and the IDE
        # re-syncs on that (syncAfterSetupAction).
        task = asyncio.create_task(self._run_for(request_client_id(), run, params or {}))
        run.task = task
        self._tasks.add(task)
        task.add_done_callback(partial(self._completed, run, uploads))
        return {"run_id": run.run_id, "status": "started", "action_id": action["id"]}

    def _completed(self, run: _SetupRun, uploads, task):
        """Also release files if cancelled before the coroutine starts."""
        self._tasks.discard(task)
        self._let_go(run)
        if uploads:
            self._engine.file_staging.discard_all(uploads)

    def _let_go(self, run: _SetupRun) -> None:
        """A run's entry is removed by that run only, never by another's end."""
        if self._active.get(run.device_id) is run:
            del self._active[run.device_id]

    # -- ending a run --------------------------------------------------------------

    async def end_run(self, device_id: str, driver: Any, why: str) -> None:
        """End the run holding ``driver`` (the device manager's retire step,
        with a ``RETIRED_*`` reason), and wait up to :data:`RUN_END_SECONDS`
        for it to finish. Nothing to do when no run holds that instance."""
        run = self._active.get(device_id)
        if run is None or not run.holds(driver):
            return
        run.lease.ended_by = why
        task = run.task
        if not _waits_on(task):
            return
        await _let_the_save_finish(run.lease)
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=RUN_END_SECONDS)
        if not done:
            log.warning(
                "[%s] Setup action '%s' did not stop within %.0f s of being ended; "
                "anything it still saves or reconnects is refused",
                device_id, run.action_id, RUN_END_SECONDS,
            )

    async def stop(self) -> None:
        """Server shutdown: end every run, before the files they read go."""
        await asyncio.gather(*(
            self.end_run(run.device_id, run.lease.driver, RETIRED_STOPPING)
            for run in list(self._active.values())
        ))

    # -- how a run ended -------------------------------------------------------------

    def outcome(self, device_id: str, run_id: str) -> dict[str, Any] | None:
        """How run ``run_id`` on ``device_id`` stands, as its last
        ``action.progress`` message said it (without the type): running, done
        or error. A run of an earlier process ended with it, and is answered
        as interrupted. None for a run of this process no longer kept: a
        newer run on the device replaced it, or :data:`KEPT_RESULTS` other
        devices' results pushed it out."""
        if not run_id.startswith(f"{self._start_id}-"):
            return {
                "device_id": device_id, "run_id": run_id,
                "step": INTERRUPTED, "pct": None, "status": ERROR, "error": INTERRUPTED,
            }
        run = self._active.get(device_id)
        if run is not None and run.run_id == run_id:
            return dict(run.latest)
        ended = self._ended.get(device_id)
        if ended is not None and ended.get("run_id") == run_id:
            return dict(ended)
        return None

    # -- running -------------------------------------------------------------------

    async def _run_for(
        self, client_id: str | None, run: _SetupRun, params: dict[str, Any],
    ) -> None:
        """_run, speaking for the tab that started it (core/client_origin.py)."""
        with client_origin(client_id):
            await self._run(run, params)

    async def _run(self, run: _SetupRun, params: dict[str, Any]) -> None:
        lease = run.lease
        driver = lease.driver
        dm = self._engine.devices

        async def progress(step: str, pct: int | None = None) -> None:
            if lease.ended_by is not None:
                raise SetupRunEnded(_ENDED_BECAUSE[lease.ended_by])
            await self._emit(run, _Line(str(step), pct, RUNNING))

        try:
            await dm.begin_setup(run.device_id, driver)
            driver._set_setup_context(SetupActionContext(self._engine, lease))
            await self._emit(run, _Line("Starting…", 0, RUNNING))
            result = await driver.run_setup_action(run.action_id, params, progress)
            await self._finish(run, result)
        except asyncio.CancelledError:
            # Ended by the retire step: the wizard is told why. A cancel
            # nobody on record asked for leaves the lease held, so the
            # connection is handed back below as at any other end.
            if lease.ended_by is not None:
                await self._end(run, _ended_line(lease.ended_by))
            raise
        except NotImplementedError:
            await self._end(run, _Line(
                "This device's driver does not implement this action.",
                None, ERROR, {"error": "not_implemented"},
            ))
        except Exception as exc:  # Catch-all: handlers run arbitrary device I/O
            await self._failed(run, exc)
        finally:
            driver._set_setup_context(None)
            if lease.ended_by is None:
                await self._hand_back(run)
            self._let_go(run)

    async def _hand_back(self, run: _SetupRun) -> None:
        """Give the instance's connection back to the retry loop. Only for a
        run whose lease still holds: a retired instance's connection belongs
        to whatever retired it."""
        try:
            await self._engine.devices.end_setup(run.device_id, run.lease.driver)
        except Exception:
            log.exception("end_setup failed for device '%s'", run.device_id)

    async def _finish(self, run: _SetupRun, result: Any) -> None:
        payload = result if isinstance(result, dict) else {}
        if payload.get("success") is False:
            # A handler that ran to completion and found the device wrong
            # (bad community string, not the model this driver drives, a
            # declared OID the device has no answer for). It reports that
            # by returning success:false rather than raising, because the
            # finding is a sentence for the operator, not a stack trace.
            # The run still FAILED, so it terminates as "error" — `status`
            # is the outcome every client branches on, and a reader that
            # doesn't know this return convention must not be told a
            # failed Test Connection succeeded.
            message = str(payload.get("message") or "").strip() or "Setup action reported failure"
            await self._end(run, _Line(message, None, ERROR, {"error": message, "result": payload}))
        else:
            await self._end(run, _Line("Done", 100, DONE, {"result": payload}))

    async def _failed(self, run: _SetupRun, exc: Exception) -> None:
        if run.lease.ended_by is not None:
            log.info(
                "[%s] Setup action '%s' ended: %s", run.device_id, run.action_id,
                _ENDED_BECAUSE[run.lease.ended_by],
            )
            await self._end(run, _ended_line(run.lease.ended_by))
            return
        log.exception(
            "Setup action '%s' on device '%s' failed", run.action_id, run.device_id
        )
        from openavc.api.error_messages import describe_change_failure

        # A setting the handler saved that the running system could not
        # take is said as every door that changes the project says it.
        change = describe_change_failure(exc)
        error = change.sentence if change else str(exc)
        await self._end(run, _Line(error or "Setup action failed", None, ERROR, {"error": error}))

    async def _end(self, run: _SetupRun, line: _Line) -> None:
        """Send the run's last message, once. A run whose lease was ended
        says why, whatever its handler went on to return."""
        if run.ended():
            return
        if run.lease.ended_by is not None:
            line = _ended_line(run.lease.ended_by)
        await self._emit(run, line)

    def _keep(self, run: _SetupRun, msg: dict[str, Any]) -> None:
        """Keep ``msg`` (without its type) as the run's latest, and as its
        device's last result when it ends the run."""
        run.latest = {k: v for k, v in msg.items() if k != "type"}
        if not run.ended():
            return
        self._ended[run.device_id] = run.latest
        self._ended.move_to_end(run.device_id)
        while len(self._ended) > KEPT_RESULTS:
            self._ended.popitem(last=False)

    async def _emit(self, run: _SetupRun, line: _Line) -> None:
        """Send one action.progress event to the Programmer's clients, and
        keep it as the run's latest.

        Never to a panel: it carries the handler's own result message and
        nothing on a panel shows it.
        """
        msg: dict[str, Any] = {"type": "action.progress", **run.message(line)}
        # A handler may return a result that isn't JSON-serializable; don't let
        # that wedge the run — drop the extra payload and emit the core event.
        try:
            json.dumps(msg)
        except (TypeError, ValueError):
            bare = _Line(line.step, line.pct, line.status)
            msg = {"type": "action.progress", **run.message(bare)}
        self._keep(run, msg)
        await self._engine.broadcast_ws(msg, client_type="programmer")
