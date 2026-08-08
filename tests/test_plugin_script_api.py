"""Tests for the plugin script API extension.

Covers:
1. SCRIPT_API validation (handler exists, async/sync match, identifier rules)
2. Loader registers/unregisters methods on the plugins proxy
3. Scripts can call openavc.plugins.<id>.<method>(*args, **kwargs)
4. Both async and sync handlers work
5. Missing plugin / missing method → AttributeError with clear message
6. Two plugins coexist without colliding
7. Aggregator + per-plugin info responses
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.plugin_loader import (
    PluginLoader,
    _PLUGIN_CLASS_REGISTRY,
    _REGISTRY_LOCK,
    register_plugin_class,
    validate_script_api,
)
from openavc.core.script_api import plugins as plugins_proxy
from openavc.core.state_store import StateStore


# ──── Fixtures ────


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def macro_engine(core):
    state, events = core
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    return MacroEngine(state, events, devices)


@pytest.fixture
def loader(core, macro_engine):
    state, events = core
    devices = MagicMock()
    devices.send_command = AsyncMock()
    return PluginLoader(state, events, macro_engine, devices)


@pytest.fixture(autouse=True)
def clean_registries():
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
    # Also clear the module-level plugins proxy so each test starts fresh
    saved_proxies = dict(plugins_proxy._plugins)
    plugins_proxy._plugins.clear()
    yield
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
        _PLUGIN_CLASS_REGISTRY.update(saved)
    plugins_proxy._plugins.clear()
    plugins_proxy._plugins.update(saved_proxies)


# ──── Mock plugin classes ────


class AudioPluginGood:
    PLUGIN_INFO = {
        "id": "audio_player",
        "name": "Audio Player",
        "version": "0.1.0",
        "author": "Test",
        "description": "Test.",
        "category": "utility",
        "license": "MIT",
        "capabilities": ["state_write"],
    }
    SCRIPT_API = {
        "play": {"handler": "script_play", "doc": "Play a sound."},
        "list_sounds": {
            "handler": "script_list_sounds",
            "doc": "Return available sounds.",
            "sync": True,
        },
    }

    def __init__(self):
        self.played = []

    async def start(self, api):
        pass

    async def stop(self):
        pass

    async def script_play(self, sound: str, volume: float = 1.0) -> str:
        self.played.append((sound, volume))
        return f"playing {sound} at {volume}"

    def script_list_sounds(self) -> list[str]:
        return ["chime_soft", "doorbell"]


class PluginBadIdentifier:
    PLUGIN_INFO = {
        "id": "audio-player",  # hyphen — not a valid Python identifier
        "name": "Bad",
        "version": "0.1.0",
        "author": "Test",
        "description": "Bad id.",
        "category": "utility",
        "license": "MIT",
    }
    SCRIPT_API = {"play": {"handler": "script_play"}}

    async def start(self, api):
        pass

    async def stop(self):
        pass

    async def script_play(self):
        pass


class PluginAsyncMismatch:
    PLUGIN_INFO = {
        "id": "audio_player",
        "name": "Audio",
        "version": "0.1.0",
        "author": "Test",
        "description": "Async/sync mismatch.",
        "category": "utility",
        "license": "MIT",
    }
    SCRIPT_API = {
        "play": {"handler": "script_play", "sync": True},  # but script_play is async
    }

    async def start(self, api):
        pass

    async def stop(self):
        pass

    async def script_play(self):
        pass


class PluginMissingHandler:
    PLUGIN_INFO = {
        "id": "audio_player",
        "name": "Audio",
        "version": "0.1.0",
        "author": "Test",
        "description": "Missing handler.",
        "category": "utility",
        "license": "MIT",
    }
    SCRIPT_API = {"play": {"handler": "no_such_method"}}

    async def start(self, api):
        pass

    async def stop(self):
        pass


class PluginAOnly:
    PLUGIN_INFO = {
        "id": "plugin_a",
        "name": "Plugin A",
        "version": "0.1.0",
        "author": "Test",
        "description": "A.",
        "category": "utility",
        "license": "MIT",
    }
    SCRIPT_API = {"hello": {"handler": "script_hello"}}

    async def start(self, api):
        pass

    async def stop(self):
        pass

    async def script_hello(self):
        return "from a"


class PluginBOnly:
    PLUGIN_INFO = {
        "id": "plugin_b",
        "name": "Plugin B",
        "version": "0.1.0",
        "author": "Test",
        "description": "B.",
        "category": "utility",
        "license": "MIT",
    }
    SCRIPT_API = {"hello": {"handler": "script_hello"}}

    async def start(self, api):
        pass

    async def stop(self):
        pass

    async def script_hello(self):
        return "from b"


# ═══════════════════════════════════════════════════════════
#  1. Validation
# ═══════════════════════════════════════════════════════════


class TestScriptApiValidation:
    def test_valid_script_api_passes(self):
        valid, error = validate_script_api(
            AudioPluginGood.SCRIPT_API, "audio_player", AudioPluginGood
        )
        assert valid is True, error

    def test_invalid_plugin_id_fails(self):
        valid, error = validate_script_api(
            PluginBadIdentifier.SCRIPT_API, "audio-player", PluginBadIdentifier
        )
        assert valid is False
        assert "identifier" in error

    def test_async_sync_mismatch_fails(self):
        valid, error = validate_script_api(
            PluginAsyncMismatch.SCRIPT_API, "audio_player", PluginAsyncMismatch
        )
        assert valid is False
        assert "async" in error.lower()

    def test_missing_handler_fails(self):
        valid, error = validate_script_api(
            PluginMissingHandler.SCRIPT_API, "audio_player", PluginMissingHandler
        )
        assert valid is False
        assert "no_such_method" in error

    def test_underscore_method_rejected(self):
        actions = {"_private": {"handler": "script_play"}}
        valid, error = validate_script_api(actions, "audio_player", AudioPluginGood)
        assert valid is False
        assert "identifier" in error

    def test_uppercase_method_rejected(self):
        actions = {"PlaySound": {"handler": "script_play"}}
        valid, error = validate_script_api(actions, "audio_player", AudioPluginGood)
        assert valid is False

    def test_sync_handler_with_sync_flag_passes(self):
        actions = {
            "list_sounds": {"handler": "script_list_sounds", "sync": True},
        }
        valid, error = validate_script_api(actions, "audio_player", AudioPluginGood)
        assert valid is True, error

    def test_sync_handler_without_sync_flag_fails(self):
        # Default expects async; AudioPluginGood.script_list_sounds is sync.
        actions = {"list_sounds": {"handler": "script_list_sounds"}}
        valid, error = validate_script_api(actions, "audio_player", AudioPluginGood)
        assert valid is False
        assert "async" in error.lower()


# ═══════════════════════════════════════════════════════════
#  2. Registration + dispatch
# ═══════════════════════════════════════════════════════════


class TestRegistrationAndDispatch:
    async def test_async_method_dispatches(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")

        result = await plugins_proxy.audio_player.play("chime_soft", volume=0.7)
        assert result == "playing chime_soft at 0.7"

    async def test_sync_method_dispatches(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")

        result = plugins_proxy.audio_player.list_sounds()
        assert result == ["chime_soft", "doorbell"]

    async def test_methods_bound_to_instance(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")
        instance = loader._instances["audio_player"]

        await plugins_proxy.audio_player.play("doorbell")
        # The handler updated the instance — proves we registered the bound method
        assert instance.played == [("doorbell", 1.0)]

    async def test_missing_method_raises_attribute_error(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")

        with pytest.raises(AttributeError, match="no script method 'play_loud'"):
            plugins_proxy.audio_player.play_loud  # noqa: B018

    async def test_missing_plugin_raises_attribute_error(self):
        with pytest.raises(AttributeError, match="not installed"):
            plugins_proxy.nonexistent_plugin  # noqa: B018

    async def test_two_plugins_coexist(self, loader):
        register_plugin_class(PluginAOnly)
        register_plugin_class(PluginBOnly)
        await loader.start_plugin("plugin_a")
        await loader.start_plugin("plugin_b")

        assert await plugins_proxy.plugin_a.hello() == "from a"
        assert await plugins_proxy.plugin_b.hello() == "from b"

    async def test_plugin_proxy_is_read_only(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")

        with pytest.raises(AttributeError, match="read-only"):
            plugins_proxy.audio_player.foo = "bar"
        with pytest.raises(AttributeError, match="read-only"):
            plugins_proxy.audio_player_new = "thing"


# ═══════════════════════════════════════════════════════════
#  3. Lifecycle (stop removes methods)
# ═══════════════════════════════════════════════════════════


class TestLifecycle:
    async def test_stop_removes_methods(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")
        # Confirm it works
        await plugins_proxy.audio_player.play("a")
        # Now stop
        await loader.stop_plugin("audio_player")
        # Plugin id is no longer "running" — proxy raises clearly
        with pytest.raises(AttributeError, match="not currently running"):
            plugins_proxy.audio_player  # noqa: B018

    async def test_failed_start_cleans_up_partial_registration(self, loader):
        # A plugin whose start() raises after we've already registered the
        # script API should leave no residue on the proxy.
        class StartFailsPlugin:
            PLUGIN_INFO = {
                "id": "explodes",
                "name": "Explodes",
                "version": "0.1.0",
                "author": "Test",
                "description": "Boom.",
                "category": "utility",
                "license": "MIT",
            }
            SCRIPT_API = {"hello": {"handler": "script_hello"}}

            async def start(self, _api):
                raise RuntimeError("boom")

            async def stop(self):
                pass

            async def script_hello(self):
                return "hi"

        register_plugin_class(StartFailsPlugin)
        ok = await loader.start_plugin("explodes")
        assert ok is False
        # Even though script API was partially registered earlier in the
        # except path of start_plugin, the cleanup hook should have removed it
        with pytest.raises(AttributeError):
            plugins_proxy.explodes  # noqa: B018


# ═══════════════════════════════════════════════════════════
#  4. Aggregator + per-plugin field
# ═══════════════════════════════════════════════════════════


class TestAggregator:
    async def test_get_all_script_api(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")

        methods = loader.get_all_script_api()
        names = {(m["plugin_id"], m["method"]) for m in methods}
        assert ("audio_player", "play") in names
        assert ("audio_player", "list_sounds") in names

        play = next(
            m for m in methods
            if m["plugin_id"] == "audio_player" and m["method"] == "play"
        )
        assert play["plugin_name"] == "Audio Player"
        assert play["doc"] == "Play a sound."
        assert play["sync"] is False

        list_sounds = next(
            m for m in methods
            if m["plugin_id"] == "audio_player" and m["method"] == "list_sounds"
        )
        assert list_sounds["sync"] is True

    async def test_per_plugin_info_includes_script_api(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")

        info = loader.get_plugin_info("audio_player")
        assert info["has_script_api"] is True
        assert "script_api" in info
        assert "play" in info["script_api"]
        # Internal handler name must not leak
        assert "handler" not in info["script_api"]["play"]
        assert info["script_api"]["play"]["doc"] == "Play a sound."

    async def test_no_script_api_omits_field(self, loader):
        class NoScriptPlugin:
            PLUGIN_INFO = {
                "id": "no_script",
                "name": "No Script",
                "version": "0.1.0",
                "author": "Test",
                "description": "Nothing.",
                "category": "utility",
                "license": "MIT",
            }

            async def start(self, _api):
                pass

            async def stop(self):
                pass

        register_plugin_class(NoScriptPlugin)
        await loader.start_plugin("no_script")

        info = loader.get_plugin_info("no_script")
        assert info["has_script_api"] is False
        assert "script_api" not in info
