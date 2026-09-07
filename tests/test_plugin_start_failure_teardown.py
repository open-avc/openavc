"""A plugin whose start() is interrupted still gets its own teardown.

``start_plugin`` runs ``start()`` under a timeout. When it raises or is
cancelled, the loader used to unregister the macro actions and the script API
and await ``registry.cleanup`` -- every one of which is something the PLATFORM
owns -- and then return. It never called ``stop()``.

What that left behind is the half the platform cannot see: a child process, a
socket, a serial port, a hardware handle opened inside ``start()`` before it was
interrupted. The plugin's own teardown is precisely what was cancelled, so
nothing closes it and nothing holds a reference to it either. On an AV
controller that is a serial port or an encoder process held until the server is
restarted, with no surface in the IDE that shows it is held.

The failure path now calls ``stop()`` with the same guard ``_stop_plugin_locked``
uses -- bounded by ``PLUGIN_STOP_TIMEOUT``, every exception logged and swallowed
-- because a ``stop()`` that trips over an attribute ``start()`` never got to
assign is an ordinary outcome here rather than a special case.

Every plugin below is invented. This tests a platform capability.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core import plugin_loader as loader_module
from openavc.core.event_bus import EventBus
from openavc.core.plugin_loader import (
    PluginLoader,
    _PLUGIN_CLASS_REGISTRY,
    _REGISTRY_LOCK,
    register_plugin_class,
)
from openavc.core.state_store import StateStore


def _info(plugin_id: str, **extra) -> dict:
    return {
        "id": plugin_id,
        "name": plugin_id.replace("_", " ").title(),
        "version": "1.0.0",
        "author": "Test",
        "description": "A plugin that holds something outside the process.",
        "category": "utility",
        "license": "MIT",
        "capabilities": ["state_write"],
        **extra,
    }


class _Handle:
    """Stands in for whatever a plugin holds outside the process."""

    def __init__(self) -> None:
        self.open = True

    def close(self) -> None:
        self.open = False


HANDLES: dict[str, _Handle] = {}


class RaisingStartPlugin:
    """Opens the handle, then start() blows up."""

    PLUGIN_INFO = _info("raising_start")

    async def start(self, api):
        self.handle = HANDLES["raising_start"] = _Handle()
        raise ValueError("the device answered nonsense")

    async def stop(self):
        self.handle.close()


class HangingStartPlugin:
    """Opens the handle, then start() never returns."""

    PLUGIN_INFO = _info("hanging_start")

    async def start(self, api):
        self.handle = HANDLES["hanging_start"] = _Handle()
        await asyncio.sleep(3600)

    async def stop(self):
        self.handle.close()


class HalfBuiltPlugin:
    """Dies before assigning what its own stop() reads.

    The commonest shape there is: a teardown written for the finished plugin,
    reached in a state its author never pictured.
    """

    PLUGIN_INFO = _info("half_built")

    async def start(self, api):
        raise RuntimeError("could not reach the encoder")

    async def stop(self):
        self.handle.close()  # AttributeError: never assigned


class ExplodingConstructorPlugin:
    """Never becomes an instance at all, so there is nothing to tear down."""

    PLUGIN_INFO = _info("exploding_ctor")

    def __init__(self) -> None:
        raise RuntimeError("bad module-level assumption")

    async def start(self, api):  # pragma: no cover - never reached
        pass

    async def stop(self):  # pragma: no cover - never reached
        pass


class HangingStopPlugin:
    """start() fails and stop() then wedges."""

    PLUGIN_INFO = _info("hanging_stop")

    async def start(self, api):
        raise ValueError("no")

    async def stop(self):
        await asyncio.sleep(3600)


class CleanPlugin:
    """Starts fine. stop() belongs to disable and shutdown, not to this path."""

    PLUGIN_INFO = _info("clean")

    def __init__(self) -> None:
        self.stopped = False

    async def start(self, api):
        await api.state_set("status", "running")

    async def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def clean_registries():
    HANDLES.clear()
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
    yield
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
        _PLUGIN_CLASS_REGISTRY.update(saved)


@pytest.fixture
def loader():
    state = StateStore()
    events = EventBus()
    macros = MagicMock()
    macros.execute = AsyncMock()
    devices = MagicMock()
    devices.send_command = AsyncMock(return_value={"status": "ok"})
    return PluginLoader(state, events, macros, devices), state


async def test_a_raising_start_still_releases_what_it_opened(loader) -> None:
    plugins, _state = loader
    register_plugin_class(RaisingStartPlugin)

    assert await plugins.start_plugin("raising_start") is False
    assert HANDLES["raising_start"].open is False, (
        "start() opened a handle and then failed; nothing else can close it"
    )
    assert plugins.get_plugin_status("raising_start") == "error"


async def test_a_cancelled_start_still_releases_what_it_opened(loader, monkeypatch) -> None:
    """The timeout case, which is the one that CANCELS rather than raises."""
    plugins, _state = loader
    monkeypatch.setattr(loader_module, "PLUGIN_START_TIMEOUT", 0.05)
    register_plugin_class(HangingStartPlugin)

    assert await plugins.start_plugin("hanging_start") is False
    assert HANDLES["hanging_start"].open is False
    assert "timed out" in plugins._errors["hanging_start"]


async def test_a_teardown_that_trips_over_a_half_built_plugin_is_swallowed(loader) -> None:
    plugins, _state = loader
    register_plugin_class(HalfBuiltPlugin)

    # No AttributeError escapes, and the original start() failure is what is
    # reported -- not the teardown's.
    assert await plugins.start_plugin("half_built") is False
    assert plugins.get_plugin_status("half_built") == "error"
    assert "could not reach the encoder" in plugins._errors["half_built"]


async def test_a_constructor_that_raises_has_nothing_to_tear_down(loader, caplog) -> None:
    """There is no instance, so the teardown must not be ATTEMPTED.

    The catch-all below it would swallow the resulting AttributeError either
    way, so what the guard buys is not correctness -- it is that the log does
    not accuse a plugin of a broken ``stop()`` when it never got as far as
    existing. That is the sentence somebody reads while diagnosing, so it is
    what this pins.
    """
    plugins, _state = loader
    register_plugin_class(ExplodingConstructorPlugin)

    with caplog.at_level("ERROR", logger="openavc.core.plugin_loader"):
        assert await plugins.start_plugin("exploding_ctor") is False

    assert plugins.get_plugin_status("exploding_ctor") == "error"
    assert not any("stop() raised" in r.message for r in caplog.records), (
        "the log blamed stop() on a plugin whose constructor never returned"
    )


async def test_a_wedged_teardown_cannot_wedge_the_failure_path(loader, monkeypatch) -> None:
    plugins, _state = loader
    monkeypatch.setattr(loader_module, "PLUGIN_STOP_TIMEOUT", 0.05)
    register_plugin_class(HangingStopPlugin)

    assert await plugins.start_plugin("hanging_stop") is False
    assert plugins.get_plugin_status("hanging_stop") == "error"


async def test_the_platform_cleanup_still_runs_after_the_teardown(loader) -> None:
    """stop() is added to the failure path, it does not replace what was there."""
    plugins, state = loader
    register_plugin_class(RaisingStartPlugin)

    await plugins.start_plugin("raising_start")

    plugins._macros.unregister_plugin_actions.assert_called_with("raising_start")
    assert state.get("plugin.raising_start.status") is None
    assert "raising_start" not in plugins._instances


async def test_a_start_that_worked_is_not_torn_down(loader) -> None:
    """The negative control. stop() belongs to disable and shutdown; calling it
    on a plugin that started correctly would kill every working plugin."""
    plugins, _state = loader
    register_plugin_class(CleanPlugin)

    assert await plugins.start_plugin("clean") is True
    assert plugins._instances["clean"].stopped is False
