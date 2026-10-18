"""Explicit HTTP policy registration, early authentication and response lifetime."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openavc.core.host_policy import AdmissionLease, HostCategory, HostPolicy, HostPolicyDenied
from openavc.utils.logger import get_logger


EarlyGuard = Callable[[Request], Awaitable[None]]
_ADMISSION = "openavc.host_admission"
_ENDPOINT_POLICY = "__openavc_endpoint_policy__"
log = get_logger(__name__)


@dataclass(frozen=True)
class EndpointPolicy:
    category: HostCategory
    authenticate: EarlyGuard | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", HostCategory(self.category))


def endpoint_policy(policy: EndpointPolicy):
    """Declare an exception to a router's explicit default policy."""
    def decorate(endpoint):
        if hasattr(endpoint, _ENDPOINT_POLICY):
            raise ValueError("Endpoint policy was already declared")
        setattr(endpoint, _ENDPOINT_POLICY, policy)
        return endpoint
    return decorate


def configure_policy(app, policy: HostPolicy) -> None:
    """Select a provider before application startup."""
    if getattr(app.state, "host_policy_started", False):
        raise RuntimeError("Host policy cannot change after application startup")
    if not isinstance(policy, HostPolicy):
        raise TypeError("Expected a HostPolicy provider")
    app.state.host_policy = policy


def application_policy(scope: Scope) -> HostPolicy:
    app = scope["app"]
    # Standalone routers and ordinary hosting retain permissive host settings.
    # Authentication remains mandatory wherever the route's guard requires it.
    if not hasattr(app.state, "host_policy"):
        app.state.host_policy = HostPolicy()
    policy = app.state.host_policy
    if not isinstance(policy, HostPolicy):
        raise TypeError("Invalid application host policy")
    return policy


def request_admission(request: Request) -> AdmissionLease:
    lease = request.scope.get(_ADMISSION)
    if not isinstance(lease, AdmissionLease):
        raise RuntimeError("This request has no registered host admission")
    return lease


async def _denied(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse(
        {"detail": "This interface is disabled by the product settings."},
        status_code=403, headers={"Cache-Control": "no-store"},
    )(scope, receive, send)


class HTTPPolicy:
    """Own admission through the complete ASGI response, including streams."""

    def __init__(self, app: ASGIApp, policy: EndpointPolicy):
        self.app = app
        self.endpoint_policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        policy = application_policy(scope)
        try:
            lease = policy.admit(self.endpoint_policy.category)
        except HostPolicyDenied:
            await _denied(scope, receive, send)
            return
        except Exception:
            log.exception("Reading the host interface policy failed")
            await JSONResponse(
                {"detail": "Product access settings are unavailable. Try again."},
                status_code=503, headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        scope[_ADMISSION] = lease
        started, complete, cancelled_by_host = False, False, False
        task = asyncio.current_task()
        assert task is not None

        def check() -> None:
            try:
                policy.refresh()
            except Exception as error:
                log.exception("Reading the host interface policy failed")
                lease.revoked.set()
                raise HostPolicyDenied("Host permission is unavailable") from error
            if lease.revoked.is_set():
                raise HostPolicyDenied("Host permission changed during the request")

        async def guarded_receive():
            check()
            message = await receive()
            check()
            return message

        async def guarded_send(message):
            nonlocal started, complete
            check()
            if message["type"] == "http.response.start":
                started = True
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                complete = True
            await send(message)

        async def watch():
            nonlocal cancelled_by_host
            while not lease.revoked.is_set():
                try:
                    await asyncio.wait_for(lease.revoked.wait(), 0.1)
                except TimeoutError:
                    try:
                        check()
                    except HostPolicyDenied:
                        break
            cancelled_by_host = True
            task.cancel()

        watcher = asyncio.create_task(watch())
        revoked = False
        try:
            try:
                await self.app(scope, guarded_receive, guarded_send)
            except HostPolicyDenied:
                revoked = True
            except asyncio.CancelledError:
                if not cancelled_by_host:
                    raise
                revoked = True
            finally:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass
            if revoked:
                if not started:
                    await _denied(scope, receive, send)
                elif not complete:
                    # A fixed-length response cannot be replaced after its headers;
                    # the server's short-body handling closes that transport.
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            lease.close()
            scope.pop(_ADMISSION, None)


class PolicyRoute(APIRoute):
    endpoint_policy: EndpointPolicy | None = None

    def __init__(self, path, endpoint, **kwargs):
        declared = getattr(endpoint, _ENDPOINT_POLICY, self.endpoint_policy)
        if not isinstance(declared, EndpointPolicy):
            raise ValueError("An HTTP route needs an explicit endpoint policy")
        self.endpoint_policy = declared
        super().__init__(path, endpoint, **kwargs)
        self.app = HTTPPolicy(self.app, declared)

    def get_route_handler(self):
        original = super().get_route_handler()
        declared = self.endpoint_policy
        assert declared is not None

        async def guarded(request: Request):
            if declared.authenticate is not None:
                await declared.authenticate(request)
            return await original(request)
        return guarded


def policy_route(policy: EndpointPolicy) -> type[PolicyRoute]:
    """Create a router default preserved when FastAPI includes that router."""
    class RegisteredPolicyRoute(PolicyRoute):
        endpoint_policy = policy
    return RegisteredPolicyRoute
