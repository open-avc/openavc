"""
Unit tests for the OpenAVC plugin system lifecycle.

Covers:
1. Manifest validation (required fields, license, category)
2. PluginAPI namespace isolation (state prefixing, event prefixing, capability enforcement)
3. PluginRegistry cleanup (tracking and removal of all registrations)
4. Error isolation (start failure status, callback failure tracking)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.core.event_bus import EventBus
from server.core.plugin_api import PluginAPI, PluginPermissionError
from server.core.plugin_loader import (
    MAX_CALLBACK_FAILURES,
    PluginLoader,
    _PLUGIN_CLASS_REGISTRY,
    _REGISTRY_LOCK,
    register_plugin_class,
)
from server.core.plugin_registry import PluginRegistry
from server.core.state_store import StateStore


# ──── Mock Plugin Classes ────


class ValidPlugin:
    """A well-formed plugin with all required fields and common capabilities."""

    PLUGIN_INFO = {
        "id": "valid_plugin",
        "name": "Valid Plugin",
        "version": "1.0.0",
        "author": "Test Author",
        "description": "A valid test plugin.",
        "category": "utility",
        "license": "MIT",
        "platforms": ["all"],
        "capabilities": ["state_read", "state_write", "event_emit", "event_subscribe"],
    }

    def __init__(self):
        self.api = None
        self.started = False
        self.stopped = False

    async def start(self, api):
        self.api = api
        self.started = True
        await api.state_set("status", "active")

    async def stop(self):
        self.stopped = True


class FailingStartPlugin:
    """Plugin that raises during start()."""

    PLUGIN_INFO = {
        "id": "failing_start",
        "name": "Failing Start Plugin",
        "version": "0.1.0",
        "author": "Test",
        "description": "Explodes on start.",
        "category": "utility",
        "license": "MIT",
        "capabilities": [],
    }

    async def start(self, api):
        raise ValueError("Kaboom on start")

    async def stop(self):
        pass


class MinimalPlugin:
    """Plugin with no capabilities at all."""

    PLUGIN_INFO = {
        "id": "minimal",
        "name": "Minimal Plugin",
        "version": "0.1.0",
        "author": "Test",
        "description": "Does nothing.",
        "category": "utility",
        "license": "MIT",
        "capabilities": [],
    }

    async def start(self, api):
        self.api = api

    async def stop(self):
        pass


# ──── Fixtures ────


@pytest.fixture
def state():
    return StateStore()


@pytest.fixture
def events():
    return EventBus()


@pytest.fixture
def wired(state, events):
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def mock_macros():
    m = MagicMock()
    m.execute = AsyncMock()
    return m


@pytest.fixture
def mock_devices():
    d = MagicMock()
    d.send_command = AsyncMock(return_value={"status": "ok"})
    return d


@pytest.fixture
def registry():
    return PluginRegistry("lifecycle_test")


@pytest.fixture
def loader(wired, mock_macros, mock_devices):
    state, events = wired
    return PluginLoader(state, events, mock_macros, mock_devices)


@pytest.fixture(autouse=True)
def clean_plugin_registry():
    """Ensure the global plugin class registry is clean for each test."""
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
    yield
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
        _PLUGIN_CLASS_REGISTRY.update(saved)


def _make_api(
    plugin_id,
    capabilities,
    state_store,
    event_bus,
    mock_macros,
    mock_devices,
    config=None,
    failure_reporter=None,
    success_reporter=None,
):
    """Helper to build a PluginAPI with a fresh PluginRegistry."""
    reg = PluginRegistry(plugin_id)
    return PluginAPI(
        plugin_id=plugin_id,
        capabilities=capabilities,
        config=config or {},
        registry=reg,
        state_store=state_store,
        event_bus=event_bus,
        macro_engine=mock_macros,
        device_manager=mock_devices,
        platform_id="test_platform",
        failure_reporter=failure_reporter,
        success_reporter=success_reporter,
    ), reg


# ═══════════════════════════════════════════════════════════
#  1. Manifest Validation
# ═══════════════════════════════════════════════════════════


class TestManifestValidation:
    """Tests for PluginLoader.validate_manifest."""

    def test_valid_manifest_passes(self, loader):
        valid, error = loader.validate_manifest(ValidPlugin)
        assert valid is True
        assert error == ""

    def test_missing_id_field(self, loader):
        class NoId:
            PLUGIN_INFO = {
                "name": "No ID",
                "version": "1.0.0",
                "author": "X",
                "description": "No id.",
                "category": "utility",
                "license": "MIT",
            }

        valid, error = loader.validate_manifest(NoId)
        assert valid is False
        assert "Missing required fields" in error
        assert "id" in error

    def test_missing_name_field(self, loader):
        class NoName:
            PLUGIN_INFO = {
                "id": "no_name",
                "version": "1.0.0",
                "author": "X",
                "description": "No name.",
                "category": "utility",
                "license": "MIT",
            }

        valid, error = loader.validate_manifest(NoName)
        assert valid is False
        assert "name" in error

    def test_missing_version_field(self, loader):
        class NoVersion:
            PLUGIN_INFO = {
                "id": "no_ver",
                "name": "No Version",
                "author": "X",
                "description": "No version.",
                "category": "utility",
                "license": "MIT",
            }

        valid, error = loader.validate_manifest(NoVersion)
        assert valid is False
        assert "version" in error

    def test_missing_author_field(self, loader):
        class NoAuthor:
            PLUGIN_INFO = {
                "id": "no_auth",
                "name": "No Author",
                "version": "1.0.0",
                "description": "No author.",
                "category": "utility",
                "license": "MIT",
            }

        valid, error = loader.validate_manifest(NoAuthor)
        assert valid is False
        assert "author" in error

    def test_missing_description_field(self, loader):
        class NoDesc:
            PLUGIN_INFO = {
                "id": "no_desc",
                "name": "No Desc",
                "version": "1.0.0",
                "author": "X",
                "category": "utility",
                "license": "MIT",
            }

        valid, error = loader.validate_manifest(NoDesc)
        assert valid is False
        assert "description" in error

    def test_missing_category_field(self, loader):
        class NoCat:
            PLUGIN_INFO = {
                "id": "no_cat",
                "name": "No Category",
                "version": "1.0.0",
                "author": "X",
                "description": "No category.",
                "license": "MIT",
            }

        valid, error = loader.validate_manifest(NoCat)
        assert valid is False
        assert "category" in error

    def test_missing_license_field(self, loader):
        class NoLic:
            PLUGIN_INFO = {
                "id": "no_lic",
                "name": "No License",
                "version": "1.0.0",
                "author": "X",
                "description": "No license.",
                "category": "utility",
            }

        valid, error = loader.validate_manifest(NoLic)
        assert valid is False
        assert "license" in error

    def test_missing_multiple_fields(self, loader):
        class Sparse:
            PLUGIN_INFO = {"id": "sparse"}

        valid, error = loader.validate_manifest(Sparse)
        assert valid is False
        assert "Missing required fields" in error

    def test_no_plugin_info_at_all(self, loader):
        class Empty:
            pass

        valid, error = loader.validate_manifest(Empty)
        assert valid is False
        assert "Missing or invalid PLUGIN_INFO" in error

    def test_plugin_info_not_dict(self, loader):
        class BadType:
            PLUGIN_INFO = "not a dict"

        valid, error = loader.validate_manifest(BadType)
        assert valid is False
        assert "Missing or invalid PLUGIN_INFO" in error

    # ── License checks ──

    def test_gpl_license_rejected(self, loader):
        class GplPlugin:
            PLUGIN_INFO = {
                "id": "gpl",
                "name": "GPL Plugin",
                "version": "1.0.0",
                "author": "X",
                "description": "GPL.",
                "category": "utility",
                "license": "GPL-3.0",
            }

        valid, error = loader.validate_manifest(GplPlugin)
        assert valid is False
        assert "not MIT-compatible" in error

    def test_lgpl_license_rejected(self, loader):
        class LgplPlugin:
            PLUGIN_INFO = {
                "id": "lgpl",
                "name": "LGPL",
                "version": "1.0.0",
                "author": "X",
                "description": "LGPL.",
                "category": "utility",
                "license": "LGPL-2.1",
            }

        valid, error = loader.validate_manifest(LgplPlugin)
        assert valid is False
        assert "not MIT-compatible" in error

    def test_agpl_license_rejected(self, loader):
        class AgplPlugin:
            PLUGIN_INFO = {
                "id": "agpl",
                "name": "AGPL",
                "version": "1.0.0",
                "author": "X",
                "description": "AGPL.",
                "category": "utility",
                "license": "AGPL-3.0",
            }

        valid, error = loader.validate_manifest(AgplPlugin)
        assert valid is False
        assert "not MIT-compatible" in error

    def test_mit_license_accepted(self, loader):
        valid, _ = loader.validate_manifest(ValidPlugin)
        assert valid is True

    def test_apache_license_accepted(self, loader):
        class Apache:
            PLUGIN_INFO = {
                "id": "apache",
                "name": "Apache",
                "version": "1.0.0",
                "author": "X",
                "description": "Apache 2.",
                "category": "utility",
                "license": "Apache-2.0",
            }

        valid, error = loader.validate_manifest(Apache)
        assert valid is True
        assert error == ""

    def test_bsd3_license_accepted(self, loader):
        class Bsd3:
            PLUGIN_INFO = {
                "id": "bsd3",
                "name": "BSD3",
                "version": "1.0.0",
                "author": "X",
                "description": "BSD-3.",
                "category": "utility",
                "license": "BSD-3-Clause",
            }

        valid, _ = loader.validate_manifest(Bsd3)
        assert valid is True

    def test_isc_license_accepted(self, loader):
        class Isc:
            PLUGIN_INFO = {
                "id": "isc",
                "name": "ISC",
                "version": "1.0.0",
                "author": "X",
                "description": "ISC.",
                "category": "utility",
                "license": "ISC",
            }

        valid, _ = loader.validate_manifest(Isc)
        assert valid is True

    def test_license_check_is_case_insensitive(self, loader):
        class MixedCase:
            PLUGIN_INFO = {
                "id": "mixcase",
                "name": "Mixed",
                "version": "1.0.0",
                "author": "X",
                "description": "Mixed case MIT.",
                "category": "utility",
                "license": "mIt",
            }

        valid, _ = loader.validate_manifest(MixedCase)
        assert valid is True

    # ── Category checks ──

    def test_invalid_category_rejected(self, loader):
        class BadCat:
            PLUGIN_INFO = {
                "id": "badcat",
                "name": "Bad Cat",
                "version": "1.0.0",
                "author": "X",
                "description": "Invalid category.",
                "category": "teleporter",
                "license": "MIT",
            }

        valid, error = loader.validate_manifest(BadCat)
        assert valid is False
        assert "Invalid category" in error
        assert "teleporter" in error

    def test_control_surface_category_valid(self, loader):
        class Surface:
            PLUGIN_INFO = {
                "id": "surface",
                "name": "Surface",
                "version": "1.0.0",
                "author": "X",
                "description": "Control surface.",
                "category": "control_surface",
                "license": "MIT",
            }

        valid, _ = loader.validate_manifest(Surface)
        assert valid is True

    def test_integration_category_valid(self, loader):
        class Integration:
            PLUGIN_INFO = {
                "id": "integ",
                "name": "Integration",
                "version": "1.0.0",
                "author": "X",
                "description": "Integration.",
                "category": "integration",
                "license": "MIT",
            }

        valid, _ = loader.validate_manifest(Integration)
        assert valid is True

    def test_sensor_category_valid(self, loader):
        class Sensor:
            PLUGIN_INFO = {
                "id": "sensor",
                "name": "Sensor",
                "version": "1.0.0",
                "author": "X",
                "description": "Sensor.",
                "category": "sensor",
                "license": "MIT",
            }

        valid, _ = loader.validate_manifest(Sensor)
        assert valid is True

    def test_utility_category_valid(self, loader):
        valid, _ = loader.validate_manifest(ValidPlugin)
        assert valid is True

    # ── Invalid capabilities ──

    def test_unknown_capability_rejected(self, loader):
        class BadCap:
            PLUGIN_INFO = {
                "id": "badcap",
                "name": "BadCap",
                "version": "1.0.0",
                "author": "X",
                "description": "Unknown cap.",
                "category": "utility",
                "license": "MIT",
                "capabilities": ["fly", "teleport"],
            }

        valid, error = loader.validate_manifest(BadCap)
        assert valid is False
        assert "Unknown capabilities" in error

    def test_invalid_config_schema_type_rejected(self, loader):
        class BadSchema:
            PLUGIN_INFO = {
                "id": "badschema",
                "name": "BadSchema",
                "version": "1.0.0",
                "author": "X",
                "description": "Bad schema.",
                "category": "utility",
                "license": "MIT",
            }
            CONFIG_SCHEMA = "not a dict"

        valid, error = loader.validate_manifest(BadSchema)
        assert valid is False
        assert "CONFIG_SCHEMA must be a dict" in error


# ═══════════════════════════════════════════════════════════
#  2. PluginAPI Namespace Isolation
# ═══════════════════════════════════════════════════════════


class TestPluginAPINamespaceIsolation:
    """Tests for state key auto-prefixing, event name auto-prefixing,
    and capability enforcement."""

    # ── State key auto-prefixing ──

    @pytest.mark.asyncio
    async def test_state_set_bare_key_gets_prefixed(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api(
            "myplug",
            ["state_write"],
            state, events, mock_macros, mock_devices,
        )
        await api.state_set("volume", 75)
        assert state.get("plugin.myplug.volume") == 75

    @pytest.mark.asyncio
    async def test_state_set_already_prefixed_key_not_double_prefixed(
        self, wired, mock_macros, mock_devices
    ):
        state, events = wired
        api, _reg = _make_api(
            "myplug",
            ["state_write"],
            state, events, mock_macros, mock_devices,
        )
        await api.state_set("plugin.myplug.volume", 50)
        # Should NOT be plugin.myplug.plugin.myplug.volume
        assert state.get("plugin.myplug.volume") == 50
        assert state.get("plugin.myplug.plugin.myplug.volume") is None

    @pytest.mark.asyncio
    async def test_state_set_tracks_key_in_registry(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, reg = _make_api(
            "tracker",
            ["state_write"],
            state, events, mock_macros, mock_devices,
        )
        await api.state_set("count", 10)
        assert "plugin.tracker.count" in reg.state_keys_set

    @pytest.mark.asyncio
    async def test_state_set_rejects_nested_objects(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api(
            "nested",
            ["state_write"],
            state, events, mock_macros, mock_devices,
        )
        with pytest.raises(PluginPermissionError, match="flat primitives"):
            await api.state_set("data", {"key": "value"})

    @pytest.mark.asyncio
    async def test_state_set_rejects_list_values(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api(
            "listval",
            ["state_write"],
            state, events, mock_macros, mock_devices,
        )
        with pytest.raises(PluginPermissionError, match="flat primitives"):
            await api.state_set("items", [1, 2, 3])

    @pytest.mark.asyncio
    async def test_state_set_accepts_none(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api(
            "noneval",
            ["state_write"],
            state, events, mock_macros, mock_devices,
        )
        # None is a valid flat primitive
        await api.state_set("cleared", None)
        assert state.get("plugin.noneval.cleared") is None

    @pytest.mark.asyncio
    async def test_state_set_accepts_bool(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api(
            "boolval",
            ["state_write"],
            state, events, mock_macros, mock_devices,
        )
        await api.state_set("active", True)
        assert state.get("plugin.boolval.active") is True

    @pytest.mark.asyncio
    async def test_variable_set_writes_to_var_namespace(self, wired, mock_macros, mock_devices):
        """variable_set writes to var.<id> when variable_write is declared."""
        state, events = wired
        api, _reg = _make_api(
            "varwriter",
            ["variable_write"],
            state, events, mock_macros, mock_devices,
        )
        await api.variable_set("room_mode", "presentation")
        assert state.get("var.room_mode") == "presentation"

    @pytest.mark.asyncio
    async def test_variable_set_rejects_non_primitives(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api(
            "varwriter",
            ["variable_write"],
            state, events, mock_macros, mock_devices,
        )
        with pytest.raises(PluginPermissionError, match="flat primitives"):
            await api.variable_set("complex", {"a": 1})

    @pytest.mark.asyncio
    async def test_variable_set_requires_variable_write(self, wired, mock_macros, mock_devices):
        """variable_set requires variable_write — state_write alone is insufficient."""
        state, events = wired
        api, _reg = _make_api(
            "stateonly",
            ["state_write"],
            state, events, mock_macros, mock_devices,
        )
        with pytest.raises(PluginPermissionError, match="variable_write"):
            await api.variable_set("room_mode", "presentation")
        assert state.get("var.room_mode") is None

    @pytest.mark.asyncio
    async def test_variable_write_does_not_grant_state_set(self, wired, mock_macros, mock_devices):
        """variable_write and state_write are independent — variable_write alone
        does not let a plugin set keys in its own plugin.<id>.* namespace."""
        state, events = wired
        api, _reg = _make_api(
            "varonly",
            ["variable_write"],
            state, events, mock_macros, mock_devices,
        )
        with pytest.raises(PluginPermissionError, match="state_write"):
            await api.state_set("status", "running")

    @pytest.mark.asyncio
    async def test_plugin_with_both_capabilities_can_use_both(
        self, wired, mock_macros, mock_devices
    ):
        """A plugin granted both capabilities can use both APIs."""
        state, events = wired
        api, _reg = _make_api(
            "both",
            ["state_write", "variable_write"],
            state, events, mock_macros, mock_devices,
        )
        await api.state_set("status", "running")
        await api.variable_set("room_mode", "active")
        assert state.get("plugin.both.status") == "running"
        assert state.get("var.room_mode") == "active"

    # ── Event name auto-prefixing ──

    @pytest.mark.asyncio
    async def test_event_emit_auto_prefixed(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api(
            "evtplug",
            ["event_emit"],
            state, events, mock_macros, mock_devices,
        )
        received = []
        events.on("plugin.evtplug.button.pressed", lambda e, p: received.append(e))

        await api.event_emit("button.pressed", {"button_id": "btn1"})
        assert len(received) == 1
        assert received[0] == "plugin.evtplug.button.pressed"

    @pytest.mark.asyncio
    async def test_event_emit_prefix_includes_plugin_id(self, wired, mock_macros, mock_devices):
        """Two plugins emitting the same event name produce different full names."""
        state, events = wired
        api_a, _ = _make_api("plug_a", ["event_emit"], state, events, mock_macros, mock_devices)
        api_b, _ = _make_api("plug_b", ["event_emit"], state, events, mock_macros, mock_devices)

        received = []
        events.on("plugin.*", lambda e, p: received.append(e))

        await api_a.event_emit("ping")
        await api_b.event_emit("ping")

        assert "plugin.plug_a.ping" in received
        assert "plugin.plug_b.ping" in received

    @pytest.mark.asyncio
    async def test_event_subscribe_can_listen_to_any_event(
        self, wired, mock_macros, mock_devices
    ):
        """Plugins can subscribe to events outside their namespace."""
        state, events = wired
        api, _reg = _make_api(
            "listener",
            ["event_subscribe"],
            state, events, mock_macros, mock_devices,
        )
        received = []

        async def handler(event_name, payload):
            received.append(event_name)

        await api.event_subscribe("device.connected.*", handler)
        await events.emit("device.connected.proj1")
        assert len(received) == 1
        assert received[0] == "device.connected.proj1"

    @pytest.mark.asyncio
    async def test_event_subscribe_tracks_in_registry(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, reg = _make_api(
            "trackevt",
            ["event_subscribe"],
            state, events, mock_macros, mock_devices,
        )
        sub_id = await api.event_subscribe("test.*", lambda e, p: None)
        assert sub_id in reg.event_subscriptions

    # ── Capability enforcement ──

    @pytest.mark.asyncio
    async def test_state_read_without_capability_raises(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api("noperm", [], state, events, mock_macros, mock_devices)
        with pytest.raises(PluginPermissionError, match="state_read"):
            await api.state_get("var.x")

    @pytest.mark.asyncio
    async def test_state_write_without_capability_raises(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api(
            "noperm", ["state_read"], state, events, mock_macros, mock_devices
        )
        with pytest.raises(PluginPermissionError, match="state_write"):
            await api.state_set("key", "val")

    @pytest.mark.asyncio
    async def test_event_emit_without_capability_raises(self, wired, mock_macros, mock_devices):
        state, events = wired
        api, _reg = _make_api("noperm", [], state, events, mock_macros, mock_devices)
        with pytest.raises(PluginPermissionError, match="event_emit"):
            await api.event_emit("test.event")

    @pytest.mark.asyncio
    async def test_event_subscribe_without_capability_raises(
        self, wired, mock_macros, mock_devices
    ):
        state, events = wired
        api, _reg = _make_api("noperm", [], state, events, mock_macros, mock_devices)
        with pytest.raises(PluginPermissionError, match="event_subscribe"):
            await api.event_subscribe("test.*", lambda e, p: None)

    @pytest.mark.asyncio
    async def test_macro_execute_without_capability_raises(
        self, wired, mock_macros, mock_devices
    ):
        state, events = wired
        api, _reg = _make_api("noperm", [], state, events, mock_macros, mock_devices)
        with pytest.raises(PluginPermissionError, match="macro_execute"):
            await api.macro_execute("some_macro")

    @pytest.mark.asyncio
    async def test_device_command_without_capability_raises(
        self, wired, mock_macros, mock_devices
    ):
        state, events = wired
        api, _reg = _make_api("noperm", [], state, events, mock_macros, mock_devices)
        with pytest.raises(PluginPermissionError, match="device_command"):
            await api.device_command("dev1", "power_on")

    @pytest.mark.asyncio
    async def test_permission_error_lists_declared_capabilities(
        self, wired, mock_macros, mock_devices
    ):
        state, events = wired
        api, _reg = _make_api(
            "partial", ["state_read"], state, events, mock_macros, mock_devices
        )
        with pytest.raises(PluginPermissionError, match="state_read") as exc_info:
            await api.state_set("x", 1)
        # The error message should mention what was declared
        assert "state_read" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_variable_set_requires_variable_write_capability(
        self, wired, mock_macros, mock_devices
    ):
        state, events = wired
        api, _reg = _make_api(
            "noperm", ["state_read"], state, events, mock_macros, mock_devices
        )
        with pytest.raises(PluginPermissionError, match="variable_write"):
            await api.variable_set("room_mode", "off")


# ═══════════════════════════════════════════════════════════
#  3. PluginRegistry Cleanup
# ═══════════════════════════════════════════════════════════


class TestPluginRegistryCleanup:
    """Tests for PluginRegistry tracking and cleanup."""

    def test_track_state_subscription(self, registry):
        registry.track_state_subscription("sub_abc")
        assert "sub_abc" in registry.state_subscriptions

    def test_track_multiple_state_subscriptions(self, registry):
        registry.track_state_subscription("sub_1")
        registry.track_state_subscription("sub_2")
        assert len(registry.state_subscriptions) == 2

    def test_track_event_subscription(self, registry):
        registry.track_event_subscription("evt_xyz")
        assert "evt_xyz" in registry.event_subscriptions

    def test_track_state_key(self, registry):
        registry.track_state_key("plugin.lifecycle_test.foo")
        assert "plugin.lifecycle_test.foo" in registry.state_keys_set

    def test_track_state_key_deduplicates(self, registry):
        registry.track_state_key("plugin.lifecycle_test.foo")
        registry.track_state_key("plugin.lifecycle_test.foo")
        assert len(registry.state_keys_set) == 1

    def test_track_task(self, registry):
        task = MagicMock(spec=asyncio.Task)
        registry.track_task(task)
        assert task in registry.managed_tasks

    def test_untrack_task(self, registry):
        task = MagicMock(spec=asyncio.Task)
        registry.track_task(task)
        registry.untrack_task(task)
        assert task not in registry.managed_tasks

    def test_untrack_nonexistent_task_no_error(self, registry):
        task = MagicMock(spec=asyncio.Task)
        # Should not raise
        registry.untrack_task(task)

    def test_track_periodic_task(self, registry):
        registry.track_periodic_task("periodic_abc")
        assert "periodic_abc" in registry.periodic_task_ids

    @pytest.mark.asyncio
    async def test_cleanup_removes_state_subscriptions(self, wired, registry):
        state, events = wired
        sub_id = state.subscribe("var.*", lambda k, o, n, s: None)
        registry.track_state_subscription(sub_id)

        await registry.cleanup(state, events)
        assert len(registry.state_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_cleanup_removes_event_subscriptions(self, wired, registry):
        state, events = wired
        handler_id = events.on("device.*", lambda e, p: None)
        registry.track_event_subscription(handler_id)

        await registry.cleanup(state, events)
        assert len(registry.event_subscriptions) == 0
        # Handler should no longer fire
        received = []
        events.on("device.connected.x", lambda e, p: received.append(e))
        await events.emit("device.connected.x")
        # Only our new handler fires, not the removed one
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_cleanup_deletes_state_keys(self, wired, registry):
        state, events = wired
        state.set("plugin.lifecycle_test.counter", 42, source="test")
        registry.track_state_key("plugin.lifecycle_test.counter")

        await registry.cleanup(state, events)
        assert state.get("plugin.lifecycle_test.counter") is None
        assert len(registry.state_keys_set) == 0

    @pytest.mark.asyncio
    async def test_cleanup_cancels_tasks(self, wired, registry):
        state, events = wired

        task = asyncio.create_task(asyncio.sleep(300))
        registry.track_task(task)

        await registry.cleanup(state, events)
        assert len(registry.managed_tasks) == 0
        # Task should be cancelled after cleanup
        assert task.done()
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_cleanup_clears_periodic_task_ids(self, wired, registry):
        state, events = wired
        registry.track_periodic_task("periodic_1")
        registry.track_periodic_task("periodic_2")

        await registry.cleanup(state, events)
        assert len(registry.periodic_task_ids) == 0

    @pytest.mark.asyncio
    async def test_cleanup_handles_already_done_tasks(self, wired, registry):
        """cleanup should not error if a task already finished."""
        state, events = wired

        async def instant():
            return

        task = asyncio.create_task(instant())
        await task  # Let it finish
        registry.track_task(task)

        # Should not raise
        await registry.cleanup(state, events)
        assert len(registry.managed_tasks) == 0

    @pytest.mark.asyncio
    async def test_cleanup_handles_missing_state_key(self, wired, registry):
        """cleanup should not error if a tracked state key was already removed."""
        state, events = wired
        registry.track_state_key("plugin.lifecycle_test.gone")
        # Key never existed in state

        # Should not raise
        await registry.cleanup(state, events)
        assert len(registry.state_keys_set) == 0

    @pytest.mark.asyncio
    async def test_full_cleanup_scenario(self, wired):
        """End-to-end: register state sub, event handler, state keys,
        task, and periodic ID, then clean all up."""
        state, events = wired
        reg = PluginRegistry("full_cleanup")

        # State subscription
        sub_id = state.subscribe("plugin.full_cleanup.*", lambda k, o, n, s: None)
        reg.track_state_subscription(sub_id)

        # Event subscription
        handler_id = events.on("test.event", lambda e, p: None)
        reg.track_event_subscription(handler_id)

        # State key
        state.set("plugin.full_cleanup.value", 100, source="test")
        reg.track_state_key("plugin.full_cleanup.value")

        # Background task
        async def bg():
            await asyncio.sleep(300)

        task = asyncio.create_task(bg())
        reg.track_task(task)

        # Periodic task ID
        reg.track_periodic_task("periodic_abc")

        await reg.cleanup(state, events)

        assert len(reg.state_subscriptions) == 0
        assert len(reg.event_subscriptions) == 0
        assert len(reg.state_keys_set) == 0
        assert len(reg.managed_tasks) == 0
        assert len(reg.periodic_task_ids) == 0
        assert state.get("plugin.full_cleanup.value") is None


# ═══════════════════════════════════════════════════════════
#  4. Error Isolation
# ═══════════════════════════════════════════════════════════


class TestErrorIsolation:
    """Tests for plugin start failure status and callback failure tracking."""

    @pytest.mark.asyncio
    async def test_start_failure_sets_error_status(self, loader):
        register_plugin_class(FailingStartPlugin)
        success = await loader.start_plugin("failing_start")
        assert success is False
        assert loader.get_plugin_status("failing_start") == "error"
        assert "failing_start" not in loader._instances

    @pytest.mark.asyncio
    async def test_start_failure_records_error_message(self, loader):
        register_plugin_class(FailingStartPlugin)
        await loader.start_plugin("failing_start")
        error_msg = loader._errors.get("failing_start", "")
        assert "Kaboom on start" in error_msg

    @pytest.mark.asyncio
    async def test_start_failure_emits_plugin_error_event(self, loader, wired):
        _, events = wired
        register_plugin_class(FailingStartPlugin)

        received = []
        events.on("plugin.error", lambda e, p: received.append(p))

        await loader.start_plugin("failing_start")
        assert len(received) == 1
        assert received[0]["plugin_id"] == "failing_start"
        assert "Kaboom on start" in received[0]["error"]

    @pytest.mark.asyncio
    async def test_start_failure_cleans_up_partial_registrations(self, loader, wired):
        """If start() fails mid-way, any state keys or subscriptions set
        during start() should be cleaned up."""
        state, events = wired

        class PartialFailPlugin:
            PLUGIN_INFO = {
                "id": "partial_fail",
                "name": "Partial Fail",
                "version": "0.1.0",
                "author": "X",
                "description": "Sets state then fails.",
                "category": "utility",
                "license": "MIT",
                "capabilities": ["state_write"],
            }

            async def start(self, api):
                await api.state_set("before_crash", "yes")
                raise RuntimeError("mid-start crash")

            async def stop(self):
                pass

        register_plugin_class(PartialFailPlugin)
        await loader.start_plugin("partial_fail")

        # The state key should have been cleaned up
        assert state.get("plugin.partial_fail.before_crash") is None

    @pytest.mark.asyncio
    async def test_failed_plugin_does_not_block_others(self, loader):
        register_plugin_class(FailingStartPlugin)
        register_plugin_class(ValidPlugin)

        config = {
            "failing_start": {"enabled": True, "config": {}},
            "valid_plugin": {"enabled": True, "config": {}},
        }
        await loader.start_plugins(config)

        assert loader.get_plugin_status("failing_start") == "error"
        assert loader.get_plugin_status("valid_plugin") == "running"

    @pytest.mark.asyncio
    async def test_callback_failure_tracked(self, wired, mock_macros, mock_devices):
        """failure_reporter should be called when a state callback raises."""
        state, events = wired
        failures = []

        api, _reg = _make_api(
            "cbfail",
            ["state_read", "state_write"],
            state, events, mock_macros, mock_devices,
            failure_reporter=lambda: failures.append(1),
        )

        async def bad_callback(key, value, old_value):
            raise RuntimeError("callback error")

        await api.state_subscribe("var.*", bad_callback)
        state.set("var.test_trigger", "boom", source="test")
        await asyncio.sleep(0.1)

        assert len(failures) == 1

    @pytest.mark.asyncio
    async def test_callback_success_resets_failure_count(self, wired, mock_macros, mock_devices):
        """success_reporter should be called when a callback succeeds."""
        state, events = wired
        successes = []

        api, _reg = _make_api(
            "cbsuccess",
            ["state_read"],
            state, events, mock_macros, mock_devices,
            success_reporter=lambda: successes.append(1),
        )

        async def good_callback(key, value, old_value):
            pass

        await api.state_subscribe("var.*", good_callback)
        state.set("var.happy", 1, source="test")
        await asyncio.sleep(0.1)

        assert len(successes) == 1

    @pytest.mark.asyncio
    async def test_event_callback_failure_tracked(self, wired, mock_macros, mock_devices):
        """failure_reporter should fire when an event callback raises."""
        state, events = wired
        failures = []

        api, _reg = _make_api(
            "evtfail",
            ["event_subscribe"],
            state, events, mock_macros, mock_devices,
            failure_reporter=lambda: failures.append(1),
        )

        async def bad_handler(event_name, payload):
            raise RuntimeError("event callback error")

        await api.event_subscribe("test.*", bad_handler)
        await events.emit("test.boom")
        # The gather in EventBus.emit waits for handlers, but the error is
        # caught inside the wrapper so we just need a brief yield
        await asyncio.sleep(0.05)

        assert len(failures) == 1

    @pytest.mark.asyncio
    async def test_event_callback_success_reported(self, wired, mock_macros, mock_devices):
        state, events = wired
        successes = []

        api, _reg = _make_api(
            "evtok",
            ["event_subscribe"],
            state, events, mock_macros, mock_devices,
            success_reporter=lambda: successes.append(1),
        )

        async def good_handler(event_name, payload):
            pass

        await api.event_subscribe("test.*", good_handler)
        await events.emit("test.ok")
        await asyncio.sleep(0.05)

        assert len(successes) == 1

    @pytest.mark.asyncio
    async def test_auto_disable_after_max_callback_failures(self, loader, wired):
        """A plugin that exceeds MAX_CALLBACK_FAILURES consecutive callback
        failures should be auto-disabled."""
        state, events = wired

        class FragilePlugin:
            PLUGIN_INFO = {
                "id": "fragile",
                "name": "Fragile Plugin",
                "version": "0.1.0",
                "author": "X",
                "description": "Breaks on callbacks.",
                "category": "utility",
                "license": "MIT",
                "capabilities": ["state_read"],
            }

            async def start(self, api):
                self.api = api

                async def always_fail(key, value, old_value):
                    raise RuntimeError("always fails")

                await api.state_subscribe("var.*", always_fail)

            async def stop(self):
                pass

        register_plugin_class(FragilePlugin)
        await loader.start_plugin("fragile")
        assert loader.is_running("fragile")

        # Trigger callbacks beyond the threshold
        for i in range(MAX_CALLBACK_FAILURES + 2):
            state.set("var.trigger", i, source="test")
            # Let the async callback and auto-disable task run
            await asyncio.sleep(0.05)

        # Give a little extra time for the auto-disable task to complete
        await asyncio.sleep(0.15)

        assert not loader.is_running("fragile")
        assert loader.get_plugin_status("fragile") == "error"
        assert "Auto-disabled" in loader._errors.get("fragile", "")

    @pytest.mark.asyncio
    async def test_health_check_for_error_plugin(self, loader):
        register_plugin_class(FailingStartPlugin)
        await loader.start_plugin("failing_start")

        health = await loader.get_health("failing_start")
        assert health["status"] == "error"

    @pytest.mark.asyncio
    async def test_health_check_for_unknown_plugin(self, loader):
        health = await loader.get_health("nonexistent")
        assert health["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_successful_start_clears_previous_error(self, loader):
        """If a plugin previously had an error but is restarted successfully,
        the error state should be cleared."""
        register_plugin_class(ValidPlugin)

        # Manually inject an error state
        loader._status["valid_plugin"] = "error"
        loader._errors["valid_plugin"] = "Previous error"

        success = await loader.start_plugin("valid_plugin")
        assert success is True
        assert loader.get_plugin_status("valid_plugin") == "running"
        assert "valid_plugin" not in loader._errors
