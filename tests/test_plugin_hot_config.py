"""Hot config apply: PluginLoader.apply_config / restart_or_apply.

A plugin that defines ``on_config_changed`` gets config changes applied to
its running instance (the loader swaps the live api.config first); returning
False or raising falls back to the normal stop/start restart.
"""

import asyncio

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.plugin_api import PluginAPI
from openavc.core.plugin_loader import PluginLoader
from openavc.core.plugin_registry import PluginRegistry
from openavc.core.plugin_test_harness import (
    MockDeviceManager,
    MockMacroEngine,
    PluginTestHarness,
)
from openavc.core.state_store import StateStore


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

    # restart_or_apply holds the per-plugin lock, so it restarts via the
    # _locked variants rather than the public (lock-taking) methods.
    monkeypatch.setattr(loader, "_stop_plugin_locked", _record_stop)
    monkeypatch.setattr(loader, "_start_plugin_locked", _record_start)

    assert await loader.restart_or_apply("hot", {"x": 1}) == "hot_applied"
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

    monkeypatch.setattr(loader, "_stop_plugin_locked", _record_stop)
    monkeypatch.setattr(loader, "_start_plugin_locked", _record_start)

    assert await loader.restart_or_apply("hot", {"x": 1}) == "restarted"
    assert calls == [("stop", "hot"), ("start", "hot")]


@pytest.mark.asyncio
async def test_restart_or_apply_noop_when_not_running():
    loader = PluginLoader(
        StateStore(), EventBus(), MockMacroEngine(), MockDeviceManager()
    )
    assert await loader.restart_or_apply("ghost", {"x": 1}) == "not_running"


@pytest.mark.asyncio
async def test_apply_config_hung_hook_times_out(monkeypatch):
    """A hook that never returns must not wedge apply_config — restart_or_apply
    holds the per-plugin lifecycle lock across it, so an unbounded await would
    block every future stop/start of that plugin."""
    from openavc.core import plugin_loader as pl

    monkeypatch.setattr(pl, "PLUGIN_APPLY_TIMEOUT", 0.05)

    class _HungPlugin:
        PLUGIN_INFO = {
            "id": "hot", "name": "Hot", "version": "1.0.0", "capabilities": [],
        }

        async def start(self, api):
            self.api = api

        async def stop(self):
            pass

        async def on_config_changed(self, new_config):
            await asyncio.Event().wait()  # never returns

    plugin = _HungPlugin()
    loader, _api = _make_loader_with(plugin, "hot")
    # Pre-fix this await never completes; bound it so a regression fails
    # instead of hanging the suite.
    handled = await asyncio.wait_for(loader.apply_config("hot", {"x": 1}), timeout=2)
    assert handled is False  # falls back to restart


@pytest.mark.asyncio
async def test_restart_or_apply_serializes_overlapping_updates(monkeypatch):
    """Two overlapping config updates must serialize as whole operations.

    Without the composite lock, update B lands in A's mid-restart not-running
    window: B returns "nothing to do" without applying, and the plugin comes
    back up on A's config while the project file holds B's — runtime and disk
    silently diverged (the cloud AI layer fans tool calls out as tasks, so
    this overlap is real, not theoretical)."""
    from openavc.core import plugin_loader as pl

    entered_stop = asyncio.Event()
    release_stop = asyncio.Event()

    class _SlowStopPlugin:
        """The injected running instance — its stop() blocks until released."""

        PLUGIN_INFO = {
            "id": "hot", "name": "Hot", "version": "1.0.0", "capabilities": [],
        }

        async def start(self, api):
            self.api = api

        async def stop(self):
            entered_stop.set()
            await release_stop.wait()

    class _FreshPlugin:
        """What the registry instantiates for each restart — stops instantly."""

        PLUGIN_INFO = {
            "id": "hot", "name": "Hot", "version": "1.0.0",
            "author": "t", "description": "t", "category": "utility",
            "license": "MIT", "capabilities": [],
        }

        async def start(self, api):
            self.api = api

        async def stop(self):
            pass

    blocking = _SlowStopPlugin()
    loader, _api = _make_loader_with(blocking, "hot")
    monkeypatch.setitem(pl._PLUGIN_CLASS_REGISTRY, "hot", _FreshPlugin)

    task_a = asyncio.create_task(loader.restart_or_apply("hot", {"cfg": "A"}))
    await asyncio.wait_for(entered_stop.wait(), timeout=2)
    task_b = asyncio.create_task(loader.restart_or_apply("hot", {"cfg": "B"}))
    # Give B every chance to (wrongly) run ahead while A is mid-stop.
    for _ in range(5):
        await asyncio.sleep(0)
    release_stop.set()
    result_a = await asyncio.wait_for(task_a, timeout=5)
    result_b = await asyncio.wait_for(task_b, timeout=5)

    # Pre-lock: B observed not-running and returned False without applying.
    assert (result_a, result_b) == ("restarted", "restarted")
    # The last-queued update wins and the runtime config matches it.
    assert loader.get_running_config("hot") == {"cfg": "B"}
    assert loader.is_running("hot")


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
