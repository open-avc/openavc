"""The idle-dim policy the panel-drawing client reads.

The server holds this policy and never acts on it: whatever is showing the
panel on the screen does the dimming (the appliance shell today). So what is
worth pinning here is the *contract* that client depends on, and the two
judgements the server deliberately keeps rather than handing over.

  - The numbers are bounded where they are READ. A hand-edited system.json
    reaches no endpoint, so a check at the write door is not the one that
    holds -- and a zero timeout would dim a panel nobody had stopped touching.
  - ``hold`` is resolved here, not in the client. Whether a state value counts
    as "on" is a platform rule (``condition_eval``); a second implementation
    of it in another language is a rule that can disagree with itself.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.main import app
from openavc.system_config import get_system_config, reset_system_config


@pytest.fixture
def client(tmp_path):
    reset_system_config()
    cfg = get_system_config()
    cfg._data_dir = tmp_path
    cfg._file_path = tmp_path / "system.json"
    cfg.load()

    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    engine = MagicMock()
    engine.state = state
    engine.events = events
    engine.project_dir = Path(tmp_path)
    rest.set_engine(engine)
    ws.set_engine(engine)

    yield TestClient(app), cfg, state

    rest.set_engine(None)
    ws.set_engine(None)
    reset_system_config()


def _policy(c):
    r = c.get("/api/system/display-idle")
    assert r.status_code == 200, r.text
    return r.json()


def test_defaults_are_off_so_an_update_changes_no_fielded_panel(client):
    c, _cfg, _state = client
    body = _policy(c)
    assert body["enabled"] is False
    assert body["hold"] is False
    assert body["hold_state_key"] == ""


def test_the_configured_policy_is_what_comes_back(client):
    c, cfg, _state = client
    cfg.set("display", "idle_dim_enabled", True)
    cfg.set("display", "idle_dim_timeout_seconds", 600)
    cfg.set("display", "idle_dim_level_percent", 15)
    cfg.set("display", "idle_dim_wake_passes_touch", True)

    body = _policy(c)
    assert body["enabled"] is True
    assert body["timeout_seconds"] == 600
    assert body["level_percent"] == 15
    assert body["wake_passes_touch"] is True


@pytest.mark.parametrize(
    "stored,expected",
    [(0, 30), (5, 30), (99999, 7200), (-1, 30), ("nonsense", 300), (None, 300)],
)
def test_a_timeout_out_of_range_is_clamped_at_the_read(client, stored, expected):
    c, cfg, _state = client
    cfg.set("display", "idle_dim_timeout_seconds", stored)
    assert _policy(c)["timeout_seconds"] == expected


@pytest.mark.parametrize(
    "stored,expected",
    [(0, 5), (3, 5), (100, 90), (-40, 5), ("nonsense", 20), (None, 20)],
)
def test_a_dim_level_out_of_range_is_clamped_at_the_read(client, stored, expected):
    c, cfg, _state = client
    cfg.set("display", "idle_dim_level_percent", stored)
    assert _policy(c)["level_percent"] == expected


def test_a_dim_level_of_zero_never_reaches_the_client(client):
    """A black panel is the one outcome this feature must never produce."""
    c, cfg, _state = client
    cfg.set("display", "idle_dim_level_percent", 0)
    assert _policy(c)["level_percent"] >= 5


def test_hold_follows_the_named_state_key(client):
    c, cfg, state = client
    cfg.set("display", "idle_dim_hold_state_key", "var.system_on")

    assert _policy(c)["hold"] is False, "unset key is not a hold"

    state.set("var.system_on", True)
    assert _policy(c)["hold"] is True

    state.set("var.system_on", False)
    assert _policy(c)["hold"] is False


@pytest.mark.parametrize(
    "value,held",
    [(True, True), (1, True), ("on", True), (0.5, True),
     (False, False), (0, False), ("", False), (None, False)],
)
def test_hold_uses_the_platform_truthiness_rule(client, value, held):
    """Same answer `condition_eval`'s `truthy` operator gives, so a bound LED
    and the dim hold cannot disagree about whether the room is in use."""
    c, cfg, state = client
    cfg.set("display", "idle_dim_hold_state_key", "device.dsp.power")
    state.set("device.dsp.power", value)
    assert _policy(c)["hold"] is held


def test_no_hold_key_means_the_timer_alone_decides(client):
    c, cfg, state = client
    state.set("var.anything", True)
    cfg.set("display", "idle_dim_hold_state_key", "")
    assert _policy(c)["hold"] is False


def test_the_capability_flag_gates_the_settings_ui(client, monkeypatch):
    """The Programmer only draws the section where a screen can actually be
    dimmed, and this flag is the whole gate."""
    import openavc.api.routes.system as system_routes

    c, _cfg, _state = client
    monkeypatch.setattr(system_routes, "_panel_dim_available", lambda: True)
    assert c.get("/api/system/version").json()["panel_dim_available"] is True

    monkeypatch.setattr(system_routes, "_panel_dim_available", lambda: False)
    assert c.get("/api/system/version").json()["panel_dim_available"] is False
