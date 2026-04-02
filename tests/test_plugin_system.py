"""
Comprehensive tests for the plugin system.

Covers: PluginAPI, PluginLoader, PluginRegistry, project schema,
project migration, missing plugins, config defaults, hot-reload sync,
platform validation, REST endpoints, and integration lifecycle.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.core.event_bus import EventBus
from server.core.plugin_api import PluginAPI, PluginPermissionError
from server.core.plugin_loader import (
    PluginLoader,
    _PLUGIN_CLASS_REGISTRY,
    get_platform_id,
    register_plugin_class,
    unregister_plugin_class,
)
from server.core.plugin_registry import PluginRegistry
from server.core.plugin_test_harness import PluginTestHarness
from server.core.project_loader import (
    PluginConfig,
    PluginDependency,
    ProjectConfig,
    build_default_plugin_config,
    get_plugin_setup_fields,
)
from server.core.project_migration import migrate_project
from server.core.state_store import StateStore


# ──── Test Plugin Classes ────


class SamplePlugin:
    PLUGIN_INFO = {
        "id": "sample",
        "name": "Sample Plugin",
        "version": "1.0.0",
        "author": "Test",
        "description": "A test plugin.",
        "category": "utility",
        "license": "MIT",
        "platforms": ["all"],
        "capabilities": ["state_read", "state_write", "event_emit", "event_subscribe"],
    }

    CONFIG_SCHEMA = {
        "greeting": {
            "type": "string",
            "label": "Greeting",
            "default": "hello",
        },
        "count": {
            "type": "integer",
            "label": "Count",
            "default": 5,
            "min": 0,
            "max": 100,
        },
    }

    def __init__(self):
        self.api = None
        self.started = False
        self.stopped = False
        self.state_changes = []

    async def start(self, api):
        self.api = api
        self.started = True
        await api.state_set("status", "running")
        await api.state_subscribe("device.*", self.on_device_change)

    async def stop(self):
        self.stopped = True

    async def on_device_change(self, key, value, old_value):
        self.state_changes.append((key, value, old_value))

    async def health_check(self):
        return {"status": "ok", "message": "All good"}


class MacroPlugin:
    PLUGIN_INFO = {
        "id": "macro_test",
        "name": "Macro Test Plugin",
        "version": "0.1.0",
        "author": "Test",
        "description": "Tests macro execution.",
        "category": "utility",
        "license": "MIT",
        "capabilities": ["macro_execute", "device_command"],
    }

    async def start(self, api):
        self.api = api

    async def stop(self):
        pass


class BadStartPlugin:
    PLUGIN_INFO = {
        "id": "bad_start",
        "name": "Bad Start Plugin",
        "version": "0.1.0",
        "author": "Test",
        "description": "Fails on start.",
        "category": "utility",
        "license": "MIT",
        "capabilities": [],
    }

    async def start(self, api):
        raise RuntimeError("Intentional start failure")

    async def stop(self):
        pass


class PlatformPlugin:
    PLUGIN_INFO = {
        "id": "platform_only",
        "name": "Platform Only Plugin",
        "version": "1.0.0",
        "author": "Test",
        "description": "Only runs on linux_arm64.",
        "category": "sensor",
        "license": "MIT",
        "platforms": ["linux_arm64"],
        "capabilities": ["state_read"],
    }

    async def start(self, api):
        self.api = api

    async def stop(self):
        pass


class SetupRequiredPlugin:
    PLUGIN_INFO = {
        "id": "setup_required",
        "name": "Setup Required Plugin",
        "version": "1.0.0",
        "author": "Test",
        "description": "Has required fields.",
        "category": "integration",
        "license": "MIT",
        "capabilities": ["state_read"],
    }

    CONFIG_SCHEMA = {
        "broker_url": {
            "type": "string",
            "label": "Broker URL",
            "required": True,
        },
        "port": {
            "type": "integer",
            "label": "Port",
            "default": 1883,
        },
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
    return PluginRegistry("test_plugin")


@pytest.fixture
def plugin_api(wired, mock_macros, mock_devices):
    state, events = wired
    reg = PluginRegistry("sample")
    return PluginAPI(
        plugin_id="sample",
        capabilities=["state_read", "state_write", "event_emit", "event_subscribe"],
        config={"greeting": "hi"},
        registry=reg,
        state_store=state,
        event_bus=events,
        macro_engine=mock_macros,
        device_manager=mock_devices,
        platform_id="test",
    )


@pytest.fixture
def loader(wired, mock_macros, mock_devices):
    state, events = wired
    return PluginLoader(state, events, mock_macros, mock_devices)


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear the global plugin registry before/after each test."""
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    _PLUGIN_CLASS_REGISTRY.clear()
    yield
    _PLUGIN_CLASS_REGISTRY.clear()
    _PLUGIN_CLASS_REGISTRY.update(saved)


# ═══════════════════════════════════════════════════════════
#  PluginRegistry Tests
# ═══════════════════════════════════════════════════════════


class TestPluginRegistry:

    def test_track_state_subscription(self, registry):
        registry.track_state_subscription("sub_1")
        assert "sub_1" in registry.state_subscriptions

    def test_track_event_subscription(self, registry):
        registry.track_event_subscription("evt_1")
        assert "evt_1" in registry.event_subscriptions

    def test_track_state_key(self, registry):
        registry.track_state_key("plugin.test.x")
        assert "plugin.test.x" in registry.state_keys_set

    def test_track_task(self, registry):
        task = MagicMock()
        registry.track_task(task)
        assert task in registry.managed_tasks

    def test_untrack_task(self, registry):
        task = MagicMock()
        registry.track_task(task)
        registry.untrack_task(task)
        assert task not in registry.managed_tasks

    @pytest.mark.asyncio
    async def test_cleanup_removes_all(self, wired, registry):
        state, events = wired

        # Register some things
        sub_id = state.subscribe("var.*", lambda k, o, n, s: None)
        registry.track_state_subscription(sub_id)

        handler_id = events.on("test.*", lambda e, p: None)
        registry.track_event_subscription(handler_id)

        state.set("plugin.test_plugin.x", 42, source="test")
        registry.track_state_key("plugin.test_plugin.x")

        await registry.cleanup(state, events)

        assert len(registry.state_subscriptions) == 0
        assert len(registry.event_subscriptions) == 0
        assert len(registry.state_keys_set) == 0
        assert len(registry.managed_tasks) == 0
        # State key should be cleared
        assert state.get("plugin.test_plugin.x") is None


# ═══════════════════════════════════════════════════════════
#  PluginAPI Tests
# ═══════════════════════════════════════════════════════════


class TestPluginAPI:

    @pytest.mark.asyncio
    async def test_state_get(self, plugin_api, wired):
        state, _ = wired
        state.set("device.proj.power", "on", source="test")
        result = await plugin_api.state_get("device.proj.power")
        assert result == "on"

    @pytest.mark.asyncio
    async def test_state_get_pattern(self, plugin_api, wired):
        state, _ = wired
        state.set("device.proj.power", "on", source="test")
        state.set("device.proj.input", "hdmi1", source="test")
        result = await plugin_api.state_get_pattern("device.proj.*")
        assert result == {"device.proj.power": "on", "device.proj.input": "hdmi1"}

    @pytest.mark.asyncio
    async def test_state_set_auto_prefix(self, plugin_api, wired):
        state, _ = wired
        await plugin_api.state_set("status", "running")
        assert state.get("plugin.sample.status") == "running"

    @pytest.mark.asyncio
    async def test_state_set_full_key(self, plugin_api, wired):
        state, _ = wired
        await plugin_api.state_set("plugin.sample.connected", True)
        assert state.get("plugin.sample.connected") is True

    @pytest.mark.asyncio
    async def test_state_set_rejects_non_primitives(self, plugin_api):
        with pytest.raises(PluginPermissionError, match="flat primitives"):
            await plugin_api.state_set("data", {"nested": "dict"})

    @pytest.mark.asyncio
    async def test_state_subscribe(self, plugin_api, wired):
        state, _ = wired
        changes = []

        async def on_change(key, value, old_value):
            changes.append((key, value))

        await plugin_api.state_subscribe("device.*", on_change)
        state.set("device.proj.power", "on", source="test")
        await asyncio.sleep(0.05)
        assert len(changes) == 1
        assert changes[0] == ("device.proj.power", "on")

    @pytest.mark.asyncio
    async def test_state_unsubscribe(self, plugin_api, wired):
        state, _ = wired
        changes = []
        sub_id = await plugin_api.state_subscribe(
            "var.*", lambda k, v, o: changes.append(v)
        )
        await plugin_api.state_unsubscribe(sub_id)
        state.set("var.x", 1, source="test")
        await asyncio.sleep(0.05)
        assert len(changes) == 0

    @pytest.mark.asyncio
    async def test_event_emit_auto_prefix(self, plugin_api, wired):
        _, events = wired
        received = []
        events.on("plugin.sample.button.pressed", lambda e, p: received.append(e))
        await plugin_api.event_emit("button.pressed", {"button": 0})
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_event_subscribe(self, plugin_api, wired):
        _, events = wired
        received = []

        async def handler(event_name, payload):
            received.append(event_name)

        await plugin_api.event_subscribe("device.*", handler)
        await events.emit("device.connected.proj1")
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_event_unsubscribe(self, plugin_api, wired):
        _, events = wired
        received = []
        sub_id = await plugin_api.event_subscribe(
            "test.*", lambda e, p: received.append(e)
        )
        await plugin_api.event_unsubscribe(sub_id)
        await events.emit("test.something")
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_capability_enforcement_state_read(self, wired, mock_macros, mock_devices):
        state, events = wired
        api = PluginAPI(
            plugin_id="no_read",
            capabilities=[],
            config={},
            registry=PluginRegistry("no_read"),
            state_store=state,
            event_bus=events,
            macro_engine=mock_macros,
            device_manager=mock_devices,
            platform_id="test",
        )
        with pytest.raises(PluginPermissionError, match="state_read"):
            await api.state_get("var.x")

    @pytest.mark.asyncio
    async def test_capability_enforcement_state_write(self, wired, mock_macros, mock_devices):
        state, events = wired
        api = PluginAPI(
            plugin_id="no_write",
            capabilities=["state_read"],
            config={},
            registry=PluginRegistry("no_write"),
            state_store=state,
            event_bus=events,
            macro_engine=mock_macros,
            device_manager=mock_devices,
            platform_id="test",
        )
        with pytest.raises(PluginPermissionError, match="state_write"):
            await api.state_set("status", "running")

    @pytest.mark.asyncio
    async def test_capability_enforcement_macro(self, wired, mock_macros, mock_devices):
        state, events = wired
        api = PluginAPI(
            plugin_id="no_macro",
            capabilities=["state_read"],
            config={},
            registry=PluginRegistry("no_macro"),
            state_store=state,
            event_bus=events,
            macro_engine=mock_macros,
            device_manager=mock_devices,
            platform_id="test",
        )
        with pytest.raises(PluginPermissionError, match="macro_execute"):
            await api.macro_execute("system_on")

    @pytest.mark.asyncio
    async def test_capability_enforcement_device_command(self, wired, mock_macros, mock_devices):
        state, events = wired
        api = PluginAPI(
            plugin_id="no_device",
            capabilities=[],
            config={},
            registry=PluginRegistry("no_device"),
            state_store=state,
            event_bus=events,
            macro_engine=mock_macros,
            device_manager=mock_devices,
            platform_id="test",
        )
        with pytest.raises(PluginPermissionError, match="device_command"):
            await api.device_command("proj1", "power_on")

    @pytest.mark.asyncio
    async def test_macro_execute(self, wired, mock_macros, mock_devices):
        state, events = wired
        api = PluginAPI(
            plugin_id="m",
            capabilities=["macro_execute"],
            config={},
            registry=PluginRegistry("m"),
            state_store=state,
            event_bus=events,
            macro_engine=mock_macros,
            device_manager=mock_devices,
            platform_id="test",
        )
        await api.macro_execute("system_on")
        mock_macros.execute.assert_called_once_with("system_on")

    @pytest.mark.asyncio
    async def test_device_command(self, wired, mock_macros, mock_devices):
        state, events = wired
        api = PluginAPI(
            plugin_id="d",
            capabilities=["device_command"],
            config={},
            registry=PluginRegistry("d"),
            state_store=state,
            event_bus=events,
            macro_engine=mock_macros,
            device_manager=mock_devices,
            platform_id="test",
        )
        result = await api.device_command("proj1", "power", {"on": True})
        mock_devices.send_command.assert_called_once_with("proj1", "power", {"on": True})
        assert result == {"status": "ok"}

    def test_config_property(self, plugin_api):
        assert plugin_api.config == {"greeting": "hi"}

    def test_plugin_id_property(self, plugin_api):
        assert plugin_api.plugin_id == "sample"

    def test_platform_property(self, plugin_api):
        assert plugin_api.platform == "test"

    def test_log(self, plugin_api):
        # Should not raise
        plugin_api.log("test message")
        plugin_api.log("warning", level="warning")

    @pytest.mark.asyncio
    async def test_create_task(self, plugin_api):
        completed = asyncio.Event()

        async def work():
            completed.set()

        _task = plugin_api.create_task(work(), name="test_work")
        await asyncio.sleep(0.1)
        assert completed.is_set()

    @pytest.mark.asyncio
    async def test_create_periodic_task(self, plugin_api):
        call_count = 0

        async def periodic():
            nonlocal call_count
            call_count += 1

        task_id = plugin_api.create_periodic_task(periodic, 0.05, name="counter")
        await asyncio.sleep(0.2)
        plugin_api.cancel_task(task_id)
        assert call_count >= 2


# ═══════════════════════════════════════════════════════════
#  PluginLoader Tests
# ═══════════════════════════════════════════════════════════


class TestPluginLoader:

    @pytest.mark.asyncio
    async def test_start_and_stop_plugin(self, loader):
        register_plugin_class(SamplePlugin)
        success = await loader.start_plugin("sample", {"greeting": "hi"})
        assert success
        assert loader.get_plugin_status("sample") == "running"
        assert "sample" in loader._instances

        await loader.stop_plugin("sample")
        assert loader.get_plugin_status("sample") == "stopped"
        assert "sample" not in loader._instances

    @pytest.mark.asyncio
    async def test_start_plugin_error_handling(self, loader):
        register_plugin_class(BadStartPlugin)
        success = await loader.start_plugin("bad_start")
        assert not success
        assert loader.get_plugin_status("bad_start") == "error"
        assert "bad_start" not in loader._instances
        assert "Intentional start failure" in loader._errors.get("bad_start", "")

    @pytest.mark.asyncio
    async def test_missing_plugin_detection(self, loader, wired):
        state, _ = wired
        plugins_config = {
            "nonexistent": {"enabled": True, "config": {}},
        }
        await loader.start_plugins(plugins_config)
        assert "nonexistent" in loader._missing_plugins
        assert state.get("plugin.nonexistent.missing") is True

    @pytest.mark.asyncio
    async def test_activate_after_install(self, loader, wired):
        state, _ = wired
        # Simulate missing plugin
        plugins_config = {"sample": {"enabled": True, "config": {}}}
        await loader.start_plugins(plugins_config)
        assert "sample" in loader._missing_plugins

        # "Install" it
        register_plugin_class(SamplePlugin)
        result = await loader.activate_plugin("sample", {})
        assert result["activated"]
        assert loader.get_plugin_status("sample") == "running"
        assert state.get("plugin.sample.missing") is None

    @pytest.mark.asyncio
    async def test_platform_incompatible(self, loader, wired):
        state, _ = wired
        register_plugin_class(PlatformPlugin)
        plugins_config = {
            "platform_only": {"enabled": True, "config": {}},
        }
        await loader.start_plugins(plugins_config)
        assert "platform_only" in loader._incompatible_plugins
        assert loader.get_plugin_status("platform_only") == "incompatible"

    @pytest.mark.asyncio
    async def test_stop_all(self, loader):
        register_plugin_class(SamplePlugin)
        register_plugin_class(MacroPlugin)
        await loader.start_plugin("sample", {})
        await loader.start_plugin("macro_test", {})
        assert len(loader._instances) == 2
        await loader.stop_all()
        assert len(loader._instances) == 0

    @pytest.mark.asyncio
    async def test_health_check(self, loader):
        register_plugin_class(SamplePlugin)
        await loader.start_plugin("sample", {})
        health = await loader.get_health("sample")
        assert health["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_not_running(self, loader):
        health = await loader.get_health("nonexistent")
        assert health["status"] == "unknown"

    def test_list_plugins(self, loader):
        register_plugin_class(SamplePlugin)
        plugins = loader.list_plugins()
        assert len(plugins) == 1
        assert plugins[0]["plugin_id"] == "sample"
        assert plugins[0]["installed"]

    def test_list_plugins_includes_missing(self, loader):
        loader._missing_plugins["ghost"] = {
            "plugin_id": "ghost",
            "reason": "Not installed",
        }
        plugins = loader.list_plugins()
        assert any(p["plugin_id"] == "ghost" and p["status"] == "missing" for p in plugins)

    def test_get_plugin_info(self, loader):
        register_plugin_class(SamplePlugin)
        info = loader.get_plugin_info("sample")
        assert info is not None
        assert info["name"] == "Sample Plugin"
        assert info["has_config_schema"]

    def test_get_plugin_info_not_found(self, loader):
        assert loader.get_plugin_info("nonexistent") is None

    def test_validate_plugins(self, loader):
        register_plugin_class(SamplePlugin)
        result = loader.validate_plugins({"sample": {}, "ghost": {}})
        assert len(result["available"]) == 1
        assert len(result["missing"]) == 1

    def test_validate_manifest_valid(self, loader):
        valid, error = loader.validate_manifest(SamplePlugin)
        assert valid
        assert error == ""

    def test_validate_manifest_bad_license(self, loader):

        class GplPlugin:
            PLUGIN_INFO = {
                "id": "gpl",
                "name": "GPL Plugin",
                "version": "1.0.0",
                "author": "Test",
                "description": "Bad license.",
                "category": "utility",
                "license": "GPL-3.0",
                "capabilities": [],
            }

        valid, error = loader.validate_manifest(GplPlugin)
        assert not valid
        assert "MIT-compatible" in error

    def test_validate_manifest_missing_fields(self, loader):

        class NoFieldsPlugin:
            PLUGIN_INFO = {"id": "broken"}

        valid, error = loader.validate_manifest(NoFieldsPlugin)
        assert not valid
        assert "Missing required fields" in error

    def test_validate_manifest_invalid_capability(self, loader):

        class BadCapPlugin:
            PLUGIN_INFO = {
                "id": "badcap",
                "name": "Bad Cap",
                "version": "1.0.0",
                "author": "Test",
                "description": "Bad capability.",
                "category": "utility",
                "license": "MIT",
                "capabilities": ["teleport"],
            }

        valid, error = loader.validate_manifest(BadCapPlugin)
        assert not valid
        assert "Unknown capabilities" in error

    def test_validate_manifest_invalid_category(self, loader):

        class BadCatPlugin:
            PLUGIN_INFO = {
                "id": "badcat",
                "name": "Bad Cat",
                "version": "1.0.0",
                "author": "Test",
                "description": "Bad category.",
                "category": "teleporter",
                "license": "MIT",
                "capabilities": [],
            }

        valid, error = loader.validate_manifest(BadCatPlugin)
        assert not valid
        assert "Invalid category" in error

    @pytest.mark.asyncio
    async def test_disabled_plugin_not_started(self, loader):
        register_plugin_class(SamplePlugin)
        await loader.start_plugins({"sample": {"enabled": False, "config": {}}})
        assert "sample" not in loader._instances


# ═══════════════════════════════════════════════════════════
#  Plugin Registry (global) Tests
# ═══════════════════════════════════════════════════════════


class TestPluginClassRegistry:

    def test_register_and_get(self):
        register_plugin_class(SamplePlugin)
        assert _PLUGIN_CLASS_REGISTRY.get("sample") is SamplePlugin

    def test_unregister(self):
        register_plugin_class(SamplePlugin)
        assert unregister_plugin_class("sample")
        assert "sample" not in _PLUGIN_CLASS_REGISTRY

    def test_unregister_not_found(self):
        assert not unregister_plugin_class("nonexistent")


# ═══════════════════════════════════════════════════════════
#  Project Schema Tests
# ═══════════════════════════════════════════════════════════


class TestProjectSchema:

    def test_plugin_config_model(self):
        pc = PluginConfig(enabled=True, config={"broker": "localhost"})
        assert pc.enabled
        assert pc.config["broker"] == "localhost"

    def test_plugin_config_defaults(self):
        pc = PluginConfig()
        assert not pc.enabled
        assert pc.config == {}

    def test_plugin_dependency_model(self):
        pd = PluginDependency(
            plugin_id="mqtt",
            plugin_name="MQTT Bridge",
            version="1.0.0",
            source="community",
            platforms=["all"],
        )
        assert pd.plugin_id == "mqtt"
        assert pd.platforms == ["all"]

    def test_plugin_dependency_defaults(self):
        pd = PluginDependency(plugin_id="test")
        assert pd.plugin_name == ""
        assert pd.source == ""
        assert pd.platforms == ["all"]

    def test_project_config_has_plugins(self):
        """ProjectConfig accepts plugins and plugin_dependencies fields."""
        data = {
            "openavc_version": "0.4.0",
            "project": {"id": "test", "name": "Test"},
            "plugins": {
                "mqtt": {"enabled": True, "config": {"broker": "localhost"}},
            },
            "plugin_dependencies": [
                {"plugin_id": "mqtt", "plugin_name": "MQTT Bridge"},
            ],
        }
        config = ProjectConfig(**data)
        assert "mqtt" in config.plugins
        assert config.plugins["mqtt"].enabled
        assert len(config.plugin_dependencies) == 1

    def test_project_config_empty_plugins(self):
        """ProjectConfig works with empty plugins (backwards compatibility)."""
        data = {
            "openavc_version": "0.4.0",
            "project": {"id": "test", "name": "Test"},
        }
        config = ProjectConfig(**data)
        assert config.plugins == {}
        assert config.plugin_dependencies == []


# ═══════════════════════════════════════════════════════════
#  Project Migration Tests
# ═══════════════════════════════════════════════════════════


class TestProjectMigration:

    def test_migrate_0_2_to_0_3(self):
        data = {
            "openavc_version": "0.2.0",
            "project": {"id": "test", "name": "Test"},
            "devices": [],
            "connections": {},
            "driver_dependencies": [],
        }
        result, migrated = migrate_project(data)
        assert migrated
        assert result["openavc_version"] == "0.4.0"
        assert result["plugins"] == {}
        assert result["plugin_dependencies"] == []

    def test_migrate_0_1_to_0_3(self):
        """Full migration chain: 0.1.0 → 0.2.0 → 0.3.0."""
        data = {
            "openavc_version": "0.1.0",
            "project": {"id": "test", "name": "Test"},
            "devices": [
                {"id": "proj1", "driver": "pjlink", "name": "Projector", "config": {"host": "1.2.3.4"}},
            ],
        }
        result, migrated = migrate_project(data)
        assert migrated
        assert result["openavc_version"] == "0.4.0"
        # 0.1 → 0.2: host moved to connections
        assert "host" not in result["devices"][0]["config"]
        assert result["connections"]["proj1"]["host"] == "1.2.3.4"
        # 0.2 → 0.3: plugins added
        assert result["plugins"] == {}
        assert result["plugin_dependencies"] == []

    def test_no_migration_needed(self):
        data = {
            "openavc_version": "0.4.0",
            "project": {"id": "test", "name": "Test"},
            "plugins": {"mqtt": {"enabled": True, "config": {}}},
            "plugin_dependencies": [],
        }
        result, migrated = migrate_project(data)
        assert not migrated

    def test_migrate_preserves_existing_plugins(self):
        """If plugins already exists in a 0.2.0 project (shouldn't happen, but be safe)."""
        data = {
            "openavc_version": "0.2.0",
            "project": {"id": "test", "name": "Test"},
            "plugins": {"existing": {"enabled": True, "config": {}}},
        }
        result, migrated = migrate_project(data)
        assert migrated
        # setdefault preserves existing
        assert "existing" in result["plugins"]


# ═══════════════════════════════════════════════════════════
#  Config Defaults & Setup Fields Tests
# ═══════════════════════════════════════════════════════════


class TestConfigHelpers:

    def test_build_default_config(self):
        schema = SamplePlugin.CONFIG_SCHEMA
        defaults = build_default_plugin_config(schema)
        assert defaults == {"greeting": "hello", "count": 5}

    def test_build_default_config_nested_group(self):
        schema = {
            "advanced": {
                "type": "group",
                "fields": {
                    "timeout": {"type": "integer", "default": 5000},
                    "debug": {"type": "boolean", "default": False},
                },
            },
            "name": {"type": "string", "default": "test"},
        }
        defaults = build_default_plugin_config(schema)
        assert defaults == {
            "advanced": {"timeout": 5000, "debug": False},
            "name": "test",
        }

    def test_build_default_config_no_defaults(self):
        schema = {
            "url": {"type": "string", "required": True},
        }
        defaults = build_default_plugin_config(schema)
        assert defaults == {}

    def test_get_setup_fields(self):
        schema = SetupRequiredPlugin.CONFIG_SCHEMA
        fields = get_plugin_setup_fields(schema)
        assert "broker_url" in fields
        assert "port" not in fields

    def test_get_setup_fields_empty(self):
        schema = SamplePlugin.CONFIG_SCHEMA
        fields = get_plugin_setup_fields(schema)
        assert fields == {}


# ═══════════════════════════════════════════════════════════
#  Test Harness Tests
# ═══════════════════════════════════════════════════════════


class TestPluginTestHarness:

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        harness = PluginTestHarness()
        plugin = SamplePlugin()

        await harness.start_plugin(plugin)
        assert plugin.started

        # Plugin set its initial state
        assert await harness.state_get("plugin.sample.status") == "running"

        await harness.stop_plugin(plugin)
        assert plugin.stopped
        # Cleanup should have cleared state
        assert await harness.state_get("plugin.sample.status") is None

    @pytest.mark.asyncio
    async def test_state_change_callback(self):
        harness = PluginTestHarness()
        plugin = SamplePlugin()
        await harness.start_plugin(plugin)

        await harness.state_set("device.proj.power", "on")
        await asyncio.sleep(0.05)

        assert len(plugin.state_changes) == 1
        assert plugin.state_changes[0][0] == "device.proj.power"
        assert plugin.state_changes[0][1] == "on"

        await harness.stop_plugin(plugin)

    @pytest.mark.asyncio
    async def test_log_tracking(self):
        harness = PluginTestHarness()
        plugin = SamplePlugin()
        api = await harness.start_plugin(plugin)

        api.log("hello world")
        assert harness.log_contains("hello world")
        assert len(harness.get_logs("sample")) == 1

        await harness.stop_plugin(plugin)

    @pytest.mark.asyncio
    async def test_macro_tracking(self):
        harness = PluginTestHarness()
        plugin = MacroPlugin()
        await harness.start_plugin(plugin, capabilities=["macro_execute", "device_command"])

        await plugin.api.macro_execute("system_on")
        assert harness.get_executed_macros() == ["system_on"]

        await harness.stop_plugin(plugin)

    @pytest.mark.asyncio
    async def test_device_command_tracking(self):
        harness = PluginTestHarness()
        plugin = MacroPlugin()
        await harness.start_plugin(plugin, capabilities=["macro_execute", "device_command"])

        await plugin.api.device_command("proj1", "power", {"on": True})
        cmds = harness.get_device_commands()
        assert len(cmds) == 1
        assert cmds[0] == ("proj1", "power", {"on": True})

        await harness.stop_plugin(plugin)


# ═══════════════════════════════════════════════════════════
#  Integration Test — Full Lifecycle
# ═══════════════════════════════════════════════════════════


class TestIntegration:

    @pytest.mark.asyncio
    async def test_full_plugin_lifecycle(self, loader, wired):
        """Load → start → verify → config change restart → stop → cleanup."""
        state, events = wired

        register_plugin_class(SamplePlugin)

        # Start
        plugins_config = {
            "sample": {"enabled": True, "config": {"greeting": "hi"}},
        }
        await loader.start_plugins(plugins_config)
        assert loader.get_plugin_status("sample") == "running"
        assert state.get("plugin.sample.status") == "running"

        # Verify health
        health = await loader.get_health("sample")
        assert health["status"] == "ok"

        # Stop and restart with new config
        await loader.stop_plugin("sample")
        assert loader.get_plugin_status("sample") == "stopped"
        assert state.get("plugin.sample.status") is None  # Cleaned up

        await loader.start_plugin("sample", {"greeting": "bye"})
        assert loader.get_plugin_status("sample") == "running"

        # Final stop
        await loader.stop_all()
        assert len(loader._instances) == 0

    @pytest.mark.asyncio
    async def test_error_plugin_doesnt_affect_others(self, loader, wired):
        """A plugin that fails to start shouldn't prevent others."""
        register_plugin_class(SamplePlugin)
        register_plugin_class(BadStartPlugin)

        plugins_config = {
            "bad_start": {"enabled": True, "config": {}},
            "sample": {"enabled": True, "config": {}},
        }
        await loader.start_plugins(plugins_config)

        assert loader.get_plugin_status("bad_start") == "error"
        assert loader.get_plugin_status("sample") == "running"


# ═══════════════════════════════════════════════════════════
#  Platform Detection Test
# ═══════════════════════════════════════════════════════════


def test_get_platform_id():
    """Platform ID should be a recognized value."""
    pid = get_platform_id()
    assert pid in ("win_x64", "linux_x64", "linux_arm64", "unknown")
