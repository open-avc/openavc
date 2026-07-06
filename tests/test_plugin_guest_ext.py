"""Tests for plugin-registered guest HTTP routers (open, plugin-gated).

Covers:
1. mount_plugin_guest_router / unmount_plugin_guest_router add/remove routes
   (idempotent, per-plugin isolation, independent of the authed /ext mount).
2. Guest routes carry NO platform auth: reachable without credentials even on
   a claimed instance, while the same plugin's /ext routes still 401.
3. PluginAPI.register_guest_router capability gating (guest_endpoints) +
   type check + registry storage.
4. Loader integration: a plugin's guest router is mounted on start and
   unmounted on stop via the extended router hooks; the two-argument
   set_router_hooks form (no guest hooks) keeps working.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import server.api.auth as auth_mod
from server.api.plugin_ext import (
    mount_plugin_guest_router,
    mount_plugin_router,
    unmount_plugin_guest_router,
    unmount_plugin_router,
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
#  1. mount / unmount
# ═══════════════════════════════════════════════════════════


class TestGuestMountUnmount:
    def test_mount_adds_routes_unmount_removes(self):
        app = FastAPI()
        before = len(app.router.routes)
        mount_plugin_guest_router(app, "acme", _ping_router())
        assert any(
            getattr(r, "path", "").startswith("/api/plugins/acme/guest")
            for r in app.router.routes
        )
        unmount_plugin_guest_router(app, "acme")
        assert len(app.router.routes) == before
        assert not any(
            getattr(r, "path", "").startswith("/api/plugins/acme/guest")
            for r in app.router.routes
        )

    def test_mount_is_idempotent(self):
        app = FastAPI()
        mount_plugin_guest_router(app, "acme", _ping_router())
        mount_plugin_guest_router(app, "acme", _ping_router())  # re-mount
        guest_routes = [
            r for r in app.router.routes
            if getattr(r, "path", "").startswith("/api/plugins/acme/guest")
        ]
        assert len(guest_routes) == 1

    def test_unmount_only_targets_one_plugin(self):
        app = FastAPI()
        mount_plugin_guest_router(app, "acme", _ping_router())
        mount_plugin_guest_router(app, "other", _ping_router())
        unmount_plugin_guest_router(app, "acme")
        assert not any(
            getattr(r, "path", "").startswith("/api/plugins/acme/guest")
            for r in app.router.routes
        )
        assert any(
            getattr(r, "path", "").startswith("/api/plugins/other/guest")
            for r in app.router.routes
        )

    def test_guest_and_ext_mounts_are_independent(self):
        """One plugin can carry both routers; unmounting one leaves the other."""
        app = FastAPI()
        mount_plugin_router(app, "acme", _ping_router())
        mount_plugin_guest_router(app, "acme", _ping_router())
        unmount_plugin_router(app, "acme")
        assert not any(
            getattr(r, "path", "").startswith("/api/plugins/acme/ext")
            for r in app.router.routes
        )
        assert any(
            getattr(r, "path", "").startswith("/api/plugins/acme/guest")
            for r in app.router.routes
        )
        unmount_plugin_guest_router(app, "acme")
        assert not any(
            getattr(r, "path", "").startswith("/api/plugins/acme/guest")
            for r in app.router.routes
        )


# ═══════════════════════════════════════════════════════════
#  2. No platform auth on guest routes
# ═══════════════════════════════════════════════════════════


class TestGuestRoutesAreOpen:
    GUEST_URL = "/api/plugins/acme/guest/ping"
    EXT_URL = "/api/plugins/acme/ext/ping"

    def _client(self):
        app = FastAPI()
        mount_plugin_guest_router(app, "acme", _ping_router())
        mount_plugin_router(app, "acme", _ping_router())
        return TestClient(app)

    def test_open_instance_allows(self, monkeypatch):
        _set_auth(monkeypatch)  # no auth configured
        assert self._client().get(self.GUEST_URL).status_code == 200

    def test_claimed_instance_allows_without_credentials(self, monkeypatch):
        """The point of guest routes: a claimed instance still serves them to
        an unauthenticated caller, while the authed /ext mount keeps its 401."""
        _set_auth(monkeypatch, password="secret")
        client = self._client()
        assert client.get(self.GUEST_URL).status_code == 200
        assert client.get(self.EXT_URL).status_code == 401


# ═══════════════════════════════════════════════════════════
#  3. register_guest_router
# ═══════════════════════════════════════════════════════════


class TestRegisterGuestRouter:
    def test_requires_guest_capability(self):
        api, _ = _make_api("acme", capabilities=[])
        with pytest.raises(PluginPermissionError):
            api.register_guest_router(_ping_router())

    def test_http_endpoints_alone_is_not_enough(self):
        """Unauthenticated routes are a bigger grant than authed /ext routes;
        the ordinary http_endpoints capability must not unlock them."""
        api, _ = _make_api("acme", capabilities=["http_endpoints"])
        with pytest.raises(PluginPermissionError):
            api.register_guest_router(_ping_router())

    def test_stores_router_with_capability(self):
        api, reg = _make_api("acme", capabilities=["guest_endpoints"])
        router = _ping_router()
        api.register_guest_router(router)
        assert reg.guest_router is router
        assert reg.http_router is None

    def test_rejects_non_router(self):
        api, _ = _make_api("acme", capabilities=["guest_endpoints"])
        with pytest.raises(TypeError):
            api.register_guest_router(object())


# ═══════════════════════════════════════════════════════════
#  4. Loader integration
# ═══════════════════════════════════════════════════════════


class GuestRouterPlugin:
    PLUGIN_INFO = {
        "id": "acme_guest",
        "name": "Acme Guest",
        "version": "1.0.0",
        "author": "Test",
        "description": "Registers a guest HTTP router.",
        "category": "utility",
        "license": "MIT",
        "platforms": ["all"],
        "capabilities": ["guest_endpoints"],
    }

    async def start(self, api):
        api.register_guest_router(_ping_router())

    async def stop(self):
        pass


class BothRoutersPlugin:
    PLUGIN_INFO = {
        "id": "acme_both",
        "name": "Acme Both",
        "version": "1.0.0",
        "author": "Test",
        "description": "Registers authed and guest routers.",
        "category": "utility",
        "license": "MIT",
        "platforms": ["all"],
        "capabilities": ["http_endpoints", "guest_endpoints"],
    }

    async def start(self, api):
        api.register_router(_ping_router())
        api.register_guest_router(_ping_router())

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


class TestLoaderGuestRouterHooks:
    @pytest.mark.asyncio
    async def test_guest_router_mounted_on_start_unmounted_on_stop(self):
        loader = _make_loader()
        register_plugin_class(GuestRouterPlugin)

        mount_calls = []
        unmount_calls = []
        guest_mount_calls = []
        guest_unmount_calls = []
        loader.set_router_hooks(
            lambda pid, router: mount_calls.append(pid),
            lambda pid: unmount_calls.append(pid),
            lambda pid, router: guest_mount_calls.append((pid, router)),
            lambda pid: guest_unmount_calls.append(pid),
        )

        started = await loader.start_plugin("acme_guest", {})
        assert started is True
        assert mount_calls == []  # no authed router registered
        assert len(guest_mount_calls) == 1
        assert guest_mount_calls[0][0] == "acme_guest"
        assert isinstance(guest_mount_calls[0][1], APIRouter)

        await loader.stop_plugin("acme_guest")
        assert guest_unmount_calls == ["acme_guest"]

    @pytest.mark.asyncio
    async def test_both_routers_mounted(self):
        loader = _make_loader()
        register_plugin_class(BothRoutersPlugin)

        mount_calls = []
        guest_mount_calls = []
        loader.set_router_hooks(
            lambda pid, router: mount_calls.append(pid),
            lambda pid: None,
            lambda pid, router: guest_mount_calls.append(pid),
            lambda pid: None,
        )

        assert await loader.start_plugin("acme_both", {}) is True
        assert mount_calls == ["acme_both"]
        assert guest_mount_calls == ["acme_both"]

    @pytest.mark.asyncio
    async def test_two_arg_hook_form_still_works(self):
        """Callers that only wire the authed-router hooks (the pre-guest form)
        must keep working; a registered guest router is then simply not
        mounted rather than crashing start."""
        loader = _make_loader()
        register_plugin_class(GuestRouterPlugin)

        loader.set_router_hooks(lambda pid, router: None, lambda pid: None)

        assert await loader.start_plugin("acme_guest", {}) is True
        await loader.stop_plugin("acme_guest")
