"""Device Audit over HTTP: ``/api/audit``.

On the authenticated router: an audit sends traffic to an address the
programmer chooses and pauses project devices, the same privilege as adding a
device. Writes sit on the control rate tier with the other commissioning
routes. Every rule lives in ``openavc/audit/``; this module parses, calls, and
turns a refusal into its sentence.

- ``GET /audit/conflicts?address=``: the project devices that connect to that
  host (``core.device_config.devices_at_host``), to pause before anything is
  sent.
- ``POST /audit/sessions``, ``GET /audit/sessions/current``,
  ``DELETE /audit/sessions/{id}`` (``?cancel=true`` for Cancel).
- ``POST /audit/sessions/{id}/network-check``;
  ``PATCH /audit/sessions/{id}/tester``.
- ``POST /audit/sessions/{id}/driver`` (the driver chosen, or none yet) and
  ``POST /audit/sessions/{id}/next-driver`` ("Test another driver").
- ``GET /audit/sessions/{id}/saved-settings`` (paused project devices whose
  settings the chosen driver can use, secrets withheld) and
  ``POST /audit/sessions/{id}/connection`` (the settings, and what connecting
  will send).
- ``POST /audit/sessions/{id}/connect`` (connect and listen),
  ``POST /audit/sessions/{id}/listen/extend`` ("Keep listening") and
  ``POST /audit/sessions/{id}/front-panel`` (the front-panel check's answer).
- ``GET /audit/sessions/{id}/report`` (the zip, also kept in recent reports;
  ``?format=json`` for the record itself).
- ``GET /audit/reports``, ``GET`` and ``DELETE /audit/reports/{name}``.

Live progress is the WebSocket's ``audit.subscribe`` (``api/ws.py``), sent to
the subscribing client only.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from openavc.api._engine import _get_engine
from openavc.api.models import (
    AuditConnectionRequest,
    AuditDriverRequest,
    AuditFrontPanelRequest,
    AuditStartRequest,
    AuditTesterRequest,
)
from openavc.audit.footprint import open_for_session, resolve_address, start_check
from openavc.audit.listen import start_listen
from openavc.audit.passes import (
    choose_driver,
    current_run,
    next_driver,
    open_runs,
    saved_settings,
    set_connection,
)
from openavc.audit.sandbox import unpaused_devices_at
from openavc.audit.report import (
    ReportStore,
    build_report,
    default_store,
    save_session_report,
)
from openavc.audit.session import (
    CANCELLED,
    FINISHED,
    AuditBusy,
    AuditError,
    AuditManager,
    AuditNotFound,
    AuditOptions,
    AuditSession,
    AuditTarget,
)
from openavc.core.device_config import devices_at_host
from openavc.core.device_manager import DeviceNotFoundError
from openavc.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/audit")

_manager: AuditManager | None = None
_discovery: Any = None
_store: ReportStore | None = None


def configure(manager: AuditManager, discovery: Any, store: ReportStore | None = None) -> None:
    """Wire the audit routes to the server's manager and discovery engine.

    Also makes every session save its report when it ends.
    """
    global _manager, _discovery, _store
    _manager = manager
    _discovery = discovery
    _store = store

    async def save_at_end(session: AuditSession) -> None:
        await save_session_report(session, _report_store())

    manager.add_end_hook(save_at_end)


def manager_or_none() -> AuditManager | None:
    return _manager


def _get_manager() -> AuditManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="Device audits are not available yet.")
    return _manager


def _report_store() -> ReportStore:
    global _store
    if _store is None:
        _store = default_store()
    return _store


def _session(session_id: str) -> AuditSession:
    try:
        session = _get_manager().get(session_id)
    except AuditNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    session.touch()
    return session


def _device_rows(engine: Any, names: list[str]) -> list[dict[str, Any]]:
    project = getattr(engine, "project", None)
    if project is None:
        return []
    rows = []
    for conn in devices_at_host(project, names):
        row = conn.to_dict()
        row["connected"] = bool(engine.state.get(f"device.{conn.device_id}.connected"))
        row["paused"] = bool(engine.state.get(f"device.{conn.device_id}.paused"))
        rows.append(row)
    return rows


@router.get("/conflicts")
async def audit_conflicts(address: str) -> dict[str, Any]:
    """The project devices that connect to this address."""
    address = address.strip()
    if not address:
        raise HTTPException(status_code=422, detail="Enter the device's IP address or host name.")
    ip = await resolve_address(address)
    names = [address] + ([ip] if ip and ip != address else [])
    return {
        "address": address,
        "ip": ip,
        "resolved": bool(ip),
        "devices": _device_rows(_get_engine(), names),
    }


@router.post("/sessions")
async def start_session(body: AuditStartRequest) -> dict[str, Any]:
    """Start an audit of ``address``, pausing the listed project devices."""
    manager = _get_manager()
    address = body.address.strip()
    ip = await resolve_address(address)
    if not ip:
        raise HTTPException(
            status_code=400,
            detail=(
                f"OpenAVC could not find {address}. Check the spelling, or enter the "
                "device's IP address."
            ),
        )
    engine = _get_engine()
    project = getattr(engine, "project", None)
    names = {d.id: d.name for d in project.devices} if project is not None else {}
    for device_id in body.pause:
        if device_id not in names:
            raise HTTPException(
                status_code=404, detail=f"No device named '{device_id}' in this project.",
            )
    try:
        session = await manager.start(
            AuditTarget(address=address, ip=ip),
            AuditOptions(extended=body.extended, snmp_communities=list(body.snmp_communities)),
            pause=[(device_id, names[device_id]) for device_id in body.pause],
        )
    except AuditBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except AuditError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DeviceNotFoundError as exc:
        # In the project but not running (disabled, or its driver is missing).
        log.warning("Could not pause project devices for an audit: %s", exc)
        raise HTTPException(
            status_code=409,
            detail="A project device at this address could not be paused, so the audit did "
                   "not start. Check that it is enabled, then try again.",
        )
    try:
        await open_for_session(session, _discovery)
        open_runs(session)
    except Exception:
        await manager.finish(session.id, CANCELLED)
        raise
    return {"session": session.to_dict()}


@router.get("/sessions/current")
async def current_session() -> dict[str, Any]:
    """The running audit, if there is one, as the wizard draws it."""
    session = _get_manager().current()
    if session is not None:
        session.touch()
    return {"session": session.to_dict() if session is not None else None}


@router.delete("/sessions/{session_id}")
async def end_session(session_id: str, cancel: bool = False) -> dict[str, Any]:
    """Finish (or cancel) the audit: stop it, reconnect what it paused."""
    session = _session(session_id)
    await _get_manager().finish(session.id, CANCELLED if cancel else FINISHED)
    return {"session": session.to_dict()}


@router.post("/sessions/{session_id}/network-check")
async def run_network_check(session_id: str) -> dict[str, Any]:
    """Start the network check; progress arrives over ``audit.subscribe``."""
    session = _session(session_id)
    try:
        start_check(session)
    except AuditError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"session": session.to_dict()}


async def _catalog() -> list[dict[str, Any]] | None:
    """The community catalog as last fetched (the network check refreshed it)."""
    index = getattr(_discovery, "community_index", None)
    if index is None:
        return None
    try:
        return await index.get_drivers()
    except Exception:
        log.warning("Could not read the driver catalog for an audit", exc_info=True)
        return None


@router.post("/sessions/{session_id}/driver")
async def set_driver(session_id: str, body: AuditDriverRequest) -> dict[str, Any]:
    """Record the driver chosen for the test, or that there is none yet."""
    session = _session(session_id)
    if session.check is None or session.check.status == "idle":
        raise HTTPException(status_code=409, detail="Run the network check first.")
    try:
        choose_driver(
            session, body.driver_id,
            manufacturer=body.manufacturer, model=body.model, firmware=body.firmware,
            catalog=await _catalog() if body.driver_id else None,
        )
    except AuditError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    session.publish_state()
    return {"session": session.to_dict()}


@router.post("/sessions/{session_id}/next-driver")
async def test_another_driver(session_id: str) -> dict[str, Any]:
    """Finish with the current driver (its results stay) to choose another."""
    session = _session(session_id)
    await next_driver(session)
    session.publish_state()
    return {"session": session.to_dict()}


@router.get("/sessions/{session_id}/saved-settings")
async def get_saved_settings(session_id: str) -> dict[str, Any]:
    """Paused project devices on the chosen driver, their settings without
    the secrets (which are named, never sent)."""
    session = _session(session_id)
    return {"devices": saved_settings(session, getattr(_get_engine(), "project", None))}


@router.post("/sessions/{session_id}/connection")
async def set_session_connection(session_id: str, body: AuditConnectionRequest) -> dict[str, Any]:
    """The connection settings for the chosen driver, and what connecting sends."""
    session = _session(session_id)
    try:
        await set_connection(
            session, dict(body.config), use_saved=body.use_saved,
            project=getattr(_get_engine(), "project", None),
        )
    except AuditError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    session.publish_state()
    return {"session": session.to_dict()}


def _listening_run(session: AuditSession):
    run = current_run(session)
    if run is None or run.listen is None:
        raise HTTPException(status_code=409, detail="Connect the driver first.")
    return run


@router.post("/sessions/{session_id}/connect")
async def connect_and_listen(session_id: str) -> dict[str, Any]:
    """Connect the chosen driver and listen; progress arrives over
    ``audit.subscribe``."""
    session = _session(session_id)
    run = current_run(session)
    if run is None:
        raise HTTPException(status_code=409, detail="Choose the driver to test first.")
    engine = _get_engine()
    names = [session.target.address] + (
        [session.target.ip] if session.target.ip != session.target.address else []
    )
    running = unpaused_devices_at(getattr(engine, "project", None), engine.state, names)
    if running:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{running[0]} in this project uses this device and is not paused. Pause it "
                "on its device page, or finish this audit and start a new one, which pauses it."
            ),
        )
    try:
        await start_listen(session, run)
    except AuditError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    session.publish_state()
    return {"session": session.to_dict()}


@router.post("/sessions/{session_id}/listen/extend")
async def keep_listening(session_id: str) -> dict[str, Any]:
    """Listen for another minute (five minutes at most in all)."""
    session = _session(session_id)
    run = _listening_run(session)
    try:
        run.listen.extend()
    except AuditError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    session.publish_state()
    return {"session": session.to_dict()}


@router.post("/sessions/{session_id}/front-panel")
async def front_panel_check(session_id: str, body: AuditFrontPanelRequest) -> dict[str, Any]:
    """Record whether OpenAVC showed a change made on the device itself."""
    session = _session(session_id)
    run = _listening_run(session)
    run.listen.answer_front_panel(body.answer, body.note.strip())
    session.publish_state()
    return {"session": session.to_dict()}


@router.patch("/sessions/{session_id}/tester")
async def set_tester(session_id: str, body: AuditTesterRequest) -> dict[str, Any]:
    """Record who ran the audit, for the report. Every field is optional."""
    session = _session(session_id)
    session.tester = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in body.model_dump().items()
        if value not in ("", None)
    }
    session.enter_step("report")
    return {"session": session.to_dict()}


@router.get("/sessions/{session_id}/report", response_model=None)
async def session_report(
    session_id: str, format: Literal["zip", "json"] = "zip",
) -> Any:
    """The report as it stands: the zip (also kept), or the record as JSON."""
    session = _session(session_id)
    if session.check is None or session.check.status == "idle":
        raise HTTPException(status_code=409, detail="Run the network check first.")
    session.enter_step("report")
    if format == "json":
        return build_report(session)
    store = _report_store()
    name = await save_session_report(session, store)
    path = store.path(name) if name else None
    if path is None:
        raise HTTPException(status_code=409, detail="There is nothing to report yet.")
    return FileResponse(
        path, media_type="application/zip", filename=name,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/reports")
async def list_reports() -> dict[str, Any]:
    """The recent reports kept on this server, newest first."""
    return {"reports": _report_store().list()}


@router.get("/reports/{name}", response_model=None)
async def get_report(name: str) -> FileResponse:
    path = _report_store().path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="That report is not on this server.")
    return FileResponse(
        path, media_type="application/zip", filename=name,
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/reports/{name}")
async def delete_report(name: str) -> dict[str, Any]:
    if not _report_store().delete(name):
        raise HTTPException(status_code=404, detail="That report is not on this server.")
    return {"deleted": name}
