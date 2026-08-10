"""
Shared test fixtures for OpenAVC tests.
"""


import os
import tempfile

# Pin an isolated, empty data dir BEFORE anything imports `openavc.config`.
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

# Run the whole suite with driver-contract violations promoted from a warning
# to a raise. A driver that writes a state variable it never declared in
# DRIVER_INFO["state_variables"] produces live state nothing can be built
# against -- no type, no binding picker entry -- and at runtime the platform
# only warns, because taking a working device offline over an author's
# omission would punish the end user for it. A test suite is exactly where
# that trade-off flips: nobody's room is on the line, and the author is
# iterating. This is the same env var a driver author sets in their own
# harness, so what fails here fails there. Honor an explicit override
# (OPENAVC_STRICT_DRIVER_STATE=0) so the warn path can still be exercised.
os.environ.setdefault("OPENAVC_STRICT_DRIVER_STATE", "1")

import pytest

from openavc.drivers.driver_loader import load_builtin_drivers
from openavc.drivers.registry import register_driver
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from tests.drivers.acme_display import AcmeDisplayDriver
from tests.drivers.acme_power_relay import AcmePowerRelayDriver
from tests.simulators.acme_display_simulator import AcmeDisplaySimulator

# Register the built-in drivers (the generic tcp / serial / http devices among
# them) that a test reaching the device layer expects to find. The server does
# this once at engine startup; plenty of tests never start an engine, and the
# ones that do would still be collected against an empty registry, so the whole
# suite gets it here at conftest import.
load_builtin_drivers()

# Install the invented test drivers the moment this file is imported, before
# any test module is collected. Tests that reach the device layer by driver id
# (DeviceManager, Engine, the REST API) need a driver that is genuinely there,
# and the data dir pinned above is deliberately empty — so nothing from the
# driver library ever is, in any environment. Registering here rather than in
# a fixture means a test module can rely on it at import time too. The driver
# loader only ever adds to this registry, so a later Engine start leaves these
# in place.
register_driver(AcmeDisplayDriver)
register_driver(AcmePowerRelayDriver)


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
    from openavc.system_config import reset_system_config
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
    from openavc.api import rest, ws, plugins, themes, assets
    rest.set_engine(None)
    ws.set_engine(None)
    ws._log_subscriptions.clear()
    plugins.set_engine(None)
    themes.set_engine(None)
    assets.set_engine(None)
    # Reset discovery engine reference
    try:
        from openavc.api import discovery
        discovery._app_engine = None
    except (ImportError, AttributeError):
        pass
    # Reset plugin class registry to prevent test cross-contamination
    from openavc.core.plugin_loader import _PLUGIN_CLASS_REGISTRY, _REGISTRY_LOCK
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
    from openavc.middleware.rate_limit import _ip_buckets, _warn_dedup
    _ip_buckets.clear()
    _warn_dedup.clear()
    # Also clear the per-key macro/trigger "fire now" debounce (a separate
    # module-level window in api/_engine). Two tests firing the same macro or
    # trigger id within 2s would otherwise spill a 429 / throttle-error into
    # the second one.
    from openavc.api._engine import _test_endpoint_last_call
    _test_endpoint_last_call.clear()
    yield


@pytest.fixture
def isolated_auth_config():
    """Snapshot and restore the auth credential around a test.

    `SystemConfig` keeps two layers: `_data`, the effective runtime view, and
    `_file_data`, the pre-env layer that `save()` serializes to system.json.
    `set()` writes BOTH. So a test that puts an admin password on the config
    and then restores only `_data` still leaves it sitting in the persisted
    layer, and the next `save()` writes it to the session data dir. Nothing
    looks wrong yet: the live singleton is clean, so the file that follows
    ends green.

    The bill arrives later. Any test that calls `reset_system_config()` --
    the simulator port tests do, in the middle of the alphabet -- makes the
    next `get_system_config()` rebuild the singleton off that file, and the
    password is back for the rest of the session. From there every route
    guarded by `require_programmer_auth` answers an unauthenticated request
    with 401, so tests in three unrelated files that assert 403 or 400 fail
    with `assert 401 == 403` and pass again the moment you run them alone.

    Restoring both layers is the fix; putting the file back byte for byte
    covers the tests that persist through `claim_instance()` or the config
    PATCH route rather than through `set()`.
    """
    from openavc.api import auth
    from openavc.system_config import get_system_config

    cfg = get_system_config()
    saved_data = dict(cfg._data.get("auth", {}))
    saved_file_data = dict(cfg._file_data.get("auth", {}))
    saved_bytes = cfg.file_path.read_bytes() if cfg.file_path.exists() else None
    auth._deployment_is_dev.cache_clear()
    yield cfg
    cfg._data["auth"] = saved_data
    cfg._file_data["auth"] = saved_file_data
    auth._deployment_is_dev.cache_clear()
    if saved_bytes is None:
        cfg.file_path.unlink(missing_ok=True)
    else:
        cfg.file_path.write_bytes(saved_bytes)


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
async def acme_sim():
    """Running Acme Display simulator on an ephemeral port. Auto-cleaned up."""
    sim = AcmeDisplaySimulator(port=0, warmup_time=0.3, cooldown_time=0.2)
    await sim.start()
    yield sim
    await sim.stop()
