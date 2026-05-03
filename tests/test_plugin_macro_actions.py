"""Tests for plugin-registered macro actions.

Covers:
1. Validation of MACRO_ACTIONS at load time (prefix, handler existence, param types)
2. Macro engine dispatch to plugin handlers
3. $var.foo resolution before handler invocation
4. Cleanup on plugin stop (action becomes unknown again)
5. Aggregator endpoints (get_all_macro_actions, get_plugin_info macro_actions)
6. Handler exceptions surface in macro error events
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine
from server.core.plugin_loader import (
    PluginLoader,
    _PLUGIN_CLASS_REGISTRY,
    _REGISTRY_LOCK,
    register_plugin_class,
    validate_macro_actions,
)
from server.core.state_store import StateStore


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
def clean_plugin_registry():
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
    yield
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
        _PLUGIN_CLASS_REGISTRY.update(saved)


# ──── Mock Plugin Classes ────


class AudioPluginGood:
    PLUGIN_INFO = {
        "id": "audio_player",
        "name": "Audio Player",
        "version": "0.1.0",
        "author": "Test",
        "description": "Test audio plugin.",
        "category": "utility",
        "license": "MIT",
        "capabilities": ["state_write"],
    }
    MACRO_ACTIONS = {
        "audio_player.play": {
            "label": "Play Sound",
            "description": "Play a sound on all panels",
            "handler": "action_play",
            "params": [
                {"key": "sound", "type": "text", "label": "Sound", "required": True},
                {"key": "volume", "type": "float", "label": "Volume", "default": 1.0},
            ],
        },
        "audio_player.stop": {
            "label": "Stop All",
            "handler": "action_stop",
            "params": [],
        },
    }

    def __init__(self):
        self.api = None
        self.played = []
        self.stopped_count = 0

    async def start(self, api):
        self.api = api

    async def stop(self):
        pass

    async def action_play(self, params, _context):
        self.played.append(params)

    async def action_stop(self, _params, _context):
        self.stopped_count += 1


class AudioPluginBadPrefix:
    """Action prefix doesn't match plugin id."""
    PLUGIN_INFO = {
        "id": "audio_player",
        "name": "Audio",
        "version": "0.1.0",
        "author": "Test",
        "description": "Bad prefix.",
        "category": "utility",
        "license": "MIT",
    }
    MACRO_ACTIONS = {
        "audio.play": {  # missing audio_player. prefix
            "label": "Play",
            "handler": "action_play",
            "params": [],
        },
    }

    async def start(self, api):
        pass

    async def stop(self):
        pass

    async def action_play(self, params, context):
        pass


class AudioPluginMissingHandler:
    PLUGIN_INFO = {
        "id": "audio_player",
        "name": "Audio",
        "version": "0.1.0",
        "author": "Test",
        "description": "Missing handler.",
        "category": "utility",
        "license": "MIT",
    }
    MACRO_ACTIONS = {
        "audio_player.play": {
            "label": "Play",
            "handler": "no_such_method",
            "params": [],
        },
    }

    async def start(self, api):
        pass

    async def stop(self):
        pass


class AudioPluginSyncHandler:
    PLUGIN_INFO = {
        "id": "audio_player",
        "name": "Audio",
        "version": "0.1.0",
        "author": "Test",
        "description": "Sync handler.",
        "category": "utility",
        "license": "MIT",
    }
    MACRO_ACTIONS = {
        "audio_player.play": {
            "label": "Play",
            "handler": "action_play",
            "params": [],
        },
    }

    async def start(self, api):
        pass

    async def stop(self):
        pass

    def action_play(self, params, context):  # not async — should fail validation
        pass


class AudioPluginRaises:
    PLUGIN_INFO = {
        "id": "audio_player",
        "name": "Audio",
        "version": "0.1.0",
        "author": "Test",
        "description": "Handler raises.",
        "category": "utility",
        "license": "MIT",
    }
    MACRO_ACTIONS = {
        "audio_player.play": {
            "label": "Play",
            "handler": "action_play",
            "params": [],
        },
    }

    async def start(self, api):
        pass

    async def stop(self):
        pass

    async def action_play(self, params, context):
        raise RuntimeError("audio device unavailable")


# ═══════════════════════════════════════════════════════════
#  1. Validation
# ═══════════════════════════════════════════════════════════


class TestMacroActionsValidation:
    def test_valid_actions_pass(self):
        valid, error = validate_macro_actions(
            AudioPluginGood.MACRO_ACTIONS, "audio_player", AudioPluginGood
        )
        assert valid is True
        assert error == ""

    def test_prefix_mismatch_fails(self):
        valid, error = validate_macro_actions(
            AudioPluginBadPrefix.MACRO_ACTIONS, "audio_player", AudioPluginBadPrefix
        )
        assert valid is False
        assert "prefixed" in error
        assert "audio_player." in error

    def test_missing_handler_method_fails(self):
        valid, error = validate_macro_actions(
            AudioPluginMissingHandler.MACRO_ACTIONS,
            "audio_player",
            AudioPluginMissingHandler,
        )
        assert valid is False
        assert "no_such_method" in error
        assert "not found" in error

    def test_sync_handler_fails(self):
        valid, error = validate_macro_actions(
            AudioPluginSyncHandler.MACRO_ACTIONS, "audio_player", AudioPluginSyncHandler
        )
        assert valid is False
        assert "async" in error

    def test_invalid_param_type_fails(self):
        actions = {
            "audio_player.play": {
                "handler": "action_play",
                "params": [{"key": "sound", "type": "weird_type"}],
            },
        }
        valid, error = validate_macro_actions(actions, "audio_player", AudioPluginGood)
        assert valid is False
        assert "weird_type" in error

    def test_select_without_options_fails(self):
        actions = {
            "audio_player.play": {
                "handler": "action_play",
                "params": [{"key": "sound", "type": "select"}],
            },
        }
        valid, error = validate_macro_actions(actions, "audio_player", AudioPluginGood)
        assert valid is False
        assert "options" in error

    def test_select_with_options_source_passes(self):
        actions = {
            "audio_player.play": {
                "handler": "action_play",
                "params": [
                    {
                        "key": "sound",
                        "type": "select",
                        "options_source": "plugin.audio_player.sounds",
                    }
                ],
            },
        }
        valid, error = validate_macro_actions(actions, "audio_player", AudioPluginGood)
        assert valid is True

    def test_duplicate_param_keys_fail(self):
        actions = {
            "audio_player.play": {
                "handler": "action_play",
                "params": [
                    {"key": "sound", "type": "text"},
                    {"key": "sound", "type": "text"},
                ],
            },
        }
        valid, error = validate_macro_actions(actions, "audio_player", AudioPluginGood)
        assert valid is False
        assert "duplicate" in error

    def test_invalid_action_name_chars(self):
        actions = {
            "audio_player.Play-Sound": {  # uppercase + hyphen not allowed
                "handler": "action_play",
                "params": [],
            },
        }
        valid, error = validate_macro_actions(actions, "audio_player", AudioPluginGood)
        assert valid is False


# ═══════════════════════════════════════════════════════════
#  2. Engine dispatch
# ═══════════════════════════════════════════════════════════


class TestMacroEngineDispatch:
    async def test_register_and_dispatch(self, macro_engine):
        called = []

        async def handler(params, context):
            called.append((params, context))

        macro_engine.register_plugin_action("audio_player.play", handler, "audio_player", "Play")
        macro_engine.load_macros([{
            "id": "test",
            "name": "Test",
            "steps": [
                {"action": "audio_player.play", "params": {"sound": "chime", "volume": 0.8}},
            ],
        }])
        await macro_engine.execute("test")
        assert len(called) == 1
        assert called[0][0] == {"sound": "chime", "volume": 0.8}

    async def test_var_resolution_in_params(self, macro_engine, core):
        state, _ = core
        state.set("var.current_sound", "doorbell", source="test")
        called = []

        async def handler(params, context):
            called.append(params)

        macro_engine.register_plugin_action("audio_player.play", handler, "audio_player")
        macro_engine.load_macros([{
            "id": "test",
            "name": "Test",
            "steps": [
                {"action": "audio_player.play", "params": {"sound": "$var.current_sound"}},
            ],
        }])
        await macro_engine.execute("test")
        assert called[0]["sound"] == "doorbell"

    async def test_unknown_plugin_action_raises(self, macro_engine, core):
        _, events = core
        errors = []
        events.on("macro.step_error.test", lambda e, p: errors.append(p))

        macro_engine.load_macros([{
            "id": "test",
            "name": "Test",
            "steps": [
                {"action": "nonexistent_plugin.do_something", "params": {}},
            ],
        }])
        await macro_engine.execute("test")
        # Step error should fire, mentioning the action
        await asyncio.sleep(0.05)
        assert any("nonexistent_plugin.do_something" in str(e.get("error", "")) for e in errors)

    async def test_handler_exception_surfaces(self, macro_engine, core):
        _, events = core
        errors = []
        events.on("macro.step_error.test", lambda e, p: errors.append(p))

        async def handler(_params, _context):
            raise RuntimeError("audio device unavailable")

        macro_engine.register_plugin_action("audio_player.play", handler, "audio_player")
        macro_engine.load_macros([{
            "id": "test",
            "name": "Test",
            "steps": [{"action": "audio_player.play", "params": {}}],
        }])
        await macro_engine.execute("test")
        await asyncio.sleep(0.05)
        assert len(errors) == 1
        assert "audio device unavailable" in errors[0]["error"]

    async def test_double_register_same_action_raises(self, macro_engine):
        async def h1(_p, _c):
            pass

        async def h2(_p, _c):
            pass

        macro_engine.register_plugin_action("audio_player.play", h1, "audio_player")
        with pytest.raises(ValueError, match="already registered"):
            macro_engine.register_plugin_action("audio_player.play", h2, "other_plugin")

    async def test_unregister_plugin_actions(self, macro_engine):
        async def h(_p, _c):
            pass

        macro_engine.register_plugin_action("audio_player.play", h, "audio_player")
        macro_engine.register_plugin_action("audio_player.stop", h, "audio_player")
        macro_engine.register_plugin_action("other.do", h, "other_plugin")

        macro_engine.unregister_plugin_actions("audio_player")
        assert macro_engine.get_plugin_action("audio_player.play") is None
        assert macro_engine.get_plugin_action("audio_player.stop") is None
        assert macro_engine.get_plugin_action("other.do") is not None


# ═══════════════════════════════════════════════════════════
#  3. Loader integration (start/stop registers + cleans up)
# ═══════════════════════════════════════════════════════════


class TestLoaderIntegration:
    async def test_start_registers_actions(self, loader, macro_engine):
        register_plugin_class(AudioPluginGood)
        ok = await loader.start_plugin("audio_player")
        assert ok is True
        assert macro_engine.get_plugin_action("audio_player.play") is not None
        assert macro_engine.get_plugin_action("audio_player.stop") is not None

    async def test_stop_unregisters_actions(self, loader, macro_engine):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")
        await loader.stop_plugin("audio_player")
        assert macro_engine.get_plugin_action("audio_player.play") is None
        assert macro_engine.get_plugin_action("audio_player.stop") is None

    async def test_invalid_macro_actions_blocks_start(self, loader, macro_engine):
        register_plugin_class(AudioPluginBadPrefix)
        ok = await loader.start_plugin("audio_player")
        assert ok is False
        assert macro_engine.get_plugin_action("audio.play") is None

    async def test_macro_dispatches_through_loader(self, loader, macro_engine):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")
        instance = loader._instances["audio_player"]

        macro_engine.load_macros([{
            "id": "play_chime",
            "name": "Chime",
            "steps": [{
                "action": "audio_player.play",
                "params": {"sound": "chime_soft", "volume": 0.5},
            }],
        }])
        await macro_engine.execute("play_chime")
        assert instance.played == [{"sound": "chime_soft", "volume": 0.5}]


# ═══════════════════════════════════════════════════════════
#  4. Aggregator + plugin info responses
# ═══════════════════════════════════════════════════════════


class TestAggregators:
    async def test_get_all_macro_actions(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")

        actions = loader.get_all_macro_actions()
        types = {a["action_type"] for a in actions}
        assert "audio_player.play" in types
        assert "audio_player.stop" in types
        play = next(a for a in actions if a["action_type"] == "audio_player.play")
        assert play["plugin_id"] == "audio_player"
        assert play["plugin_name"] == "Audio Player"
        assert play["label"] == "Play Sound"
        assert play["description"] == "Play a sound on all panels"
        assert len(play["params"]) == 2

    async def test_get_plugin_info_includes_macro_actions(self, loader):
        register_plugin_class(AudioPluginGood)
        await loader.start_plugin("audio_player")

        info = loader.get_plugin_info("audio_player")
        assert info["has_macro_actions"] is True
        assert "macro_actions" in info
        assert "audio_player.play" in info["macro_actions"]
        # 'handler' must be stripped from the public payload
        assert "handler" not in info["macro_actions"]["audio_player.play"]
        assert "label" in info["macro_actions"]["audio_player.play"]

    async def test_no_macro_actions_omits_field(self, loader):
        class PlainPlugin:
            PLUGIN_INFO = {
                "id": "plain",
                "name": "Plain",
                "version": "0.1.0",
                "author": "Test",
                "description": "Nothing.",
                "category": "utility",
                "license": "MIT",
            }

            async def start(self, api):
                pass

            async def stop(self):
                pass

        register_plugin_class(PlainPlugin)
        await loader.start_plugin("plain")
        info = loader.get_plugin_info("plain")
        assert info["has_macro_actions"] is False
        assert "macro_actions" not in info
