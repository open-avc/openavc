"""Tests for plugin-registered HTTP routers and their auth (Phase B).

Covers:
1. Plugin-token mint/verify (signature, plugin scope, expiry, tampering).
2. require_plugin_access composite dependency (open instance, programmer auth,
   valid token, wrong-plugin token, expired token, no auth).
3. mount_plugin_router / unmount_plugin_router add/remove routes (idempotent).
4. PluginAPI.register_router capability gating + type check + registry storage.
5. PluginAPI.proxy_to forwarding (method/body/query, host & content-length stripped).
6. Loader integration: a plugin's router is mounted on start, unmounted on stop.
7. get_all_extensions passes a panel_elements ext_auth flag through untouched.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

import server.api.auth as auth_mod
from server.api.plugin_ext import (
    PLUGIN_TOKEN_HEADER,
    PLUGIN_TOKEN_QUERY,
    mint_plugin_token,
    mount_plugin_router,
    unmount_plugin_router,
    verify_plugin_token,
)
from server.core.event_bus import EventBus
from server.core.plugin_api import PluginAPI, PluginPermissionError
from server.core.plugin_loader import (
    PluginLoader,
    _PLUGIN_CLASS_REGISTRY,
    _REGISTRY_LOCK,
    register_plugin_class,
)
from server.core.plugin_registry import PluginRegistry
from server.core.state_store import StateStore


def _set_auth(monkeypatch, password="", api_key="", username=""):
    monkeypatch.setattr(auth_mod, "_get_username", lambda: username)
    monkeypatch.setattr(auth_mod, "_get_password", lambda: password)
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: api_key)


def _make_api(plugin_id, capabilities):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    macros = MagicMock()
    macros.execute = AsyncMock()
    devices = MagicMock()
    devices.send_command = AsyncMock(return_value={"status": "ok"})
    reg = PluginRegistry(plugin_id)
    api = PluginAPI(
        plugin_id=plugin_id,
        capabilities=capabilities,
        config={},
        registry=reg,
        state_store=state,
        event_bus=events,
        macro_engine=macros,
        device_manager=devices,
        platform_id="test_platform",
    )
    return api, reg


def _ping_router():
    router = APIRouter()

    @router.get("/ping")
    async def ping():
        return {"pong": True}

    return router


# ═══════════════════════════════════════════════════════════
#  1. Token mint / verify
# ═══════════════════════════════════════════════════════════


class TestPluginToken:
    def test_valid_token_verifies(self):
        token, exp = mint_plugin_token("video_panel")
        assert exp > 0
        assert verify_plugin_token(token, "video_panel") is True

    def test_token_is_plugin_scoped(self):
        token, _ = mint_plugin_token("video_panel")
        assert verify_plugin_token(token, "other_plugin") is False

    def test_expired_token_rejected(self):
        token, _ = mint_plugin_token("video_panel", ttl=-10)
        assert verify_plugin_token(token, "video_panel") is False

    def test_tampered_signature_rejected(self):
        token, _ = mint_plugin_token("video_panel")
        msg_b64, _sig = token.split(".", 1)
        forged = f"{msg_b64}.AAAAAAAA"
        assert verify_plugin_token(forged, "video_panel") is False

    def test_garbage_token_rejected(self):
        assert verify_plugin_token("not-a-token", "video_panel") is False
        assert verify_plugin_token("", "video_panel") is False
        assert verify_plugin_token("a.b.c", "video_panel") is False


# ═══════════════════════════════════════════════════════════
#  2. require_plugin_access dependency (via mounted router)
# ═══════════════════════════════════════════════════════════


def _client_with_mounted(plugin_id="vp"):
    app = FastAPI()
    mount_plugin_router(app, plugin_id, _ping_router())
    return TestClient(app), app


class TestPluginAccessDependency:
    URL = "/api/plugins/vp/ext/ping"

    def test_open_instance_allows_without_token(self, monkeypatch):
        _set_auth(monkeypatch)  # no auth configured
        client, _ = _client_with_mounted()
        assert client.get(self.URL).status_code == 200

    def test_configured_blocks_without_credentials(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        client, _ = _client_with_mounted()
        assert client.get(self.URL).status_code == 401

    def test_programmer_basic_auth_reaches_route(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        client, _ = _client_with_mounted()
        assert client.get(self.URL, auth=("admin", "secret")).status_code == 200

    def test_api_key_reaches_route(self, monkeypatch):
        _set_auth(monkeypatch, api_key="k123")
        client, _ = _client_with_mounted()
        resp = client.get(self.URL, headers={"x-api-key": "k123"})
        assert resp.status_code == 200

    def test_valid_token_header_reaches_route(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        token, _ = mint_plugin_token("vp")
        client, _ = _client_with_mounted()
        resp = client.get(self.URL, headers={PLUGIN_TOKEN_HEADER: token})
        assert resp.status_code == 200

    def test_valid_token_query_param_reaches_route(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        token, _ = mint_plugin_token("vp")
        client, _ = _client_with_mounted()
        resp = client.get(f"{self.URL}?{PLUGIN_TOKEN_QUERY}={token}")
        assert resp.status_code == 200

    def test_wrong_plugin_token_rejected(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        token, _ = mint_plugin_token("some_other_plugin")
        client, _ = _client_with_mounted()
        resp = client.get(self.URL, headers={PLUGIN_TOKEN_HEADER: token})
        assert resp.status_code == 401

    def test_expired_token_rejected(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        token, _ = mint_plugin_token("vp", ttl=-10)
        client, _ = _client_with_mounted()
        resp = client.get(self.URL, headers={PLUGIN_TOKEN_HEADER: token})
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════
#  3. mount / unmount
# ═══════════════════════════════════════════════════════════


class TestMountUnmount:
    def test_mount_adds_routes_unmount_removes(self):
        app = FastAPI()
        before = len(app.router.routes)
        mount_plugin_router(app, "vp", _ping_router())
        assert any(
            getattr(r, "path", "").startswith("/api/plugins/vp/ext")
            for r in app.router.routes
        )
        unmount_plugin_router(app, "vp")
        assert len(app.router.routes) == before
        assert not any(
            getattr(r, "path", "").startswith("/api/plugins/vp/ext")
            for r in app.router.routes
        )

    def test_mount_is_idempotent(self):
        app = FastAPI()
        mount_plugin_router(app, "vp", _ping_router())
        mount_plugin_router(app, "vp", _ping_router())  # re-mount
        ext_routes = [
            r for r in app.router.routes
            if getattr(r, "path", "").startswith("/api/plugins/vp/ext")
        ]
        assert len(ext_routes) == 1

    def test_unmount_only_targets_one_plugin(self):
        app = FastAPI()
        mount_plugin_router(app, "vp", _ping_router())
        mount_plugin_router(app, "other", _ping_router())
        unmount_plugin_router(app, "vp")
        assert not any(
            getattr(r, "path", "").startswith("/api/plugins/vp/ext")
            for r in app.router.routes
        )
        assert any(
            getattr(r, "path", "").startswith("/api/plugins/other/ext")
            for r in app.router.routes
        )


# ═══════════════════════════════════════════════════════════
#  4. register_router
# ═══════════════════════════════════════════════════════════


class TestRegisterRouter:
    def test_requires_capability(self):
        api, _ = _make_api("p", capabilities=[])
        with pytest.raises(PluginPermissionError):
            api.register_router(_ping_router())

    def test_stores_router_with_capability(self):
        api, reg = _make_api("p", capabilities=["http_endpoints"])
        router = _ping_router()
        api.register_router(router)
        assert reg.http_router is router

    def test_rejects_non_router(self):
        api, _ = _make_api("p", capabilities=["http_endpoints"])
        with pytest.raises(TypeError):
            api.register_router(object())


# ═══════════════════════════════════════════════════════════
#  5. proxy_to
# ═══════════════════════════════════════════════════════════


class _FakeUpstreamResponse:
    status_code = 207
    content = b'{"upstream": true}'
    headers = {
        "content-type": "application/json",
        "content-length": "999",  # deliberately wrong; must be stripped
        "x-upstream": "yes",
    }


class _FakeAsyncClient:
    captured: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, *, content, headers, params):
        _FakeAsyncClient.captured = {
            "method": method,
            "url": url,
            "content": content,
            "headers": headers,
            "params": params,
        }
        return _FakeUpstreamResponse()


class TestProxyTo:
    def test_forwards_request_and_strips_headers(self, monkeypatch):
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        api, _ = _make_api("p", capabilities=["http_endpoints"])

        app = FastAPI()

        @app.post("/call")
        async def call(request: Request):
            return await api.proxy_to("http://upstream/target", request)

        client = TestClient(app)
        resp = client.post("/call?a=1&b=2", content=b"hello", headers={"x-test": "v"})

        assert resp.status_code == 207
        assert resp.json() == {"upstream": True}
        cap = _FakeAsyncClient.captured
        assert cap["method"] == "POST"
        assert cap["url"] == "http://upstream/target"
        assert cap["content"] == b"hello"
        assert cap["params"] == {"a": "1", "b": "2"}
        # host and content-length must not be forwarded upstream
        fwd_lower = {k.lower() for k in cap["headers"]}
        assert "host" not in fwd_lower
        assert "content-length" not in fwd_lower
        assert cap["headers"].get("x-test") == "v"
        # upstream's wrong content-length must not leak into the response
        assert resp.headers.get("x-upstream") == "yes"
        assert resp.headers["content-length"] == str(len(b'{"upstream": true}'))


# ═══════════════════════════════════════════════════════════
#  6. Loader integration
# ═══════════════════════════════════════════════════════════


class RouterPlugin:
    PLUGIN_INFO = {
        "id": "router_plugin",
        "name": "Router Plugin",
        "version": "1.0.0",
        "author": "Test",
        "description": "Registers an HTTP router.",
        "category": "utility",
        "license": "MIT",
        "platforms": ["all"],
        "capabilities": ["http_endpoints"],
    }

    async def start(self, api):
        api.register_router(_ping_router())

    async def stop(self):
        pass


@pytest.fixture(autouse=True)
def clean_plugin_registry():
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
    yield
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
        _PLUGIN_CLASS_REGISTRY.update(saved)


def _make_loader():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    macros = MagicMock()
    macros.execute = AsyncMock()
    devices = MagicMock()
    return PluginLoader(state, events, macros, devices)


class TestLoaderRouterHooks:
    @pytest.mark.asyncio
    async def test_router_mounted_on_start_unmounted_on_stop(self):
        loader = _make_loader()
        register_plugin_class(RouterPlugin)

        mount_calls = []
        unmount_calls = []
        loader.set_router_hooks(
            lambda pid, router: mount_calls.append((pid, router)),
            lambda pid: unmount_calls.append(pid),
        )

        started = await loader.start_plugin("router_plugin", {})
        assert started is True
        assert len(mount_calls) == 1
        assert mount_calls[0][0] == "router_plugin"
        assert isinstance(mount_calls[0][1], APIRouter)

        await loader.stop_plugin("router_plugin")
        assert unmount_calls == ["router_plugin"]

    @pytest.mark.asyncio
    async def test_no_mount_hook_call_for_plugin_without_router(self):
        loader = _make_loader()

        class NoRouterPlugin:
            PLUGIN_INFO = {
                "id": "no_router",
                "name": "No Router",
                "version": "1.0.0",
                "author": "Test",
                "description": "No router.",
                "category": "utility",
                "license": "MIT",
                "platforms": ["all"],
                "capabilities": [],
            }

            async def start(self, api):
                pass

            async def stop(self):
                pass

        register_plugin_class(NoRouterPlugin)
        mount_calls = []
        loader.set_router_hooks(lambda pid, r: mount_calls.append(pid), lambda pid: None)
        await loader.start_plugin("no_router", {})
        assert mount_calls == []


# ═══════════════════════════════════════════════════════════
#  7. get_all_extensions ext_auth pass-through
# ═══════════════════════════════════════════════════════════


def test_ext_auth_flag_passes_through_extensions():
    loader = _make_loader()

    class PanelPlugin:
        PLUGIN_INFO = {
            "id": "vp",
            "name": "VP",
            "version": "1.0.0",
            "author": "T",
            "description": "d",
            "category": "utility",
            "license": "MIT",
        }
        EXTENSIONS = {
            "panel_elements": [
                {
                    "type": "video_stream",
                    "label": "Camera",
                    "renderer": "iframe",
                    "renderer_url": "panel/video_stream.html",
                    "ext_auth": True,
                    "sandbox_permissions": ["allow-same-origin"],
                }
            ]
        }

    loader._instances = {"vp": PanelPlugin()}
    exts = loader.get_all_extensions()
    panel_elements = exts["panel_elements"]
    assert len(panel_elements) == 1
    assert panel_elements[0]["ext_auth"] is True
    # Phase C sanitization still applies alongside the new flag.
    assert panel_elements[0]["sandbox_permissions"] == ["allow-same-origin"]
