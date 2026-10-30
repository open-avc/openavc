"""Send Command's background runs: a command that takes a file or hands one
back (``spec.is_file_command``), run from the device page.

Such a command can run for minutes (a firmware image going to a device), and
the request that starts it cannot wait that long: through the cloud tunnel a
response has 30 seconds in all. So the request answers with a run at once,
and the run carries on as a background task, the shape the setup-action
runner set (``core/setup_actions.py``):

  - the run is not tied to the request, so a closed tab or a dropped tunnel
    does not cut a firmware upload short;
  - one file run per device at a time;
  - progress goes to every Programmer client as ``command.run`` WebSocket
    messages: the run's state, and how much of each file the driver has
    read so far (``CommandFile.bytes_read``), sent while it changes;
  - a person can stop a run. That cancels the driver's call (its own cleanup
    runs) and frees the device, so a driver with no timeout of its own
    cannot hold the slot until the server restarts;
  - a run that finished is kept for :data:`RUN_KEEP_SECONDS`, so a reloaded
    device page can show how it ended and still download what it returned.

The files themselves are staged by ``core/file_staging.py`` and deleted when
the run ends; a returned file is held there until it is downloaded.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from openavc.core.client_origin import request_client_id
from openavc.core.file_staging import FileStaging, HeldFile, StagingError
from openavc.drivers.command_files import CommandFile, DeviceFile
from openavc.drivers.spec import file_param_names, is_file_command
from openavc.utils.logger import get_logger

log = get_logger(__name__)

RUN_KEEP_SECONDS = 10 * 60
PROGRESS_SECONDS = 0.5

RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"


def device_owner(device_id: str) -> str:
    """The staging owner for a project device's uploads."""
    return f"device:{device_id}"


class CommandRunInProgress(Exception):
    """A device already has a file run going (one at a time)."""

    def __init__(self, label: str) -> None:
        super().__init__(
            f"'{label}' is still running on this device. Wait for it to "
            "finish, or stop it, before starting another."
        )


@dataclass(frozen=True)
class RunFailure:
    """A run's failure as the page reads it: the sentence, and the HTTP status
    and code Send Command answers the same failure with."""

    text: str
    status: int | None = None
    code: str | None = None


@dataclass(eq=False)
class CommandRun:
    id: str
    device_id: str
    command: str
    label: str
    files: dict[str, CommandFile]
    upload_ids: list[str]
    client_id: str | None
    status: str = RUNNING
    started: float = field(default_factory=time.time)
    ended: float | None = None
    error: str | None = None
    # The HTTP status and code the same failure answers Send Command with
    # (``RunFailure``), so the page treats both alike.
    error_status: int | None = None
    error_code: str | None = None
    result: Any = None
    held_id: str | None = None
    returned: dict[str, Any] | None = None
    task: asyncio.Task | None = None

    def sent(self) -> int:
        return sum(f.bytes_read for f in self.files.values())

    def total(self) -> int:
        return sum(f.size for f in self.files.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.id,
            "device_id": self.device_id,
            "command": self.command,
            "label": self.label,
            "status": self.status,
            "started": self.started,
            "ended": self.ended,
            "files": [
                {"param": param, "name": f.name, "size": f.size, "read": f.bytes_read}
                for param, f in self.files.items()
            ],
            "sent": self.sent(),
            "total": self.total(),
            "result": self.result,
            "error": self.error,
            "error_status": self.error_status,
            "error_code": self.error_code,
            "returned": self.returned,
            "client_id": self.client_id,
        }


def _jsonable(value: Any) -> Any:
    """A driver's return value as the page can show it (what /command sends)."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class CommandRuns:
    """The file runs of every device, and the files they handed back."""

    def __init__(self, engine: Any, staging: FileStaging) -> None:
        self._engine = engine
        self._staging = staging
        self._runs: dict[str, CommandRun] = {}

    # -- starting ----------------------------------------------------------------

    def start(
        self,
        device_id: str,
        command: str,
        params: dict[str, Any] | None,
        files: dict[str, str] | None,
        *,
        describe_error: Callable[[Exception], "str | RunFailure"],
    ) -> CommandRun:
        """Start a file command on a device in the background.

        ``files`` maps each file param to the upload that fills it. Raises
        ``DeviceNotFoundError`` (no such device), ``StagingError`` (an upload
        that is not ready or not for this command), ``ValueError`` (not a file
        command, or a file param named wrongly), or
        :class:`CommandRunInProgress`.
        """
        from openavc.core.device_manager import DeviceNotFoundError

        driver = self._engine.devices.get_driver(device_id)
        if driver is None:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        info = getattr(driver, "DRIVER_INFO", {}) or {}
        cmd_def = (info.get("commands") or {}).get(command)
        label = str((cmd_def or {}).get("label") or command) if isinstance(cmd_def, dict) else command
        if not is_file_command(cmd_def):
            raise ValueError(
                f"'{label}' takes no file. Send it with Send Command as usual."
            )
        self._sweep()
        running = self.running(device_id)
        if running is not None:
            raise CommandRunInProgress(running.label)

        file_params = set(file_param_names(cmd_def))
        files = dict(files or {})
        stray = sorted(set(files) - file_params)
        if stray:
            raise ValueError(f"'{label}' has no file parameter named '{stray[0]}'.")
        owner = device_owner(device_id)
        taken: dict[str, CommandFile] = {}
        try:
            for param, upload_id in files.items():
                taken[param] = self._staging.take(
                    upload_id, owner=owner, command=command, param=param,
                )
        except StagingError:
            # Hand back what was already taken, so a retry can use it.
            for param in taken:
                self._staging.untake(files[param])
            raise

        run = CommandRun(
            id=secrets.token_hex(8),
            device_id=device_id,
            command=command,
            label=label,
            files=taken,
            upload_ids=list(files.values()),
            client_id=request_client_id(),
        )
        merged = {k: v for k, v in (params or {}).items() if k not in file_params}
        merged.update(taken)
        self._runs[run.id] = run
        run.task = asyncio.create_task(self._run(run, merged, describe_error))
        return run

    async def _run(
        self,
        run: CommandRun,
        params: dict[str, Any],
        describe_error: Callable[[Exception], "str | RunFailure"],
    ) -> None:
        ticker: asyncio.Task | None = None
        try:
            await self._publish(run)
            ticker = asyncio.create_task(self._tick(run))
            result = await self._engine.devices.send_command(
                run.device_id, run.command, params, files_allowed=True,
            )
            if isinstance(result, DeviceFile):
                held = self._staging.hold(device_owner(run.device_id), result)
                run.held_id = held.id
                run.returned = held.to_dict()
            else:
                run.result = _jsonable(result)
            run.status = DONE
        except asyncio.CancelledError:
            run.status = CANCELLED
        except Exception as exc:  # the driver's own failure, worded for the page
            run.status = ERROR
            try:
                described = describe_error(exc)
                if isinstance(described, RunFailure):
                    run.error = described.text
                    run.error_status = described.status
                    run.error_code = described.code
                else:
                    run.error = described
            except Exception:
                log.exception("Wording a command run's failure failed")
                run.error = f"'{run.label}' did not finish."
        finally:
            if ticker is not None:
                ticker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ticker
            run.ended = time.time()
            self._staging.discard_all(run.upload_ids)
            await self._publish(run)

    async def _tick(self, run: CommandRun) -> None:
        last = run.sent()
        while True:
            await asyncio.sleep(PROGRESS_SECONDS)
            now = run.sent()
            if now != last:
                last = now
                await self._publish(run)

    # -- reading -------------------------------------------------------------------

    def running(self, device_id: str) -> CommandRun | None:
        return next(
            (r for r in self._runs.values() if r.device_id == device_id and r.status == RUNNING),
            None,
        )

    def for_device(self, device_id: str) -> list[CommandRun]:
        self._sweep()
        return sorted(
            (r for r in self._runs.values() if r.device_id == device_id),
            key=lambda r: r.started,
        )

    def get(self, device_id: str, run_id: str) -> CommandRun | None:
        run = self._runs.get(run_id)
        return run if run is not None and run.device_id == device_id else None

    def returned_file(self, device_id: str, run_id: str) -> HeldFile | None:
        run = self.get(device_id, run_id)
        if run is None:
            return None
        held = self._staging.held(run.held_id)
        if held is None and run.returned is not None:
            run.returned = {**run.returned, "gone": True}
        return held

    async def downloaded(self, device_id: str, run_id: str) -> None:
        """The returned file went out in full: nothing keeps it now."""
        run = self.get(device_id, run_id)
        if run is None or run.held_id is None:
            return
        self._staging.release_held(run.held_id)
        run.held_id = None
        if run.returned is not None:
            run.returned = {**run.returned, "gone": True}
        await self._publish(run)

    # -- stopping ------------------------------------------------------------------

    def cancel(self, device_id: str, run_id: str) -> CommandRun | None:
        run = self.get(device_id, run_id)
        if run is not None and run.status == RUNNING and run.task is not None:
            run.task.cancel()
        return run

    async def stop(self) -> None:
        """Server shutdown: stop every run and let each one clean up."""
        tasks = [r.task for r in self._runs.values() if r.task and not r.task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._runs.clear()

    def _sweep(self) -> None:
        now = time.time()
        for run_id, run in list(self._runs.items()):
            if run.ended is not None and now - run.ended > RUN_KEEP_SECONDS:
                if run.held_id:
                    self._staging.release_held(run.held_id)
                del self._runs[run_id]

    async def _publish(self, run: CommandRun) -> None:
        try:
            await self._engine.broadcast_ws(
                {"type": "command.run", **run.to_dict()}, client_type="programmer",
            )
        except Exception:
            log.debug("command.run broadcast failed", exc_info=True)
