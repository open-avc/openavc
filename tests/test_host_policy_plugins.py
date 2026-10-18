"""Plugin tokens, declared panel paths and stopped registration lifetimes."""
import asyncio

from fastapi import APIRouter, Body, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from starlette.responses import StreamingResponse

from openavc.api import plugin_ext
from openavc.api.host_policy import HostPolicyMiddleware, configure_policy, validate_policies
from openavc.api.plugin_ext import (
    mint_panel_token, mint_plugin_token, mount_plugin_guest_router,
    mount_plugin_router, unmount_plugin_guest_router, unmount_plugin_router,
)
from openavc.core.host_policy import HostPolicy, PolicySnapshot


class MutableHost(HostPolicy):
    current = PolicySnapshot(1, True, True)

    def snapshot(self):
        return self.current


@pytest.fixture
def product(isolated_auth_config):
    isolated_auth_config.set("auth", "programmer_username", "engineer")
    isolated_auth_config.set("auth", "programmer_password", "disposable-plugin-password")
    isolated_auth_config.set("auth", "api_key", "")
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    host = MutableHost()
    configure_policy(app, host)
    app.add_middleware(HostPolicyMiddleware)
    yield app, host
    unmount_plugin_router(app, "policy_example")
    unmount_plugin_guest_router(app, "policy_example")


def test_plugin_scope_and_guest_alias_keep_distinct_authority(product):
    app, host = product
    router, guest, effects = APIRouter(), APIRouter(), []

    @router.get("/view")
    async def view():
        return {"value": 12}

    @router.post("/configure")
    async def configure(value: dict = Body()):
        effects.append(value)
        return {"saved": True}

    @guest.get("/view")
    async def guest_view(key: str = ""):
        if key != "example-resource-key":
            raise HTTPException(401)
        return {"guest": True}

    mount_plugin_router(app, "policy_example", router, panel_paths=["GET /view"])
    mount_plugin_guest_router(app, "policy_example", guest, alias="policy-example-guest")
    validate_policies(app)
    panel = {"x-openavc-plugin-token": mint_panel_token("policy_example")[0]}
    programmer = {"x-openavc-plugin-token": mint_plugin_token("policy_example")[0]}
    root = "/api/plugins/policy_example"
    with TestClient(app) as client:
        assert client.get(root + "/ext/view", headers=panel).json() == {"value": 12}
        assert client.post(root + "/ext/configure", headers=panel, content=b"not json").status_code == 401
        assert not effects
        assert client.post(root + "/ext/configure", headers=programmer, json={"value": 7}).status_code == 200
        assert effects == [{"value": 7}]
        host.current = PolicySnapshot(2, False, True)
        assert client.get(root + "/ext/view", headers=panel).status_code == 200
        assert client.get(root + "/ext/view", headers=programmer).status_code == 403
        assert client.post(root + "/ext/configure", headers=panel, content=b"not json").status_code == 403
        assert effects == [{"value": 7}]
        host.current = PolicySnapshot(3, False, False)
        assert client.get(root + "/ext/view", headers=panel).status_code == 403
        for prefix in [root + "/guest", "/policy-example-guest"]:
            assert client.get(prefix + "/view").status_code == 401
            assert client.get(prefix + "/view?key=example-resource-key").json() == {"guest": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["ext", "guest", "alias"])
async def test_stopped_plugin_stream_cannot_inherit_replacement_registration(
    product, surface, isolated_auth_config,
):
    app, host = product
    router, replacement = APIRouter(), APIRouter()
    started, closed = asyncio.Event(), asyncio.Event()
    continue_stream, continued = asyncio.Event(), asyncio.Event()

    @router.get("/view")
    async def stream():
        async def chunks():
            try:
                yield b"old registration"
                started.set()
                await continue_stream.wait()
                yield b"same panel or guest authority"
                continued.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
        return StreamingResponse(chunks())

    @replacement.get("/view")
    async def new_view():
        return {"replacement": True}

    def mount(selected):
        if surface == "ext":
            mount_plugin_router(app, "policy_example", selected, panel_paths=["GET /view"])
        else:
            mount_plugin_guest_router(app, "policy_example", selected, alias="policy-example-guest")

    mount(router)
    path = ("/policy-example-guest/view" if surface == "alias"
            else f"/api/plugins/policy_example/{surface}/view")
    headers = [(b"x-openavc-plugin-token", mint_panel_token("policy_example")[0].encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "method": "GET", "path": path,
             "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "scheme": "http", "headers": headers,
             "server": ("test", 80), "client": ("127.0.0.1", 12345), "http_version": "1.1"}
    sent = []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(started.wait(), 1)
        isolated_auth_config.set("auth", "programmer_password", "changed-disposable-password")
        continue_stream.set()
        await asyncio.wait_for(continued.wait(), 1)
        assert not closed.is_set()
        if surface == "ext":
            unmount_plugin_router(app, "policy_example")
        else:
            unmount_plugin_guest_router(app, "policy_example")
        mount(replacement)
        await asyncio.wait_for(closed.wait(), 1)
        await asyncio.wait_for(task, 1)
        assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
        assert (await host.apply_current(timeout=0)).observed
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("credential,change", [
    ("programmer", "password"), ("programmer", "expiry"), ("panel", "expiry"),
])
async def test_plugin_stream_closes_when_its_credential_stops_authorizing(
    product, isolated_auth_config, monkeypatch, credential, change,
):
    app, host = product
    router = APIRouter()
    started, closed = asyncio.Event(), asyncio.Event()
    clock = [2_000_000_000]
    monkeypatch.setattr(plugin_ext.time, "time", lambda: clock[0])

    @router.get("/view")
    async def stream():
        async def chunks():
            try:
                yield b"authorized stream"
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
        return StreamingResponse(chunks())

    mount_plugin_router(app, "policy_example", router,
                        panel_paths=["GET /view"] if credential == "panel" else [])
    mint = mint_panel_token if credential == "panel" else mint_plugin_token
    token, expiry = mint("policy_example", ttl=60)
    path = "/api/plugins/policy_example/ext/view"
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "scheme": "http", "http_version": "1.1",
             "headers": [(b"x-openavc-plugin-token", token.encode())],
             "server": ("test", 80), "client": ("127.0.0.1", 12345)}
    sent = []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(started.wait(), 1)
        if change == "password":
            isolated_auth_config.set("auth", "programmer_password", "changed-disposable-password")
        else:
            clock[0] = expiry + 1
        await asyncio.wait_for(closed.wait(), 1)
        await asyncio.wait_for(task, 1)
        assert sent[0]["status"] == 200
        assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
        assert (await host.apply_current(timeout=0)).observed
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
