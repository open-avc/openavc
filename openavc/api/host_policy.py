"""Explicit interface policy registration and HTTP/WebSocket lifetimes."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.routing import Match, Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from openavc.core.host_policy import AdmissionLease, HostCategory, HostPolicy, HostPolicyDenied
from openavc.utils.logger import get_logger


EarlyGuard = Callable[[Request], Awaitable[None]]
CategoryResolver = Callable[[Scope], HostCategory]
_ADMISSION = "openavc.host_admission"
_ENDPOINT_POLICY = "__openavc_endpoint_policy__"
log = get_logger(__name__)


@dataclass(frozen=True)
class EndpointPolicy:
    category: HostCategory | CategoryResolver
    authenticate: EarlyGuard | None = None

    def __post_init__(self) -> None:
        if not callable(self.category):
            object.__setattr__(self, "category", HostCategory(self.category))

    def resolve(self, scope: Scope) -> HostCategory:
        """Resolve only through a registered adapter's canonical authority check."""
        return HostCategory(self.category(scope) if callable(self.category) else self.category)


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
            lease = policy.admit(self.endpoint_policy.resolve(scope))
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
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                complete = True

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


class WebSocketPolicy:
    """Enforce a registered WebSocket category through handshake and cleanup.

    The wrapped endpoint retains its canonical credential and panel checks.
    Interface refusal is accepted then closed so browser clients receive the
    existing product-settings close code, including when a subprotocol is used.
    """

    def __init__(self, app: ASGIApp, policy: EndpointPolicy):
        if policy.authenticate is not None:
            raise ValueError("WebSocket authentication belongs to its canonical adapter")
        self.app = app
        self.endpoint_policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        policy = application_policy(scope)
        accepted, closed, disconnected, connected = False, False, False, False

        async def close():
            if closed or disconnected:
                return
            if not accepted:
                if not connected:
                    message = await receive()
                    if message["type"] == "websocket.disconnect":
                        return
                # Use the same offered credential subprotocol as the normal
                # endpoint. Never invent an unoffered protocol on refusal.
                from openavc.api.auth import get_ws_auth_subprotocol
                subprotocol = get_ws_auth_subprotocol(dict(HTTPConnection(scope).headers))
                await send({"type": "websocket.accept", "subprotocol": subprotocol})
            await send({"type": "websocket.close", "code": 4013,
                        "reason": "This interface is disabled by the product settings."})

        try:
            lease = policy.admit(self.endpoint_policy.resolve(scope))
        except HostPolicyDenied:
            await close()
            return
        except Exception:
            log.exception("Reading the host WebSocket policy failed")
            await close()
            return
        scope[_ADMISSION] = lease
        task = asyncio.current_task()
        assert task is not None
        cancelled_by_host = False

        def check():
            try:
                policy.refresh()
            except Exception as error:
                log.exception("Reading the host WebSocket policy failed")
                lease.revoked.set()
                raise HostPolicyDenied("Host permission is unavailable") from error
            if lease.revoked.is_set():
                raise HostPolicyDenied("Host permission changed during the connection")

        async def guarded_receive():
            nonlocal disconnected, connected
            check()
            message = await receive()
            if message["type"] == "websocket.connect":
                connected = True
            if message["type"] == "websocket.disconnect":
                disconnected = True
                return message
            check()
            return message

        async def guarded_send(message):
            nonlocal accepted, closed
            # Permit the endpoint's canonical cleanup close after revocation.
            if message["type"] != "websocket.close":
                check()
            if message["type"] == "websocket.accept":
                accepted = True
            await send(message)
            if message["type"] == "websocket.close":
                closed = True

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
                await close()
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


class PolicyMount(Mount):
    """A mounted ASGI application with an explicit host interface contract."""

    def __init__(self, path, app, policy: EndpointPolicy, **kwargs):
        self.endpoint_policy = policy
        super().__init__(path, app=app, **kwargs)


def registered_policy(route) -> EndpointPolicy:
    declared = getattr(route, "endpoint_policy", None)
    if declared is None:
        declared = getattr(getattr(route, "endpoint", None), _ENDPOINT_POLICY, None)
    if not isinstance(declared, EndpointPolicy):
        raise ValueError(f"Route {route.path!r} has no explicit host policy")
    return declared


def validate_policies(app) -> None:
    """Refuse startup with an unclassified operational route or mount."""
    for route in app.routes:
        registered_policy(route)


class HostPolicyMiddleware:
    """Own connection lifetime outside response/startup middleware.

    Route matching uses Starlette's public interface and the same declaration
    order. Authority comes solely from explicit registration metadata, never
    endpoint names, URL conventions or FastAPI's dependency internals. Ordinary
    no-match/redirect responses retain the Programmer surface's policy.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    def resolve(self, scope: Scope) -> EndpointPolicy:
        partial = None
        for route in scope["app"].routes:
            match, child = route.matches(scope)
            if match is Match.FULL:
                declared = registered_policy(route)
                # Give resolvers the matched parameters without changing the
                # router's input: mutating root_path here would strip a mount
                # prefix a second time when the actual router dispatches.
                return EndpointPolicy(
                    lambda original: declared.resolve({**original, **child}),
                    declared.authenticate,
                )
            if match is Match.PARTIAL and partial is None:
                partial = route
        if partial is not None:
            return registered_policy(partial)
        return EndpointPolicy(HostCategory.PROGRAMMER)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        try:
            declared = self.resolve(scope)
        except ValueError:
            log.exception("An interface route has no registered host policy")
            if scope["type"] == "http":
                await JSONResponse(
                    {"detail": "This interface is unavailable."}, status_code=503,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1011})
            return
        wrapper = HTTPPolicy if scope["type"] == "http" else WebSocketPolicy
        await wrapper(self.app, declared)(scope, receive, send)
