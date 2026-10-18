"""Explicit interface policy registration and HTTP/WebSocket lifetimes.

The canonical application installs HostPolicyMiddleware once. Hosts configure
their provider through ``openavc.main.configure_host_policy(provider)`` before
starting the application. Replacing it after lifespan starts is refused.

An APIRouter selects a PolicyRoute subclass for its default category and named
early authentication; endpoint_policy declares exceptions. PolicyMount declares
mounted applications. WebSocket authentication stays with the endpoint. Dynamic
plugin routers use include_policy_router, which preserves their public FastAPI
schema/dependencies and binds authority to the platform's registration.

The middleware owns complete response/socket lifetimes. Route guards authenticate
before FastAPI consumes a body. bind_request_authority can retain a successful
canonical credential check for streams and queued work. request_admission returns
the connection's lease; a durable operation must retain its separate commit lease
until its owner knows the outcome. HTTP completion is not a durability receipt.

Slash redirects use the target's declared policy; unknown operational routes are
refused. Disabled interfaces return non-cacheable 403 HTML/JSON or a WebSocket
4013 close. This supplements authentication and never grants it.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from contextlib import contextmanager

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute
from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Match, Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from openavc.api.auth import authenticate_programmer_request
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


def bind_request_authority(request: HTTPConnection, check: Callable[[], bool]) -> None:
    """Keep authenticated authority current through response/commit admission.

    Standalone router consumers without the host middleware still perform their
    ordinary authentication; only the canonical host owns connection accounting.
    """
    lease = request.scope.get(_ADMISSION)
    if isinstance(lease, AdmissionLease):
        lease.require_current(check)


def require_current_request(request: Request) -> None:
    """Revalidate admitted work immediately after an adapter's awaited gate."""
    lease = request.scope.get(_ADMISSION)
    if isinstance(lease, AdmissionLease) and not lease.revalidate():
        raise HostPolicyDenied("Request authority changed before the operation could start")


@contextmanager
def synchronous_response_commit(request: Request):
    """Own a synchronous save and retain only its completed acknowledgement.

    Use only around a block with no await: validation happens immediately before
    mutation and completion follows the checked settings save. A caller may then finish
    post-save notifications and send its result, even if it changed its own
    credential. This is not an owner for asynchronous/project transactions.
    """
    lease = request.scope.get(_ADMISSION)
    commit = lease.begin_commit() if isinstance(lease, AdmissionLease) else None
    try:
        yield
    except BaseException:
        if commit is not None:
            commit.finish("save failed")
        raise
    else:
        if commit is not None:
            commit.finish("saved")
            lease.complete_response(commit)


def interface_disabled_response(scope: Scope):
    """Return the product-settings refusal suitable for a browser or API client."""
    message = "This interface is disabled in product management."
    headers = {"Cache-Control": "no-store"}
    if "text/html" in HTTPConnection(scope).headers.get("accept", ""):
        return HTMLResponse(
            '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>OpenAVC interface disabled</title><style>body{font:18px system-ui;background:#f4f6f5;'
            'color:#192d28;max-width:640px;margin:12vh auto;padding:24px}p{line-height:1.6}</style>'
            '<h1>OpenAVC interface disabled</h1><p>' + message + '</p>'
            '<p>Use this product’s management interface to change access settings.</p></html>',
            status_code=403, headers=headers,
        )
    return JSONResponse({"detail": message, "code": "interface_disabled"}, status_code=403, headers=headers)


async def _denied(scope: Scope, receive: Receive, send: Send) -> None:
    lease = scope.get(_ADMISSION)
    if isinstance(lease, AdmissionLease) and lease.authority_revoked:
        await JSONResponse(
            {"detail": "Authentication is no longer valid. Sign in again.",
             "code": "authentication_required"},
            status_code=401, headers={"Cache-Control": "no-store"},
        )(scope, receive, send)
    else:
        await interface_disabled_response(scope)(scope, receive, send)


class HTTPPolicy:
    """Own admission through the complete ASGI response, including streams."""

    def __init__(self, app: ASGIApp, policy: EndpointPolicy):
        self.app = app
        self.endpoint_policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        policy = application_policy(scope)
        try:
            category = self.endpoint_policy.resolve(scope)
            lease = policy.admit(category, is_current=lambda: self.endpoint_policy.resolve(scope) == category)
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
                lease.refresh()
            except Exception as error:
                log.exception("Reading the host interface policy failed")
                lease.revoked.set()
                raise HostPolicyDenied("Host permission is unavailable") from error
            if lease.revoked.is_set():
                raise HostPolicyDenied("Host permission changed during the request")

        async def guarded_receive():
            if lease.response_only:
                raise HostPolicyDenied("The completed operation cannot consume more input")
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
            lease = scope.get(_ADMISSION)
            if isinstance(lease, AdmissionLease) and lease.authority_revoked:
                code = {HostCategory.PREVIEW: 4014, HostCategory.PANEL: 4010}.get(lease.category, 4001)
                reason = "Authentication is no longer valid. Reconnect to authenticate again."
            else:
                code, reason = 4013, "This interface is disabled by the product settings."
            await send({"type": "websocket.close", "code": code, "reason": reason})

        try:
            category = self.endpoint_policy.resolve(scope)
            lease = policy.admit(category, is_current=lambda: self.endpoint_policy.resolve(scope) == category)
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
                lease.refresh()
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
    allow_endpoint_override = True

    def __init__(self, path, endpoint, **kwargs):
        declared = (getattr(endpoint, _ENDPOINT_POLICY, self.endpoint_policy)
                    if self.allow_endpoint_override else self.endpoint_policy)
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


class ProgrammerRoute(PolicyRoute):
    endpoint_policy = EndpointPolicy(HostCategory.PROGRAMMER, authenticate_programmer_request)


class PublicProgrammerRoute(PolicyRoute):
    """Programmer shell/bootstrap routes keep their existing in-handler guards."""
    endpoint_policy = EndpointPolicy(HostCategory.PROGRAMMER)


class PanelRoute(PolicyRoute):
    endpoint_policy = EndpointPolicy(HostCategory.PANEL)


class RenderRoute(PolicyRoute):
    endpoint_policy = EndpointPolicy(HostCategory.RENDER)


class RuntimeRoute(PolicyRoute):
    endpoint_policy = EndpointPolicy(HostCategory.RUNTIME)


def include_policy_router(app, router: APIRouter, policy: EndpointPolicy, **kwargs) -> None:
    """Register externally supplied HTTP routes with the platform's policy.

    FastAPI's public inclusion API copies schema/response/dependency settings.
    Select its route class at that registration point so custom route handlers
    still run and host authentication precedes the copied body parser. A plugin
    cannot choose another host category through endpoint metadata.
    """
    class ClassifiedRouter(APIRouter):
        def add_api_route(self, path, endpoint, *, route_class_override=None, **options):
            original = route_class_override or APIRoute
            bases = ((original,) if issubclass(original, PolicyRoute)
                     else (PolicyRoute, original))
            classified = type("ClassifiedRoute", bases, {
                "endpoint_policy": policy, "allow_endpoint_override": False,
            })
            super().add_api_route(path, endpoint, route_class_override=classified, **options)

    prepared = ClassifiedRouter()
    prepared.include_router(router)
    previous = {id(route) for route in app.routes}
    app.include_router(prepared, **kwargs)
    for route in app.routes:
        if id(route) in previous:
            continue
        if getattr(route, "methods", None) is None:
            # WebSocket dependencies remain with FastAPI; HTTP pre-body guards
            # are not WebSocket authentication functions.
            route.endpoint_policy = EndpointPolicy(policy.category)
        else:
            route.endpoint_policy = policy
            if not isinstance(route, PolicyRoute) and policy.authenticate is not None:
                route.app = _EarlyAuthApp(route.app, policy.authenticate)


class _EarlyAuthApp:
    """Pre-body authentication for explicitly registered plain ASGI routes."""

    def __init__(self, app: ASGIApp, authenticate: EarlyGuard):
        self.app, self.authenticate = app, authenticate

    async def __call__(self, scope, receive, send):
        await self.authenticate(Request(scope, receive))
        await self.app(scope, receive, send)


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


def resolve_endpoint_policy(scope: Scope) -> EndpointPolicy:
    """Resolve declared authority for dispatch, method refusal or slash redirect."""
    def bound(route, child, overrides=None):
        declared = registered_policy(route)
        return EndpointPolicy(
            lambda original: declared.resolve({**original, **(overrides or {}), **child}),
            declared.authenticate,
        )

    partial = None
    for route in scope["app"].routes:
        match, child = route.matches(scope)
        if match is Match.FULL:
            return bound(route, child)
        if match is Match.PARTIAL and partial is None:
            partial = (route, child)
    if partial is not None:
        return bound(*partial)
    # Starlette only redirects after all full and method-only matches fail.
    # The redirect receives the target's registered policy without dispatching
    # that target or changing the scope the real router will see.
    router = scope["app"].router
    if scope["type"] == "http" and router.redirect_slashes and scope["path"] != "/":
        path = scope["path"]
        redirected = {"path": path.rstrip("/") if path.endswith("/") else path + "/"}
        for route in router.routes:
            match, child = route.matches({**scope, **redirected})
            if match is not Match.NONE:
                return bound(route, child, redirected)
    return EndpointPolicy(HostCategory.PROGRAMMER)


class HostPolicyMiddleware:
    """Own connection lifetime outside response/startup middleware.

    Route matching uses Starlette's public interface and the same declaration
    order. Authority comes solely from explicit registration metadata, never
    endpoint names, URL conventions or FastAPI's dependency internals. Ordinary
    redirects use their target's policy; unmatched responses use Programmer.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    def resolve(self, scope: Scope) -> EndpointPolicy:
        return resolve_endpoint_policy(scope)

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
