"""Problem reports over HTTP.

On the authenticated router: a report holds a device's settings (credentials
masked), its address and its recent traffic. The rules live in
``openavc/support/``; this module parses, calls, and answers.

- ``GET /devices/{device_id}/problem-report``: what a report would include
  ("What's included" in the dialog), as counts and lists from the same
  sources the report reads, plus where Send delivers it (``report_url``,
  ``report_host``; empty when sending is off) and the size it may be.
- ``POST /devices/{device_id}/problem-report``: build one and keep it in
  ``{data_dir}/problem_reports/`` (the newest 10); answers ``{name,
  report_id, size_bytes}``.
- ``GET /problem-reports/{name}``: the saved zip, for Download and for the
  browser's own send when this server cannot reach the internet.
- ``POST /problem-reports/{name}/send``: send it once
  (``support/report_upload.py``); answers ``{sent, reference, failure,
  status}``. A failure is still a 200: the send was tried, and the page words
  the outcome.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from openavc.api._engine import _get_engine
from openavc.api.host_policy import ProgrammerRoute
from openavc.api.models import ProblemReportRequest
from openavc.audit.report import ReportStore
from openavc.support import problem_report, report_upload
from openavc.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(route_class=ProgrammerRoute)

_discovery: Any = None
_store: ReportStore | None = None


def configure(discovery: Any, store: ReportStore | None = None) -> None:
    """Wire the routes to the discovery engine, whose community index is the
    driver library a report's driver files are checked against."""
    global _discovery, _store
    _discovery = discovery
    _store = store


def _report_store() -> ReportStore:
    global _store
    if _store is None:
        _store = problem_report.default_store()
    return _store


async def _catalog() -> list[dict[str, Any]] | None:
    index = getattr(_discovery, "community_index", None)
    if index is None:
        return None
    try:
        return await index.get_drivers()
    except Exception:
        log.debug("The driver library could not be read for a problem report", exc_info=True)
        return None


async def _snapshot(device_id: str) -> problem_report.Snapshot:
    try:
        return problem_report.Snapshot(_get_engine(), device_id, await _catalog())
    except problem_report.DeviceMissing:
        raise HTTPException(status_code=404, detail="That device is not in this project.") from None


async def send_saved(store: ReportStore, name: str) -> dict[str, Any]:
    """Send one saved report (either kind) through the upload door."""
    path = store.path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="That report is not on this server.")
    try:
        kind, report_id = report_upload.report_identity(path)
    except report_upload.ReportUnreadable as exc:
        log.warning("Not sending %s: %s", name, exc)
        return {"name": name, **report_upload.UploadResult(
            False, failure=report_upload.REFUSED,
        ).to_dict()}
    result = await report_upload.upload_report(path, kind, report_id)
    return {"name": name, "report_id": report_id, **result.to_dict()}


@router.get("/devices/{device_id}/problem-report")
async def problem_report_preview(device_id: str) -> dict[str, Any]:
    snapshot = await _snapshot(device_id)
    return {
        **problem_report.preview(snapshot),
        "report_url": report_upload.report_url(),
        "report_host": report_upload.report_host(),
        "max_bytes": report_upload.MAX_REPORT_BYTES,
    }


@router.post("/devices/{device_id}/problem-report")
async def create_problem_report(device_id: str, body: ProblemReportRequest) -> dict[str, Any]:
    snapshot = await _snapshot(device_id)
    return problem_report.save_report(
        snapshot,
        _report_store(),
        message=body.message,
        name=body.name,
        email=body.email,
        leave_out_serial=body.leave_out_serial,
        context=body.context.model_dump() if body.context is not None else None,
    )


@router.get("/problem-reports/{name}", response_model=None)
async def get_problem_report(name: str) -> FileResponse:
    path = _report_store().path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="That report is not on this server.")
    return FileResponse(
        path, media_type="application/zip", filename=name,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/problem-reports/{name}/send")
async def send_problem_report(name: str) -> dict[str, Any]:
    return await send_saved(_report_store(), name)
