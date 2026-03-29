"""
Shared test fixtures for OpenAVC tests.
"""


import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from tests.simulators.pjlink_simulator import PJLinkSimulator


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset all global module-level state after each test to prevent leakage."""
    yield
    # Reset API module engine references
    from server.api import rest, ws, plugins, themes, assets
    rest.set_engine(None)
    ws.set_engine(None)
    ws._log_subscriptions.clear()
    plugins.set_engine(None)
    themes.set_engine(None)
    assets.set_engine(None)
    # Reset discovery engine reference
    try:
        from server.api import discovery
        discovery._app_engine = None
    except (ImportError, AttributeError):
        pass
    # Reset plugin class registry to prevent test cross-contamination
    from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY, _REGISTRY_LOCK
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()


@pytest.fixture
def state():
    """Fresh StateStore instance."""
    return StateStore()


@pytest.fixture
def events():
    """Fresh EventBus instance."""
    return EventBus()


@pytest.fixture
def wired(state, events):
    """StateStore and EventBus wired together."""
    state.set_event_bus(events)
    return state, events


@pytest.fixture
async def pjlink_sim():
    """Running PJLink simulator on a random-ish test port. Auto-cleaned up."""
    sim = PJLinkSimulator(port=0, warmup_time=0.3, cooldown_time=0.2)
    await sim.start()
    yield sim
    await sim.stop()
