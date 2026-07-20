"""
Shared test fixtures for OpenAVC tests.
"""


import os
import tempfile

# Pin an isolated, empty data dir BEFORE anything imports `server.config`.
# config.py computes TLS_ENABLED / HTTP_PORT / TLS_PORT (and friends) as
# module-level constants at *import* time. A test module that does
# `from server import config` at top level gets config.py imported during
# collection -- earlier than the session-scoped _isolated_system_config
# fixture below can run -- which would bake the developer's real
# ./data/system.json into those constants for the whole process. When that
# file has TLS enabled (e.g. after cert bench work), a later test that builds
# a plain-http loopback URL then sees TLS on and gets an https URL instead,
# so the suite fails in a way that depends on collection order and the
# developer's machine. CI never saw it (no ./data), which is exactly what
# made it look random. Setting the env var here, before the server imports
# below, guarantees the first import of config.py reads an empty dir. Honor an
# explicit override if the developer set one.
os.environ.setdefault(
    "OPENAVC_DATA_DIR", tempfile.mkdtemp(prefix="openavc_test_import_")
)

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

    The module-level ``OPENAVC_DATA_DIR`` pin at the top of this file already
    covers config.py's *import-time* constants; this fixture re-pins to a
    tidy per-session tmp dir (auto-cleaned by pytest) and resets the config
    singleton so ``get_system_config()`` callers see the same isolation.
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


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """Clear per-IP rate-limiter buckets before each test.

    The limiter keeps module-level state (`_ip_buckets`, `_warn_dedup`) that
    persists across requests within the single in-process test run. The
    TestClient sends requests as the IP 'testclient', which the localhost
    exemption doesn't cover, so buckets filled by earlier tests spill over and
    return 429 to unrelated tests (notably test_assets.py). Clearing before
    each test gives every test a fresh budget. Tests that exercise the limiter
    itself (test_rate_limit.py) trip the limit from a clean bucket within their
    own test, so this stays compatible with them.
    """
    from server.middleware.rate_limit import _ip_buckets, _warn_dedup
    _ip_buckets.clear()
    _warn_dedup.clear()
    # Also clear the per-key macro/trigger "fire now" debounce (a separate
    # module-level window in api/_engine). Two tests firing the same macro or
    # trigger id within 2s would otherwise spill a 429 / throttle-error into
    # the second one.
    from server.api._engine import _test_endpoint_last_call
    _test_endpoint_last_call.clear()
    yield


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
