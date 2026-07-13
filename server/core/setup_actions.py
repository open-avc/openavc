"""
Setup-action runner — the platform side of driver-declared provisioning wizards.

A setup action (an action of ``kind:"setup"``, see ``server/drivers/actions.py``)
is a driver-declared wizard that can run while a device is offline, brings its
own out-of-band transport, reports multi-step progress, and may rewrite the
device's connection config and reconnect on success. This module orchestrates a
run generically — it never knows what an action *does*:

  - suppress auto-reconnect for the duration (so it can't race the handler's own
    transport), then re-enable it afterward;
  - install a per-run context that backs ``driver.request_config_update`` /
    ``driver.request_reconnect``;
  - stream the handler's ``progress(step, pct)`` calls to the UI as
    ``action.progress`` WebSocket events, plus a final ``done`` / ``error`` event;
  - run as a background task so a client disconnecting mid-flight can't abort a
    hardware-mutating provisioning step.

All device/protocol specifics (which transport, which commands, the config
delta) live in the driver's ``run_setup_action`` handler.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from server.utils.logger import get_logger

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


class SetupActionContext:
    """Per-run handle the platform installs on a driver for the duration of a
    ``run_setup_action`` call. Backs ``request_config_update`` /
    ``request_reconnect`` so the handler can persist new connection settings and
    bring the device back online without reaching into platform internals.
    """

    def __init__(self, engine: Any, device_id: str):
        self._engine = engine
        self._device_id = device_id

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

        from server.core.project_migration import CONNECTION_FIELDS

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
        dev = next((d for d in project.devices if d.id == device_id), None)
        if dev is None:
            raise RuntimeError(f"Device '{device_id}' not found in project")

        conn = dict(project.connections.get(device_id, {}))
        protocol = dict(dev.config)
        for key, value in delta.items():
            if key in CONNECTION_FIELDS:
                conn[key] = value
            else:
                protocol[key] = value
        if conn:
            project.connections[device_id] = conn
        else:
            project.connections.pop(device_id, None)
        dev.config = protocol

        # Merge into the live driver BEFORE the reconcile: driver.config is
        # the same dict the device manager compares against the new resolved
        # config, so updating it first keeps that compare convergent — the
        # running instance is not torn down, the handler's ``self`` stays
        # valid, and the next connect() uses the new settings.
        driver = engine.devices.get_driver(device_id)
        if driver is not None:
            driver.config.update(delta)

        # Runs in the action's background task, never under the reconcile
        # lock, so awaiting the seam directly is safe. The revision bump and
        # project.reloaded broadcast mean a stale IDE PUT gets a 409 instead
        # of silently reverting what the wizard just wrote.
        await engine.apply_project(project)
        log.info("[%s] Setup action applied config update: %s",
                 device_id, sorted(delta.keys()))

    async def reconnect(self) -> None:
        """Reconnect the device in place using its current config."""
        await self._engine.devices.reconnect_in_place(self._device_id)


class SetupActionRunner:
    """Owns running setup actions: one per device at a time, each as a tracked
    background task that streams progress over WebSocket.
    """

    def __init__(self, engine: Any):
        self._engine = engine
        self._active: dict[str, str] = {}  # device_id -> run_id
        self._tasks: set[asyncio.Task] = set()

    def is_running(self, device_id: str) -> bool:
        return device_id in self._active

    async def start(
        self, device_id: str, action: dict[str, Any], params: dict[str, Any],
    ) -> dict[str, Any]:
        """Kick off a setup action as a background task. Returns immediately with
        a ``run_id``; progress streams over the ``action.progress`` WS channel.

        Raises ValueError if the device has no live driver, or
        SetupActionInProgress if one is already running on it.
        """
        driver = self._engine.devices.get_driver(device_id)
        if driver is None:
            raise ValueError(
                f"Device '{device_id}' not found or has no live driver"
            )
        if device_id in self._active:
            raise SetupActionInProgress(device_id, self._active[device_id])

        run_id = uuid4().hex
        self._active[device_id] = run_id
        task = asyncio.create_task(
            self._run(device_id, action, params or {}, run_id)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"run_id": run_id, "status": "started", "action_id": action["id"]}

    async def _run(
        self,
        device_id: str,
        action: dict[str, Any],
        params: dict[str, Any],
        run_id: str,
    ) -> None:
        action_id = action["id"]
        dm = self._engine.devices
        driver = dm.get_driver(device_id)
        if driver is None:
            self._active.pop(device_id, None)
            return

        async def progress(step: str, pct: int | None = None) -> None:
            await self._emit(device_id, action_id, run_id, str(step), pct, "running")

        try:
            await dm.begin_setup(device_id)
            driver._set_setup_context(SetupActionContext(self._engine, device_id))
            await self._emit(device_id, action_id, run_id, "Starting…", 0, "running")
            result = await driver.run_setup_action(action_id, params, progress)
            await self._emit(
                device_id, action_id, run_id, "Done", 100, "done",
                result=result if isinstance(result, dict) else {},
            )
        except NotImplementedError:
            await self._emit(
                device_id, action_id, run_id,
                "This device's driver does not implement this action.",
                None, "error", error="not_implemented",
            )
        except Exception as exc:  # Catch-all: handlers run arbitrary device I/O
            log.exception(
                "Setup action '%s' on device '%s' failed", action_id, device_id
            )
            await self._emit(
                device_id, action_id, run_id,
                str(exc) or "Setup action failed", None, "error", error=str(exc),
            )
        finally:
            driver._set_setup_context(None)
            try:
                await dm.end_setup(device_id)
            except Exception:
                log.exception("end_setup failed for device '%s'", device_id)
            self._active.pop(device_id, None)

    async def _emit(
        self,
        device_id: str,
        action_id: str,
        run_id: str,
        step: str,
        pct: int | None,
        status: str,
        **extra: Any,
    ) -> None:
        """Broadcast one action.progress event to all WS clients."""
        msg: dict[str, Any] = {
            "type": "action.progress",
            "device_id": device_id,
            "action_id": action_id,
            "run_id": run_id,
            "step": step,
            "pct": pct,
            "status": status,
            **extra,
        }
        # A handler may return a result that isn't JSON-serializable; don't let
        # that wedge the run — drop the extra payload and emit the core event.
        try:
            json.dumps(msg)
        except (TypeError, ValueError):
            msg = {
                "type": "action.progress",
                "device_id": device_id,
                "action_id": action_id,
                "run_id": run_id,
                "step": step,
                "pct": pct,
                "status": status,
            }
        await self._engine.broadcast_ws(msg)
