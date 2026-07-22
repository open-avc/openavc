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

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

import server.api.auth as auth_mod
from server.api.plugin_ext import (
    PLUGIN_TOKEN_HEADER,
    PLUGIN_TOKEN_QUERY,
    _AccessLogRedactionFilter,
    _redact_query_secrets,
    install_access_log_redaction,
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


class TestPluginTokenCredentialBinding:
    """A token is bound to the admin credentials live at mint time, so changing
    a credential invalidates every previously-minted token (no revocation on
    disk needed)."""

    def test_token_invalid_after_password_change(self, monkeypatch):
        _set_auth(monkeypatch, password="old-pw")
        token, _ = mint_plugin_token("vp")
        assert verify_plugin_token(token, "vp") is True
        # Password change rotates the signing key -> old token no longer verifies.
        _set_auth(monkeypatch, password="new-pw")
        assert verify_plugin_token(token, "vp") is False
        # A freshly minted token under the new password verifies.
        fresh, _ = mint_plugin_token("vp")
        assert verify_plugin_token(fresh, "vp") is True

    def test_token_invalid_after_api_key_change(self, monkeypatch):
        _set_auth(monkeypatch, password="pw", api_key="key-1")
        token, _ = mint_plugin_token("vp")
        assert verify_plugin_token(token, "vp") is True
        _set_auth(monkeypatch, password="pw", api_key="key-2")
        assert verify_plugin_token(token, "vp") is False

    def test_token_invalid_after_username_change(self, monkeypatch):
        _set_auth(monkeypatch, password="pw", username="alice")
        token, _ = mint_plugin_token("vp")
        assert verify_plugin_token(token, "vp") is True
        _set_auth(monkeypatch, password="pw", username="bob")
        assert verify_plugin_token(token, "vp") is False


class TestAccessLogRedaction:
    """The plugin token, when it rides the _plugin_token query param, must be
    stripped from uvicorn access-log records so it never lands on disk."""

    def test_redacts_token_first_param(self):
        out = _redact_query_secrets("/api/plugins/vp/ext/x?_plugin_token=SEKRET")
        assert "SEKRET" not in out
        assert "_plugin_token=REDACTED" in out

    def test_redacts_token_among_other_params(self):
        out = _redact_query_secrets("/x?a=1&_plugin_token=SEKRET&b=2")
        assert out == "/x?a=1&_plugin_token=REDACTED&b=2"

    def test_leaves_paths_without_token_untouched(self):
        assert _redact_query_secrets("/api/status?foo=bar") == "/api/status?foo=bar"

    def test_filter_rewrites_access_record(self):
        filt = _AccessLogRedactionFilter()
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 0,
            '%s - "%s %s HTTP/%s" %d',
            ("1.2.3.4", "GET", "/api/plugins/vp/ext/x?_plugin_token=SEKRET", "1.1", 200),
            None,
        )
        assert filt.filter(record) is True
        assert "SEKRET" not in record.getMessage()
        assert "REDACTED" in record.getMessage()
        # Non-path args are untouched.
        assert record.args[0] == "1.2.3.4"
        assert record.args[4] == 200

    def test_filter_ignores_non_access_records(self):
        filt = _AccessLogRedactionFilter()
        record = logging.LogRecord(
            "uvicorn.error", logging.INFO, __file__, 0, "plain message", None, None,
        )
        assert filt.filter(record) is True
        assert record.getMessage() == "plain message"

    def test_install_is_idempotent(self):
        access_logger = logging.getLogger("uvicorn.access")
        for f in [f for f in access_logger.filters if isinstance(f, _AccessLogRedactionFilter)]:
            access_logger.removeFilter(f)
        install_access_log_redaction()
        install_access_log_redaction()
        count = sum(
            isinstance(f, _AccessLogRedactionFilter) for f in access_logger.filters
        )
        assert count == 1


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
            # allow_internal=True: this test fakes the upstream (the host isn't
            # resolvable), so skip the SSRF resolution and exercise forwarding.
            return await api.proxy_to(
                "http://upstream/target", request, allow_internal=True
            )

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

    def test_requires_http_endpoints_capability(self):
        """proxy_to is egress; a plugin that didn't declare http_endpoints
        can't make outbound requests (auditability)."""
        from server.core.plugin_api import PluginPermissionError

        api, _ = _make_api("p", capabilities=[])

        app = FastAPI()

        @app.post("/call")
        async def call(request: Request):
            return await api.proxy_to("http://8.8.8.8/x", request)

        client = TestClient(app)
        with pytest.raises(PluginPermissionError):
            client.post("/call", content=b"")

    def test_blocks_internal_host_by_default(self, monkeypatch):
        """Default-deny SSRF guard: a loopback/internal upstream is refused
        even with the capability, unless the caller opts into allow_internal."""
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        api, _ = _make_api("p", capabilities=["http_endpoints"])

        app = FastAPI()

        @app.post("/blocked")
        async def blocked(request: Request):
            return await api.proxy_to("http://127.0.0.1:9000/x", request)

        @app.post("/allowed")
        async def allowed(request: Request):
            return await api.proxy_to(
                "http://127.0.0.1:9000/x", request, allow_internal=True
            )

        client = TestClient(app)
        with pytest.raises(ValueError):
            client.post("/blocked", content=b"")
        # Same loopback target proceeds when explicitly opted in.
        assert client.post("/allowed", content=b"").status_code == 207


class TestSaveConfig:
    @pytest.mark.asyncio
    async def test_rejects_non_serializable_config(self):
        """A non-JSON-serializable config is refused before it can poison the
        in-memory project; api.config is left unchanged."""
        from server.core.plugin_api import PluginPermissionError

        api, _ = _make_api("p", capabilities=[])
        with pytest.raises(PluginPermissionError):
            await api.save_config({"sock": object()})
        assert api.config == {}

    @pytest.mark.asyncio
    async def test_rejects_cyclic_config(self):
        from server.core.plugin_api import PluginPermissionError

        api, _ = _make_api("p", capabilities=[])
        cyclic: dict = {}
        cyclic["self"] = cyclic
        with pytest.raises(PluginPermissionError):
            await api.save_config(cyclic)
        assert api.config == {}

    @pytest.mark.asyncio
    async def test_reverts_config_on_save_failure(self):
        """If the persist step fails, api.config reverts so it never reports an
        unpersisted value as saved."""
        api, _ = _make_api("p", capabilities=[])
        api._config = {"keep": 1}
        api._save_config_fn = AsyncMock(side_effect=OSError("disk full"))
        with pytest.raises(OSError):
            await api.save_config({"keep": 2})
        assert api.config == {"keep": 1}


class TestVariableCleanup:
    @pytest.mark.asyncio
    async def test_created_vars_cleaned_declared_preserved(self):
        """A plugin's ad-hoc var.* keys are removed on cleanup; a declared user
        variable the plugin merely wrote to is left intact."""
        from server.core.plugin_api import _MISSING

        api, reg = _make_api("p", capabilities=["variable_write"])
        # A declared user variable is seeded by the engine before plugins run.
        api._state.set("var.declared", "seed", source="system")

        await api.variable_set("declared", "from_plugin")  # pre-existing -> not tracked
        await api.variable_set("created", "ad_hoc")  # new -> tracked

        assert reg.variable_keys_set == {"var.created"}

        await reg.cleanup(api._state, api._events)

        # The plugin's ad-hoc var is gone; the declared one survives.
        assert api._state.get("var.created", _MISSING) is _MISSING
        assert api._state.get("var.declared") == "from_plugin"


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
            lambda pid, router, panel_paths=None: mount_calls.append((pid, router)),
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
        loader.set_router_hooks(
            lambda pid, r, panel_paths=None: mount_calls.append(pid), lambda pid: None
        )
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


def test_panel_element_surfaces_capabilities():
    """panel_elements carry the plugin's declared capabilities so the panel
    iframe bridge can gate openavc:action requests against them."""
    loader = _make_loader()

    class CapPlugin:
        PLUGIN_INFO = {
            "id": "cp",
            "name": "CP",
            "version": "1.0.0",
            "author": "T",
            "description": "d",
            "category": "utility",
            "license": "MIT",
            "capabilities": ["device_command", "state_write"],
        }
        EXTENSIONS = {
            "panel_elements": [
                {"type": "widget", "label": "W", "renderer": "iframe", "renderer_url": "panel/w.html"}
            ]
        }

    loader._instances = {"cp": CapPlugin()}
    panel_elements = loader.get_all_extensions()["panel_elements"]
    assert panel_elements[0]["capabilities"] == ["device_command", "state_write"]


def test_panel_element_capabilities_default_empty():
    """A plugin that declares no capabilities surfaces an empty list (not None),
    so the panel treats it as 'no actions permitted'."""
    loader = _make_loader()

    class NoCapPlugin:
        PLUGIN_INFO = {
            "id": "ncp",
            "name": "NCP",
            "version": "1.0.0",
            "author": "T",
            "description": "d",
            "category": "utility",
            "license": "MIT",
        }
        EXTENSIONS = {
            "panel_elements": [
                {"type": "widget", "label": "W", "renderer": "iframe", "renderer_url": "panel/w.html"}
            ]
        }

    loader._instances = {"ncp": NoCapPlugin()}
    panel_elements = loader.get_all_extensions()["panel_elements"]
    assert panel_elements[0]["capabilities"] == []


# ═══════════════════════════════════════════════════════════
#  8. Panel tokens + panel-reachable ext paths (claimed-instance panels)
# ═══════════════════════════════════════════════════════════


from server.api.plugin_ext import (  # noqa: E402
    has_panel_paths,
    mint_guest_token,
    mint_panel_token,
    panel_path_allowed,
    parse_panel_paths,
    verify_panel_token,
)


def _media_router():
    """Router shaped like a real media plugin: CRUD + media on shared paths."""
    router = APIRouter()

    @router.get("/streams")
    async def list_streams():
        return {"streams": []}

    @router.post("/streams")
    async def add_stream():
        return {"added": True}

    @router.get("/status")
    async def status():
        return {"ok": True}

    @router.post("/whep/{stream_id}")
    async def whep_offer(stream_id: str):
        return {"offer": stream_id}

    @router.delete("/whep/{stream_id}/{secret}")
    async def whep_teardown(stream_id: str, secret: str):
        return {"gone": True}

    return router


PANEL_PATTERNS = ["GET /streams", "/whep/*"]


def _client_with_panel_paths(plugin_id="vp", panel_paths=PANEL_PATTERNS):
    app = FastAPI()
    mount_plugin_router(app, plugin_id, _media_router(), panel_paths)
    return TestClient(app), app


class TestPanelToken:
    def test_valid_token_verifies(self):
        token, expires_at = mint_panel_token("vp")
        assert verify_panel_token(token, "vp")
        assert expires_at > 0

    def test_token_is_plugin_scoped(self):
        token, _ = mint_panel_token("vp")
        assert not verify_panel_token(token, "other")

    def test_expired_token_rejected(self):
        token, _ = mint_panel_token("vp", ttl=-10)
        assert not verify_panel_token(token, "vp")

    def test_tampered_signature_rejected(self):
        token, _ = mint_panel_token("vp")
        msg_b64, sig_b64 = token.split(".", 1)
        bad = msg_b64 + "." + ("A" + sig_b64[1:] if sig_b64[0] != "A" else "B" + sig_b64[1:])
        assert not verify_panel_token(bad, "vp")

    def test_garbage_token_rejected(self):
        assert not verify_panel_token("not-a-token", "vp")
        assert not verify_panel_token("", "vp")

    def test_domain_separation_from_plugin_tokens(self):
        """A panel token must never verify as a full plugin token, and a full
        plugin token must never verify as a panel token — the families carry
        different privileges."""
        panel_token, _ = mint_panel_token("vp")
        plugin_token, _ = mint_plugin_token("vp")
        assert not verify_plugin_token(panel_token, "vp")
        assert not verify_panel_token(plugin_token, "vp")

    def test_domain_separation_from_guest_tokens(self):
        guest_token, _ = mint_guest_token("vp", "scope1")
        assert not verify_panel_token(guest_token, "vp")

    def test_not_credential_bound(self, monkeypatch):
        """Unlike plugin tokens, a panel token survives a password change:
        it authenticates the panel surface, not a credential — a programmer
        password change must not kill wall-panel media."""
        _set_auth(monkeypatch, password="old")
        token, _ = mint_panel_token("vp")
        _set_auth(monkeypatch, password="new")
        assert verify_panel_token(token, "vp")


class TestParsePanelPaths:
    def test_bare_path(self):
        assert parse_panel_paths(["/whep/*"]) == ((None, "/whep/*"),)

    def test_method_scoped(self):
        assert parse_panel_paths(["GET /streams"]) == (("GET", "/streams"),)

    def test_lowercase_method_normalized(self):
        assert parse_panel_paths(["get /streams"]) == (("GET", "/streams"),)

    def test_missing_leading_slash_normalized(self):
        assert parse_panel_paths(["status"]) == ((None, "/status"),)

    def test_non_list_raises(self):
        with pytest.raises(ValueError):
            parse_panel_paths("/whep/*")

    def test_empty_entry_raises(self):
        with pytest.raises(ValueError):
            parse_panel_paths(["  "])

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError):
            parse_panel_paths(["FETCH /streams"])


class TestPanelPathAllowed:
    def test_matching_and_scoping(self):
        app = FastAPI()
        mount_plugin_router(app, "vp", _media_router(), PANEL_PATTERNS)
        try:
            assert panel_path_allowed("vp", "GET", "/streams")
            assert not panel_path_allowed("vp", "POST", "/streams")
            # Glob crosses path depth.
            assert panel_path_allowed("vp", "POST", "/whep/s1")
            assert panel_path_allowed("vp", "DELETE", "/whep/s1/secret9")
            assert not panel_path_allowed("vp", "GET", "/status")
            assert not panel_path_allowed("other_plugin", "GET", "/streams")
        finally:
            unmount_plugin_router(app, "vp")

    def test_unmount_clears_reachability(self):
        app = FastAPI()
        mount_plugin_router(app, "vp", _media_router(), PANEL_PATTERNS)
        assert has_panel_paths("vp")
        unmount_plugin_router(app, "vp")
        assert not has_panel_paths("vp")

    def test_remount_without_panel_paths_clears(self):
        app = FastAPI()
        mount_plugin_router(app, "vp", _media_router(), PANEL_PATTERNS)
        mount_plugin_router(app, "vp", _media_router())
        try:
            assert not has_panel_paths("vp")
        finally:
            unmount_plugin_router(app, "vp")


class TestPanelAccessDependency:
    """Claimed instance: the panel token opens ONLY declared routes."""

    def _headers(self, plugin_id="vp"):
        token, _ = mint_panel_token(plugin_id)
        return {PLUGIN_TOKEN_HEADER: token}

    def test_panel_token_reaches_declared_route(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        client, app = _client_with_panel_paths()
        try:
            resp = client.get("/api/plugins/vp/ext/streams", headers=self._headers())
            assert resp.status_code == 200
        finally:
            unmount_plugin_router(app, "vp")

    def test_method_scope_enforced(self, monkeypatch):
        """GET /streams is declared; POST /streams (CRUD) stays locked."""
        _set_auth(monkeypatch, password="secret")
        client, app = _client_with_panel_paths()
        try:
            resp = client.post("/api/plugins/vp/ext/streams", headers=self._headers())
            assert resp.status_code == 401
        finally:
            unmount_plugin_router(app, "vp")

    def test_glob_covers_nested_paths(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        client, app = _client_with_panel_paths()
        try:
            assert client.post(
                "/api/plugins/vp/ext/whep/s1", headers=self._headers()
            ).status_code == 200
            assert client.delete(
                "/api/plugins/vp/ext/whep/s1/tok123", headers=self._headers()
            ).status_code == 200
        finally:
            unmount_plugin_router(app, "vp")

    def test_undeclared_route_stays_locked(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        client, app = _client_with_panel_paths()
        try:
            resp = client.get("/api/plugins/vp/ext/status", headers=self._headers())
            assert resp.status_code == 401
        finally:
            unmount_plugin_router(app, "vp")

    def test_wrong_plugin_panel_token_rejected(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        client, app = _client_with_panel_paths()
        try:
            resp = client.get(
                "/api/plugins/vp/ext/streams", headers=self._headers("other")
            )
            assert resp.status_code == 401
        finally:
            unmount_plugin_router(app, "vp")

    def test_expired_panel_token_rejected(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        token, _ = mint_panel_token("vp", ttl=-10)
        client, app = _client_with_panel_paths()
        try:
            resp = client.get(
                "/api/plugins/vp/ext/streams", headers={PLUGIN_TOKEN_HEADER: token}
            )
            assert resp.status_code == 401
        finally:
            unmount_plugin_router(app, "vp")

    def test_full_plugin_token_reaches_undeclared_routes(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        token, _ = mint_plugin_token("vp")
        client, app = _client_with_panel_paths()
        try:
            resp = client.post(
                "/api/plugins/vp/ext/streams", headers={PLUGIN_TOKEN_HEADER: token}
            )
            assert resp.status_code == 200
        finally:
            unmount_plugin_router(app, "vp")

    def test_panel_token_useless_without_declared_paths(self, monkeypatch):
        _set_auth(monkeypatch, password="secret")
        client, app = _client_with_panel_paths(panel_paths=None)
        try:
            resp = client.get("/api/plugins/vp/ext/streams", headers=self._headers())
            assert resp.status_code == 401
        finally:
            unmount_plugin_router(app, "vp")


class TestRegisterRouterPanelPaths:
    def test_stores_panel_paths_on_registry(self):
        api, reg = _make_api("p", capabilities=["http_endpoints"])
        api.register_router(_ping_router(), panel_paths=["GET /streams"])
        assert reg.panel_ext_paths == ["GET /streams"]

    def test_defaults_to_empty(self):
        api, reg = _make_api("p", capabilities=["http_endpoints"])
        api.register_router(_ping_router())
        assert reg.panel_ext_paths == []

    def test_invalid_panel_paths_fail_at_registration(self):
        api, _ = _make_api("p", capabilities=["http_endpoints"])
        with pytest.raises(ValueError):
            api.register_router(_ping_router(), panel_paths=["FETCH /x"])


class PanelPathsRouterPlugin:
    PLUGIN_INFO = {
        "id": "panel_paths_plugin",
        "name": "Panel Paths Plugin",
        "version": "1.0.0",
        "author": "Test",
        "description": "Registers an HTTP router with panel-reachable paths.",
        "category": "utility",
        "license": "MIT",
        "platforms": ["all"],
        "capabilities": ["http_endpoints"],
    }

    async def start(self, api):
        api.register_router(_ping_router(), panel_paths=["GET /ping"])

    async def stop(self):
        pass


class TestLoaderPanelPaths:
    @pytest.mark.asyncio
    async def test_loader_passes_panel_paths_to_mount_hook(self):
        loader = _make_loader()
        register_plugin_class(PanelPathsRouterPlugin)

        mount_calls = []
        loader.set_router_hooks(
            lambda pid, router, panel_paths=None: mount_calls.append((pid, panel_paths)),
            lambda pid: None,
        )

        assert await loader.start_plugin("panel_paths_plugin", {}) is True
        assert mount_calls == [("panel_paths_plugin", ["GET /ping"])]
        await loader.stop_plugin("panel_paths_plugin")
