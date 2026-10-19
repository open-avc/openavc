"""Protected prepare/start/result access for device commissioning.

Every capability stays in a POST body. Current route admission and the original
prepare grant both guard actual response delivery, including the exact secret
generation when plaintext is returned. Connection completion never becomes a
durability receipt or permission to repeat the physical operation.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from openavc.api._engine import _get_engine
from openavc.api.auth import authenticated_programmer_grant
from openavc.api.host_policy import ProgrammerRoute, bind_response_availability, request_admission
from openavc.api.models import ActionInvokeRequest
from openavc.api.setup_transfer import active_transfer, browser_location, expire_cookie
from openavc.core.device_manager import DeviceNotFoundError
from openavc.core.outbound_messages import SetupScope
from openavc.core.setup_actions import SetupActionInProgress, SetupContractRequired
from openavc.core.setup_results import SetupCapacityError, SetupConflict, SetupUnavailable
from openavc.drivers.base import CommandParamError


class SetupRoute(ProgrammerRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def protected(request):
            try:
                response = await original(request)
            except RequestValidationError:
                # Pydantic error inputs can contain a supplied capability/key.
                response = JSONResponse({"detail": "Invalid setup request"}, status_code=422)
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            except (SetupUnavailable, SetupConflict, SetupCapacityError,
                    SetupContractRequired, SetupActionInProgress, DeviceNotFoundError, CommandParamError) as exc:
                if isinstance(exc, SetupUnavailable):
                    code, detail = 410, "This setup result is unavailable. Do not repeat the action without checking the device."
                elif isinstance(exc, DeviceNotFoundError):
                    code, detail = 404, "The setup device is no longer available."
                elif isinstance(exc, SetupCapacityError):
                    code, detail = 429, "Setup result capacity is full. Finish an existing attempt before starting another."
                elif isinstance(exc, SetupContractRequired):
                    code, detail = 409, "This installed driver needs a qualified setup result contract. No action was started."
                elif isinstance(exc, CommandParamError):
                    code, detail = 400, "Check the setup action's required inputs. No action was started."
                else:
                    code, detail = 409, "This setup attempt cannot start with the current inputs or device state."
                response = JSONResponse({"detail": detail}, status_code=code)
            response.headers["Cache-Control"] = "no-store"
            return response
        return protected


router = APIRouter(route_class=SetupRoute)


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)


class StartRequest(CapabilityRequest):
    params: dict = Field(default_factory=dict, repr=False)


class AcknowledgeRequest(CapabilityRequest):
    generation: int = Field(strict=True, ge=1)


class SubscriptionRequest(CapabilityRequest):
    connection_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscardTransferRequest(TransferRequest):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")


async def _current(request: Request):
    grant = await authenticated_programmer_grant(request)
    lease = request_admission(request)
    return grant, lease


def _bind_result(request, runner, capability, *, generation=None):
    bind_response_availability(request, runner.results.access_guard(capability, secret_generation=generation))


def _transfer_location(request):
    if request.headers.get("x-openavc-setup-transfer") != "1":
        raise HTTPException(400, "Refresh this page to continue setup.")
    return browser_location(request)


@router.post("/setup-actions/transfer")
async def recover_setup_transfer(body: TransferRequest, request: Request):
    location = _transfer_location(request)
    _, lease = await _current(request)
    runner = _get_engine().setup_actions
    active, stale = active_transfer(request, runner, location, lease.revalidate)
    if active is None:
        response = JSONResponse({"pending": None, "cleared": bool(stale)})
        if stale:
            expire_cookie(response, stale[0], location)
        return response
    _, ticket, item, frozen = active
    # Revalidate the retained selection and exact target without resolving a new
    # adapter or exposing its private configuration/source binding.
    runner.prepare_transfer(frozen["device_id"], frozen["action_id"], frozen["params"],
        origin=location.origin, api_path=location.api_path, ticket=ticket,
        original_grant=lambda: False, current=lease.revalidate)
    bind_response_availability(request, runner.results.transfer_guard(
        ticket, origin=location.origin, api_path=location.api_path))
    return {"pending": {"run_id": item.run_id, "capability": item.capability,
                        "status": "prepared", "device_id": frozen["device_id"],
                        "action": frozen["action"], "params": frozen["params"]}, "cleared": False}


@router.post("/setup-actions/discard-transfer")
async def discard_setup_transfer(body: DiscardTransferRequest, request: Request):
    location = _transfer_location(request)
    _, lease = await _current(request)
    runner = _get_engine().setup_actions
    active, _ = active_transfer(request, runner, location, lease.revalidate)
    if active is None or active[2].run_id != body.run_id:
        raise SetupUnavailable("This unstarted setup input is no longer available")
    name, ticket, _, _ = active
    runner.results.discard_transfer(ticket, origin=location.origin, api_path=location.api_path,
                                    current=lease.revalidate)
    response = JSONResponse({"discarded": body.run_id})
    expire_cookie(response, name, location)
    return response


@router.post("/devices/{device_id}/actions/{action_id}/prepare")
async def prepare_setup(device_id: str, action_id: str, body: ActionInvokeRequest, request: Request):
    grant, lease = await _current(request)
    # Retain public provider/snapshot identity and the original grant, never the
    # HTTP lease that closes as soon as this prepare response is complete.
    provider, snapshot, category = lease.policy, lease.snapshot, lease.category

    def original():
        return grant() is True and snapshot.allows(category) and provider.snapshot() == snapshot

    runner = _get_engine().setup_actions
    prepared = runner.prepare(device_id, action_id, body.params, original_grant=original, current=lease.revalidate)
    _bind_result(request, runner, prepared["capability"])
    return prepared


@router.post("/devices/{device_id}/actions/{action_id}/start")
async def start_setup(device_id: str, action_id: str, body: StartRequest, request: Request):
    _, lease = await _current(request)
    runner = _get_engine().setup_actions
    result = await runner.start(device_id, {"id": action_id}, body.params,
                                capability=body.capability, current=lease.revalidate)
    _bind_result(request, runner, body.capability)
    return result


@router.post("/setup-actions/status")
async def setup_status(body: CapabilityRequest, request: Request):
    _, lease = await _current(request)
    runner = _get_engine().setup_actions
    result = runner.results.status(body.capability, current=lease.revalidate)
    _bind_result(request, runner, body.capability)
    return result


@router.post("/setup-actions/retrieve")
async def retrieve_setup(body: CapabilityRequest, request: Request):
    _, lease = await _current(request)
    runner = _get_engine().setup_actions
    result = runner.results.retrieve(body.capability, current=lease.revalidate)
    _bind_result(request, runner, body.capability, generation=result["generation"])
    return result


@router.post("/setup-actions/acknowledge")
async def acknowledge_setup(body: AcknowledgeRequest, request: Request):
    _, lease = await _current(request)
    runner = _get_engine().setup_actions
    runner.results.acknowledge(body.capability, body.generation, current=lease.revalidate)
    _bind_result(request, runner, body.capability)
    return {"acknowledged": body.generation}


def _connection(hub, connection_id):
    try:
        return hub.programmer_connection(connection_id)
    except Exception:
        raise SetupUnavailable("The Programmer connection is no longer available") from None


@router.post("/setup-actions/subscribe")
async def subscribe_setup(body: SubscriptionRequest, request: Request):
    _, lease = await _current(request)
    engine = _get_engine()
    runner, hub = engine.setup_actions, engine.ws
    status = runner.results.status(body.capability, current=lease.revalidate)
    identity = runner.results.request_data(body.capability, current=lease.revalidate)
    device_id, action_id = identity["device_id"], identity["action_id"]
    del identity
    connection = _connection(hub, body.connection_id)
    guard = runner.results.access_guard(body.capability)
    if not lease.revalidate() or not guard():
        raise SetupUnavailable("Setup permission changed before subscription")
    scope = hub.open_scope(connection, scope_type=SetupScope, current=guard)

    def publish():
        update = runner.results.status(body.capability, current=guard)
        hub.send(connection, {"type": "action.progress", "device_id": device_id,
                              "action_id": action_id, **update}, scope=scope)

    remove = runner.observe(status["run_id"], publish)

    def cleanup():
        hub.close_scope(connection, scope)
        remove()

    try:
        hub.replace_subscription(connection, "setup", status["run_id"], cleanup)
        # Install then snapshot without a yield: an early terminal event cannot
        # fall between subscription and reconciliation. Neither carries secrets.
        status = runner.results.status(body.capability, current=lease.revalidate)
        _bind_result(request, runner, body.capability)
        return status
    except BaseException:
        cleanup()
        raise


@router.post("/setup-actions/unsubscribe")
async def unsubscribe_setup(body: SubscriptionRequest, request: Request):
    _, lease = await _current(request)
    engine = _get_engine()
    # Possessing a routing ID cannot tear down somebody else's progress stream.
    status = engine.setup_actions.results.status(body.capability, current=lease.revalidate)
    connection = _connection(engine.ws, body.connection_id)
    engine.ws.close_subscription(connection, "setup", status["run_id"])
    _bind_result(request, engine.setup_actions, body.capability)
    return {"unsubscribed": True}
