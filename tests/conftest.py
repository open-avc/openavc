"""
Shared test fixtures for OpenAVC tests.
"""


import os

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from tests.simulators.pjlink_simulator import PJLinkSimulator


@pytest.fixture(autouse=True, scope="session")
def _isolated_system_config(tmp_path_factory):
    """Pin OPENAVC_DATA_DIR to an empty temp dir for the test session.

    Without this, `get_system_config()` reads from the developer's local
    `./data/system.json` (which typically has a real programmer_password,
    cloud config, etc. set). That leaks the dev environment into the test
    suite -- routes protected by `require_programmer_auth` start returning
    401 to test requests that don't authenticate, and tests that exercise
    route logic (state CRUD, themes, scripts, assets) fail in ways that
    depend on the developer's machine. CI passes because the runner has
    no `data/` directory, so the singleton falls back to defaults.

    Pinning the data dir here makes the test environment deterministic:
    fresh defaults, no auth, no cloud, no kiosk. Tests that need to
    exercise specific config (e.g. test_api_auth.py) override values
    explicitly via monkeypatch.
    """
    from server.system_config import reset_system_config
    data_dir = tmp_path_factory.mktemp("openavc_test_data")
    prior = os.environ.get("OPENAVC_DATA_DIR")
    os.environ["OPENAVC_DATA_DIR"] = str(data_dir)
    reset_system_config()
    yield
    if prior is None:
        os.environ.pop("OPENAVC_DATA_DIR", None)
    else:
        os.environ["OPENAVC_DATA_DIR"] = prior
    reset_system_config()


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
