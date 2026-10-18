"""Policy metadata follows the final routing table, not endpoint names."""
import asyncio

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, WebSocket
from fastapi.routing import APIRoute
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from starlette.staticfiles import StaticFiles

from openavc.api.host_policy import (
    EndpointPolicy, HostPolicyMiddleware, PolicyMount, configure_policy,
    endpoint_policy, include_policy_router, policy_route, validate_policies,
)
from openavc.core.host_policy import HostCategory, HostPolicy, PolicySnapshot


class MutableHost(HostPolicy):
    def __init__(self):
        super().__init__()
        self.current = PolicySnapshot(1, True, True)

    def snapshot(self):
        return self.current


def application():
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    host = MutableHost()
    configure_policy(app, host)
    app.add_middleware(HostPolicyMiddleware)
    return app, host


def test_nested_websocket_registration_preserves_dependencies_and_path_parameters():
    app, host = application()
    called = []

    async def credential():
        called.append("auth")

    router = APIRouter()

    @router.websocket("/control/{device_id}")
    @endpoint_policy(EndpointPolicy(HostCategory.PANEL))
    async def socket(ws: WebSocket, device_id: str, auth=Depends(credential)):
        await ws.accept()
        await ws.send_json({"device": device_id})
        await ws.close(code=1000)

    outer = APIRouter()
    outer.include_router(router, prefix="/nested")
    app.include_router(outer, prefix="/api")
    validate_policies(app)
    with TestClient(app) as client:
        with client.websocket_connect("/api/nested/control/amplifier") as ws:
            assert ws.receive_json() == {"device": "amplifier"}
        host.current = PolicySnapshot(2, True, False)
        with client.websocket_connect("/api/nested/control/amplifier") as ws:
            with pytest.raises(WebSocketDisconnect) as result:
                ws.receive_json()
            assert result.value.code == 4013
    assert called == ["auth"]  # Disabled access never enters dependency resolution.


def test_static_mount_keeps_prefix_redirect_file_response_and_policy(tmp_path):
    (tmp_path / "index.html").write_text("panel shell")
    (tmp_path / "panel.js").write_text("panel asset")
    app, host = application()

    def category(scope):
        # Starlette supplies the matched root_path while preserving path.
        assert scope["root_path"] == "/panel"
        return HostCategory.PANEL if scope["path"].endswith("/") else HostCategory.RENDER

    app.router.routes.append(PolicyMount(
        "/panel", StaticFiles(directory=tmp_path, html=True), EndpointPolicy(category), name="panel",
    ))
    validate_policies(app)
    with TestClient(app) as client:
        assert client.get("/panel", follow_redirects=False).status_code == 307
        assert client.get("/panel/").text == "panel shell"
        assert client.get("/panel/panel.js").text == "panel asset"
        host.current = PolicySnapshot(2, True, False)
        assert client.get("/panel/").status_code == 403
        assert client.get("/panel/panel.js").text == "panel asset"
        host.current = PolicySnapshot(3, False, True)
        assert client.get("/panel", follow_redirects=False).status_code == 307
        assert client.get("/panel").text == "panel shell"
        host.current = PolicySnapshot(3, False, False)
        denied = client.get("/panel/panel.js", headers={"Accept": "text/html"})
        assert denied.status_code == 403
        assert denied.headers["content-type"].startswith("text/html")
        assert denied.headers["cache-control"] == "no-store"
        assert client.get("/panel/").json()["code"] == "interface_disabled"


def test_unclassified_route_cannot_use_runtime_path_or_claimed_name_to_bypass():
    app, host = application()

    @app.post("/health", name="runtime")
    async def health():
        raise AssertionError("Unclassified endpoint executed")

    with pytest.raises(ValueError, match="no explicit host policy"):
        validate_policies(app)
    with TestClient(app) as client:
        assert client.post("/health", content=b"untrusted body").status_code == 503


def test_no_match_and_method_refusals_preserve_programmer_policy_and_runtime_route():
    app, host = application()
    router = APIRouter(route_class=policy_route(EndpointPolicy(HostCategory.RUNTIME)))

    @router.get("/health")
    async def health():
        return {"ok": True}

    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/absent").status_code == 404
        assert client.post("/health").status_code == 405
        host.current = PolicySnapshot(2, False, False)
        assert client.get("/absent").status_code == 403
        assert client.get("/health").json() == {"ok": True}
        assert client.post("/health").status_code == 405
    assert asyncio.run(host.apply_current(timeout=0)).observed


def test_external_router_keeps_custom_handler_and_schema_with_platform_authority():
    app, host = application()
    calls = []

    class CustomRoute(APIRoute):
        def get_route_handler(self):
            original = super().get_route_handler()

            async def wrapped(request):
                calls.append("custom")
                return await original(request)
            return wrapped

    async def authenticate(request):
        calls.append("authenticate")
        if request.headers.get("x-example-credential") != "allowed":
            raise HTTPException(401)

    router = APIRouter(route_class=CustomRoute)

    @router.post("/write")
    @endpoint_policy(EndpointPolicy(HostCategory.RUNTIME))
    async def write(value: dict = Body()):
        calls.append("endpoint")
        return value

    include_policy_router(app, router, EndpointPolicy(HostCategory.PROGRAMMER, authenticate), prefix="/extension")
    validate_policies(app)
    assert "requestBody" in app.openapi()["paths"]["/extension/write"]["post"]
    with TestClient(app) as client:
        # Invalid JSON would produce 422 if the parser ran before this guard.
        assert client.post("/extension/write", content=b"not json").status_code == 401
        assert calls == ["authenticate"]
        calls.clear()
        assert client.post("/extension/write", json={"value": 7},
                           headers={"x-example-credential": "allowed"}).json() == {"value": 7}
        assert calls == ["authenticate", "custom", "endpoint"]
        host.current = PolicySnapshot(2, False, True)
        calls.clear()
        assert client.post("/extension/write", json={}).status_code == 403
        assert not calls
