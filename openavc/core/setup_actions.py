"""Driver-declared commissioning through a prepared result reservation.

The existing runner suppresses reconnect, installs the driver's context, runs one
handler per device, and restores reconnect afterwards. Prepare reserves protected
result capacity without starting a task; start consumes that reservation once.
Typed progress/results are observed by authorized adapters, never broadcast as
arbitrary dictionaries. Credentials are registered before an awaited save or
reconnect and retrieved separately. The runner does not infer physical success,
project durability or complete target retirement from a returned boolean.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from openavc.core.device_config import apply_config_delta
from openavc.core.device_manager import DeviceNotFoundError
from openavc.core.outbound_messages import RoomText
from openavc.core.setup_results import (
    Outcome, SetupConflict, SetupFinding, SetupProgress, SetupResult,
    SetupResultStore, SetupUnavailable, StageState,
)
from openavc.drivers.actions import resolve_device_actions
from openavc.drivers.base import normalize_and_validate_command_params
from openavc.utils.logger import get_logger

log = get_logger(__name__)


class SetupActionInProgress(Exception):
    """Raised when a setup action is requested for a device that already has
    one running (one run per device at a time)."""

    def __init__(self, device_id: str, run_id: str):
        self.device_id = device_id
        self.run_id = run_id
        super().__init__(
            f"A setup action is already running on device '{device_id}'"
        )


class SetupHandshakeRequired(ValueError):
    """An old one-POST caller cannot safely begin credential-creating work."""


class SetupContractRequired(ValueError):
    """No declared or qualified installed-source result adapter is selected."""


class SetupActionContext:
    """Per-run handle the platform installs on a driver for the duration of a
    ``run_setup_action`` call. Backs ``request_config_update`` /
    ``request_reconnect`` so the handler can persist new connection settings and
    bring the device back online without reaching into platform internals.
    """

    def __init__(self, engine: Any, device_id: str, *, results=None, slot=None):
        self._engine = engine
        self._device_id = device_id
        self._results = results
        self._slot = slot
        self.config_save = StageState.NOT_REQUESTED
        self.reconnect_state = StageState.NOT_REQUESTED

    def register_secret(self, values: dict[str, str]) -> int:
        if self._results is None:
            raise RuntimeError("No protected setup result reservation")
        return self._results.publish_secret(self._slot, values)

    async def apply_config_update(self, delta: dict[str, Any]) -> None:
        """Persist a connection/config delta and merge it into the live driver.

        Connection fields (host, port, transport, credentials, …) go to the
        project's connections table; anything else goes to the device's protocol
        config. The live driver instance's ``self.config`` is updated in place so
        the next ``connect()`` uses the new settings — the same instance keeps
        running, so the handler's ``self`` stays valid.

        A ``None``-valued connection field is ignored (with a warning): the
        connections table never stores None, so honoring it would drop the key
        from the persisted table while the live driver kept ``key=None`` — a
        skew the device reconcile reads as a config change, tearing down the
        device mid-action and invalidating the running handler.
        """
        if not isinstance(delta, dict) or not delta:
            return
        engine = self._engine
        if getattr(engine, "project", None) is None:
            raise RuntimeError("No project loaded")

        from openavc.core.project_migration import CONNECTION_FIELDS

        device_id = self._device_id
        none_conn = [k for k, v in delta.items()
                     if k in CONNECTION_FIELDS and v is None]
        if none_conn:
            log.warning(
                "[%s] Setup action tried to unset connection field(s) %s — "
                "ignored (unsetting connection fields is not supported)",
                device_id, sorted(none_conn),
            )
            delta = {k: v for k, v in delta.items() if k not in none_conn}
            if not delta:
                return

        # Build the change on a copy — apply_project diffs it against the
        # live project, so an in-place edit would reconcile nothing.
        project = engine.project.model_copy(deep=True)
        if not apply_config_delta(project, device_id, delta):
            raise RuntimeError(f"Device '{device_id}' not found in project")

        # Merge into the live device BEFORE the reconcile — into the driver
        # (what the next connect dials) and into the config the device manager
        # compares against the new resolved one. Doing it first keeps that
        # compare convergent, so the running instance is not torn down and the
        # handler's ``self`` stays valid.
        engine.devices.merge_live_config(device_id, delta)

        # Runs in the action's background task, never under the reconcile
        # lock, so awaiting the seam directly is safe. The revision bump and
        # project.reloaded broadcast mean a stale IDE PUT gets a 409 instead
        # of silently reverting what the wizard just wrote.
        generation = self._results.saving_generation(self._slot, delta) if self._results else None
        # Persistence can precede a failed reconcile. An exception is not a
        # receipt that nothing was saved; the project owner still owns that fact.
        self.config_save = StageState.UNKNOWN
        await engine.apply_project(project)
        self.config_save = StageState.REPORTED_COMPLETE
        if generation is not None:
            self._results.secret_saved(self._slot, generation)
        log.info("[%s] Setup action applied config update: %s",
                 device_id, sorted(delta.keys()))

    async def reconnect(self) -> None:
        """Reconnect the device in place using its current config."""
        self.reconnect_state = StageState.UNKNOWN
        try:
            await self._engine.devices.reconnect_in_place(self._device_id)
        except Exception:
            self.reconnect_state = StageState.FAILED
            raise
        self.reconnect_state = StageState.REPORTED_COMPLETE


class SetupActionRunner:
    """Existing per-device execution owner, with reserved typed result ingress.

    Prepare is inert. Start validates and consumes once without yielding before
    task creation. Result/HTTP revocation does not silently cancel equipment
    effects already dispatched; runtime target retirement remains with its owner.
    """

    def __init__(self, engine: Any):
        self._engine = engine
        self._active: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()
        self.results = SetupResultStore()
        self._observers: dict[str, set[Callable[[], None]]] = {}

    def is_running(self, device_id: str) -> bool:
        return device_id in self._active

    def _definition(self, device_id: str, action_id: str):
        driver = self._engine.devices.get_driver(device_id)
        if driver is None:
            raise DeviceNotFoundError(f"Device '{device_id}' not found or has no live driver")
        action = next((a for a in resolve_device_actions(driver.DRIVER_INFO)
                       if a["id"] == action_id and a["kind"] == "setup"), None)
        if action is None or action.get("result_contract") != "setup/v1":
            raise SetupContractRequired("This setup action needs a qualified result contract before it can run.")
        return driver, action

    def _binding(self, device_id: str, driver, action: dict, params: dict) -> dict:
        return {"device_id": device_id, "action_id": action["id"], "action": action,
                "driver_version": driver.DRIVER_INFO.get("version"),
                "project_revision": getattr(self._engine, "_project_revision", None),
                "config": driver.config,
                "params": normalize_and_validate_command_params(action["id"], action["params"], params)}

    def prepare(self, device_id: str, action_id: str, params: dict, *,
                original_grant: Callable[[], bool], current: Callable[[], bool],
                principal: str | None = None) -> dict:
        driver, action = self._definition(device_id, action_id)
        item = self.results.prepare(self._binding(device_id, driver, action, params),
                                    target=driver, original_grant=original_grant,
                                    current=current, principal=principal)
        return {"run_id": item.run_id, "capability": item.capability, "status": "prepared"}

    async def start(self, device_id: str, action: dict, params: dict, *,
                    capability: str | None = None, current: Callable[[], bool] | None = None,
                    principal: str | None = None) -> dict:
        if capability is None or current is None:
            raise SetupHandshakeRequired("Reopen this setup wizard before starting the action.")
        prior = self.results.status(capability, current=current, principal=principal)
        frozen = self.results.request_data(capability, current=current, principal=principal)
        if frozen["device_id"] != device_id or frozen["action_id"] != action["id"]:
            raise SetupConflict("This setup reservation belongs to a different action")
        # A retry after a save/reload reads the original run; it never resolves a
        # new target or accidentally adopts a newly edited action declaration.
        if prior["status"] != "prepared":
            repeated = {**frozen, "params": normalize_and_validate_command_params(
                frozen["action_id"], frozen["action"]["params"], params)}
            self.results.claim(capability, repeated, target=None, current=current, principal=principal)
            return self.results.status(capability, current=current, principal=principal)
        driver, declared = self._definition(device_id, action["id"])
        if device_id in self._active:
            raise SetupActionInProgress(device_id, self._active[device_id])
        bound = self._binding(device_id, driver, declared, params)
        slot = self.results.claim(capability, bound, target=driver, current=current, principal=principal)
        if slot is None:
            return self.results.status(capability, current=current, principal=principal)
        self._active[device_id] = slot.run_id
        # Freeze again through the owner's stored bytes, never caller aliases.
        inputs = self.results.dispatch_inputs(slot)
        coroutine = self._run(device_id, driver, inputs, slot,
                              self.results.access_guard(capability, principal=principal))
        try:
            task = asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close()
            self._active.pop(device_id, None)
            self.results.complete(slot, SetupResult(Outcome.UNVERIFIED, RoomText("Setup could not start.")))
            raise
        self._tasks.add(task)

        def settled(completed):
            self._tasks.discard(completed)
            # Cancellation before the coroutine's first instruction cannot run
            # its finally block. Retire the consumed result without permitting
            # another dispatch; this does not cancel or replace any device task.
            if slot.phase in ("claimed", "running"):
                self.results.complete(slot, SetupResult(Outcome.UNVERIFIED, RoomText("Setup was interrupted.")))
                if self._active.get(device_id) == slot.run_id:
                    self._active.pop(device_id, None)
                self._notify(slot.run_id)
        task.add_done_callback(settled)
        return self.results.status(capability, current=current, principal=principal)

    def observe(self, run_id: str, callback: Callable[[], None]) -> Callable[[], None]:
        """An authorized socket adapter owns registration and its teardown."""
        observers = self._observers.setdefault(run_id, set())
        observers.add(callback)

        def remove():
            observers.discard(callback)
            if not observers:
                self._observers.pop(run_id, None)
        return remove

    def _notify(self, run_id: str) -> None:
        # Observers only project/enqueue; no awaits or task scheduling here.
        for callback in tuple(self._observers.get(run_id, ())):
            try:
                callback()
            except Exception:
                observers = self._observers.get(run_id, set())
                observers.discard(callback)
                if not observers:
                    self._observers.pop(run_id, None)
                log.warning("Setup progress subscriber refused a notification")

    async def _run(self, device_id: str, driver, inputs: dict, slot, original: Callable[[], bool]) -> None:
        dm = self._engine.devices
        action_id = inputs["action_id"]
        context = SetupActionContext(self._engine, device_id, results=self.results, slot=slot)
        outcome = SetupResult(Outcome.UNVERIFIED, RoomText("Setup did not finish."))
        began = False

        async def progress(value: SetupProgress) -> None:
            if not isinstance(value, SetupProgress):
                raise TypeError("The setup result contract requires typed progress")
            self.results.progress(slot, value.code, value.detail, value.percent)
            self._notify(slot.run_id)

        try:
            await dm.begin_setup(device_id)
            began = True
            # begin_setup can wait for reconnect cancellation. Recheck after it
            # and before invoking the handler; this is not a runtime target lease.
            live, declared = self._definition(device_id, action_id)
            if live is not driver:
                raise SetupConflict("The setup target changed before dispatch")
            self.results.require_inputs(slot, self._binding(device_id, live, declared, inputs["params"]))
            if not original():
                raise SetupUnavailable("Setup permission changed before dispatch")
            driver._set_setup_context(context)
            self.results.running(slot)
            await progress(SetupProgress("setup.starting", "Starting", 0))
            outcome = await driver.run_setup_action(action_id, inputs["params"], progress)
            if not isinstance(outcome, SetupResult):
                raise TypeError("The setup result contract requires a typed result")
            # Validate before cleanup so malformed handler output gets the same
            # explicit failure disposition as any other ingress violation.
            outcome.payload()
        except asyncio.CancelledError:
            outcome = SetupResult(Outcome.UNVERIFIED, RoomText("Setup was interrupted. Check the device before trying again."))
        except Exception as exc:
            # Arbitrary exception strings may contain newly generated credentials.
            # Keep a typed cause; a declared adapter supplies useful findings.
            log.warning("Setup action failed (%s)", type(exc).__name__)
            outcome = SetupResult(Outcome.UNVERIFIED, RoomText("Setup could not finish. Check the device before trying again."),
                                  (SetupFinding("setup.exception", Outcome.FAILED, type(exc).__name__),))
        finally:
            driver._set_setup_context(None)
            if began:
                try:
                    await dm.end_setup(device_id)
                except Exception as exc:
                    log.warning("Setup cleanup failed (%s)", type(exc).__name__)
                    outcome = replace(outcome, outcome=Outcome.DEGRADED,
                                      findings=(*outcome.findings, SetupFinding(
                                          "setup.cleanup", Outcome.UNVERIFIED, type(exc).__name__)))
            self._active.pop(device_id, None)
            outcome = replace(outcome, config_save=context.config_save, reconnect=context.reconnect_state)
            try:
                self.results.complete(slot, outcome)
            except (TypeError, ValueError, RuntimeError):
                # Oversized/malformed diagnostics cannot strand result capacity or
                # discard an independently registered credential.
                self.results.complete(slot, SetupResult(Outcome.UNVERIFIED,
                    RoomText("Setup returned an unsupported result. Check the device before trying again."),
                    config_save=context.config_save, reconnect=context.reconnect_state))
            self._notify(slot.run_id)
