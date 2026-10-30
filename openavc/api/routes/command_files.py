"""Commands that take a file or hand one back, over HTTP: the uploads, and
Send Command's background runs of them.

The rules live in ``core/file_staging.py`` (where a file waits and what it
must be) and ``core/command_runs.py`` (how a run goes); this module only
answers. A file goes up in chunks, each a raw ``PUT`` at its offset, because
the cloud tunnel carries a request as one message: the server names the
chunk size, small through the tunnel and large on a direct connection.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from openavc.api._engine import _get_engine
from openavc.api.errors import StructuredApiError
from openavc.api.host_policy import ProgrammerRoute
from openavc.api.models import CommandFileBeginRequest, CommandRunRequest
from openavc.api.routes.devices import command_failure
from openavc.core.command_runs import CommandRunInProgress, RunFailure, device_owner
from openavc.core.device_manager import DeviceNotFoundError
from openavc.core.file_staging import (
    CHUNK_BYTES_DIRECT,
    StagingError,
    chunk_size_for,
)
from openavc.utils.request_origin import is_tunneled_request

router = APIRouter(route_class=ProgrammerRoute)


def _run_failure(exc: HTTPException) -> RunFailure:
    """A background run's failure, worded and classed as Send Command's is."""
    code = exc.fields.get("code") if isinstance(exc, StructuredApiError) else None
    return RunFailure(str(exc.detail), exc.status_code, code)

_DOWNLOAD_CHUNK = 256 * 1024


def staging_refusal(exc: StagingError) -> HTTPException:
    return StructuredApiError(exc.status, str(exc), **exc.fields)


def file_param_def(commands: Any, command: str, param: str) -> dict[str, Any]:
    """The declaration of one file param, or a 404/400 saying what is wrong.
    Shared with the Device Audit's upload door."""
    cmd_def = commands.get(command) if isinstance(commands, dict) else None
    if not isinstance(cmd_def, dict):
        raise HTTPException(status_code=404, detail=f"Command '{command}' not found")
    pdef = (cmd_def.get("params") or {}).get(param)
    label = cmd_def.get("label") or command
    if not isinstance(pdef, dict) or pdef.get("type") != "file":
        raise HTTPException(
            status_code=400,
            detail=f"'{label}' has no file parameter named '{param}'.",
        )
    return pdef


def upload_answer(upload: Any, request: Request) -> dict[str, Any]:
    return {
        "upload": upload.to_dict(),
        "chunk_size": chunk_size_for(is_tunneled_request(request)),
    }


def content_disposition(name: str) -> str:
    """``attachment`` with the name in both forms a browser reads."""
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in name
    ) or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"


async def file_stream(path, on_complete) -> AsyncIterator[bytes]:
    """A staged file a piece at a time; ``on_complete`` runs once the last
    piece has gone out (a download cut short leaves the file to expire)."""
    handle = await asyncio.to_thread(path.open, "rb")
    try:
        while True:
            piece = await asyncio.to_thread(handle.read, _DOWNLOAD_CHUNK)
            if not piece:
                break
            yield piece
    finally:
        await asyncio.to_thread(handle.close)
    await on_complete()


# --- uploads ------------------------------------------------------------------


@router.post("/devices/{device_id}/command-files")
async def begin_device_upload(
    device_id: str, body: CommandFileBeginRequest, request: Request,
) -> dict[str, Any]:
    """Open an upload for one file param of one of this device's commands."""
    engine = _get_engine()
    driver = engine.devices.get_driver(device_id)
    if driver is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    commands = (getattr(driver, "DRIVER_INFO", {}) or {}).get("commands")
    pdef = file_param_def(commands, body.command, body.param)
    try:
        upload = engine.file_staging.begin(
            owner=device_owner(device_id), command=body.command, param=body.param,
            pdef=pdef, name=body.name, size=body.size,
        )
    except StagingError as exc:
        raise staging_refusal(exc) from None
    return upload_answer(upload, request)


@router.put("/command-files/{upload_id}")
async def upload_chunk(
    upload_id: str, request: Request, offset: int = Query(ge=0),
) -> dict[str, Any]:
    """One chunk of an upload, at ``offset``. The body is the raw bytes."""
    engine = _get_engine()
    try:
        engine.file_staging.get(upload_id)
    except StagingError as exc:
        raise staging_refusal(exc) from None
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > CHUNK_BYTES_DIRECT:
        raise HTTPException(status_code=413, detail="That chunk is larger than an upload takes.")
    received = bytearray()
    async for piece in request.stream():
        received.extend(piece)
        if len(received) > CHUNK_BYTES_DIRECT:
            raise HTTPException(status_code=413, detail="That chunk is larger than an upload takes.")
    try:
        upload = await engine.file_staging.write(upload_id, offset, bytes(received))
    except StagingError as exc:
        raise staging_refusal(exc) from None
    return {"upload": upload.to_dict()}


@router.delete("/command-files/{upload_id}")
async def discard_upload(upload_id: str) -> dict[str, Any]:
    """Drop an upload that will not be used (its file is deleted)."""
    engine = _get_engine()
    try:
        upload = engine.file_staging.get(upload_id)
    except StagingError:
        return {"status": "discarded"}
    if upload.in_use:
        raise HTTPException(status_code=409, detail=f"{upload.name} is in use by a running command.")
    engine.file_staging.discard(upload_id)
    return {"status": "discarded"}


# --- runs -----------------------------------------------------------------------


@router.post("/devices/{device_id}/command-runs", status_code=202)
async def start_command_run(device_id: str, body: CommandRunRequest) -> dict[str, Any]:
    """Start a command that takes or hands back a file. Answers at once with
    the run; ``command.run`` WebSocket messages follow it."""
    engine = _get_engine()
    try:
        run = engine.command_runs.start(
            device_id, body.command, body.params, body.files,
            describe_error=lambda exc: _run_failure(command_failure(device_id, body.command, exc)),
        )
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found") from None
    except StagingError as exc:
        raise staging_refusal(exc) from None
    except CommandRunInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"run": run.to_dict()}


@router.get("/devices/{device_id}/command-runs")
async def list_command_runs(device_id: str) -> dict[str, Any]:
    """This device's file runs: the one running and those that ended lately."""
    engine = _get_engine()
    return {"runs": [r.to_dict() for r in engine.command_runs.for_device(device_id)]}


@router.get("/devices/{device_id}/command-runs/{run_id}")
async def get_command_run(device_id: str, run_id: str) -> dict[str, Any]:
    run = _get_engine().command_runs.get(device_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="That run has ended and is no longer kept.")
    return {"run": run.to_dict()}


@router.post("/devices/{device_id}/command-runs/{run_id}/cancel")
async def cancel_command_run(device_id: str, run_id: str) -> dict[str, Any]:
    """Stop a run. Its driver call is cancelled; the run ends as stopped."""
    run = _get_engine().command_runs.cancel(device_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="That run has ended and is no longer kept.")
    return {"run": run.to_dict()}


@router.get("/devices/{device_id}/command-runs/{run_id}/file")
async def download_run_file(device_id: str, run_id: str) -> StreamingResponse:
    """The file a run handed back. Streamed without a length, so the cloud
    tunnel streams it too; deleted once it has gone out in full."""
    engine = _get_engine()
    held = engine.command_runs.returned_file(device_id, run_id)
    if held is None:
        raise HTTPException(
            status_code=404,
            detail="That file is no longer kept. Run the command again to get a new copy.",
        )

    async def done() -> None:
        await engine.command_runs.downloaded(device_id, run_id)

    return StreamingResponse(
        file_stream(held.path, done),
        media_type=held.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(held.name),
            "Cache-Control": "no-store",
        },
    )
