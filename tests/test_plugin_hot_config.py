"""Hot config apply: PluginLoader.apply_config / restart_or_apply.

A plugin that defines ``on_config_changed`` gets config changes applied to
its running instance (the loader swaps the live api.config first); returning
False or raising falls back to the normal stop/start restart.
"""

import pytest

from server.core.event_bus import EventBus
from server.core.plugin_api import PluginAPI
from server.core.plugin_loader import PluginLoader
from server.core.plugin_registry import PluginRegistry
from server.core.plugin_test_harness import (
    MockDeviceManager,
    MockMacroEngine,
    PluginTestHarness,
)
from server.core.state_store import StateStore


class _HotPlugin:
    PLUGIN_INFO = {"id": "hot", "name": "Hot", "version": "1.0.0", "capabilities": []}

    def __init__(self, result=True, raise_error=False):
        self.api = None
        self.seen = None
        self._result = result
        self._raise = raise_error

    async def start(self, api):
        self.api = api

    async def stop(self):
        pass

    async def on_config_changed(self, new_config):
        if self._raise:
            raise RuntimeError("boom")
        self.seen = new_config
        return self._result


class _ColdPlugin:
    """No on_config_changed — always restarted on config change."""

    PLUGIN_INFO = {"id": "cold", "name": "Cold", "version": "1.0.0", "capabilities": []}

    async def start(self, api):
        self.api = api

    async def stop(self):
        pass


def _make_loader_with(plugin, plugin_id):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    loader = PluginLoader(state, events, MockMacroEngine(), MockDeviceManager())
    api = PluginAPI(
        plugin_id=plugin_id,
        capabilities=[],
        config={"old": True},
        registry=PluginRegistry(plugin_id),
        state_store=state,
        event_bus=events,
        macro_engine=MockMacroEngine(),
        device_manager=MockDeviceManager(),
        platform_id="test",
    )
    loader._instances[plugin_id] = plugin
    loader._apis[plugin_id] = api
    return loader, api


@pytest.mark.asyncio
async def test_apply_config_calls_hook_with_swapped_config():
    plugin = _HotPlugin()
    loader, api = _make_loader_with(plugin, "hot")
    handled = await loader.apply_config("hot", {"brightness": 40})
    assert handled is True
    assert plugin.seen == {"brightness": 40}
    # The live api.config was swapped before the hook ran.
    assert api.config == {"brightness": 40}


@pytest.mark.asyncio
async def test_apply_config_without_hook_returns_false():
    plugin = _ColdPlugin()
    loader, _api = _make_loader_with(plugin, "cold")
    assert await loader.apply_config("cold", {"x": 1}) is False


@pytest.mark.asyncio
async def test_apply_config_hook_false_or_raise_falls_back():
    declined = _HotPlugin(result=False)
    loader, _api = _make_loader_with(declined, "hot")
    assert await loader.apply_config("hot", {"x": 1}) is False

    raising = _HotPlugin(raise_error=True)
    loader2, _api2 = _make_loader_with(raising, "hot")
    assert await loader2.apply_config("hot", {"x": 1}) is False


@pytest.mark.asyncio
async def test_restart_or_apply_hot_path_skips_restart(monkeypatch):
    plugin = _HotPlugin()
    loader, _api = _make_loader_with(plugin, "hot")
    calls = []

    async def _record_stop(pid):
        calls.append(("stop", pid))

    async def _record_start(pid, config=None):
        calls.append(("start", pid))
        return True

    monkeypatch.setattr(loader, "stop_plugin", _record_stop)
    monkeypatch.setattr(loader, "start_plugin", _record_start)

    assert await loader.restart_or_apply("hot", {"x": 1}) is True
    assert calls == []  # hot apply -> no restart


@pytest.mark.asyncio
async def test_restart_or_apply_falls_back_to_restart(monkeypatch):
    plugin = _HotPlugin(result=False)
    loader, _api = _make_loader_with(plugin, "hot")
    calls = []

    async def _record_stop(pid):
        calls.append(("stop", pid))

    async def _record_start(pid, config=None):
        calls.append(("start", pid))
        return True

    monkeypatch.setattr(loader, "stop_plugin", _record_stop)
    monkeypatch.setattr(loader, "start_plugin", _record_start)

    assert await loader.restart_or_apply("hot", {"x": 1}) is True
    assert calls == [("stop", "hot"), ("start", "hot")]


@pytest.mark.asyncio
async def test_restart_or_apply_noop_when_not_running():
    loader = PluginLoader(
        StateStore(), EventBus(), MockMacroEngine(), MockDeviceManager()
    )
    assert await loader.restart_or_apply("ghost", {"x": 1}) is False


@pytest.mark.asyncio
async def test_harness_apply_config_helper():
    harness = PluginTestHarness()
    plugin = _HotPlugin()
    await harness.start_plugin(plugin, config={"a": 1})
    assert await harness.apply_config(plugin, {"a": 2}) is True
    assert plugin.seen == {"a": 2}
    assert plugin.api.config == {"a": 2}

    cold = _ColdPlugin()
    await harness.start_plugin(cold, config={})
    assert await harness.apply_config(cold, {"b": 1}) is False
