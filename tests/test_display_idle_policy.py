"""The panel display policy the panel-drawing client reads.

The server holds this policy and never acts on it: whatever is showing the
panel does the dimming (the appliance shell today). So what is worth pinning
is the contract that client depends on, and the judgements the server keeps
rather than hands over.

  - It reads from the PROJECT, so a customer sets it once and deploys one
    template to a hundred panels.
  - The numbers are bounded where they are READ. A project arrives from a
    template, an import or a backup restore, so a check at one write door is
    not the one that holds -- and a zero timeout would dim a panel nobody had
    stopped touching.
  - `hold` is resolved here, not in the client. Whether a state value counts
    as "on" is a platform rule (`condition_eval`); a second implementation of
    it in another language is a rule that can disagree with itself.
  - `blackout` is its own flag on the wire even though it is spelled as a
    level of 0 in the project, so the client never has to know 0 is a mode.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import ProjectConfig
from openavc.core.state_store import StateStore
from openavc.main import app


def _project(**display) -> ProjectConfig:
    return ProjectConfig(
        project={"id": "p1", "name": "t"},
        settings={"display": display} if display else {},
    )


@pytest.fixture
def client(tmp_path):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    engine = MagicMock()
    engine.state = state
    engine.events = events
    engine.project = _project()
    engine.project_dir = Path(tmp_path)
    rest.set_engine(engine)
    ws.set_engine(engine)
    yield TestClient(app), engine, state
    rest.set_engine(None)
    ws.set_engine(None)


def _policy(c):
    r = c.get("/api/system/display-idle")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_dim_arrives_on_so_a_panel_does_not_need_a_visit(client):
    c, _e, _s = client
    body = _policy(c)
    assert body["enabled"] is True
    assert body["timeout_seconds"] == 300
    assert body["level_percent"] == 20
    assert body["blackout"] is False
    assert body["brightness_percent"] is None


def test_the_project_is_what_the_panel_reads(client):
    c, engine, _s = client
    engine.project = _project(
        idle_dim_timeout_seconds=600,
        idle_dim_level_percent=15,
        idle_dim_wake_passes_touch=True,
        brightness_percent=65,
    )
    body = _policy(c)
    assert body["timeout_seconds"] == 600
    assert body["level_percent"] == 15
    assert body["wake_passes_touch"] is True
    assert body["brightness_percent"] == 65


def test_a_level_of_zero_is_blackout_and_never_a_brightness_of_zero(client):
    """0 is a MODE, not a level. The client is handed a real percentage it can
    multiply by plus a flag, so it never has to know that."""
    c, engine, _s = client
    engine.project = _project(idle_dim_level_percent=0)
    body = _policy(c)
    assert body["blackout"] is True
    assert body["level_percent"] >= 1, "a 0 multiplier would be a black backlight, not a floor"


@pytest.mark.parametrize("stored,expected", [(0, 30), (5, 30), (99999, 7200), (-1, 30)])
def test_a_timeout_out_of_range_is_clamped_at_the_read(client, stored, expected):
    c, engine, _s = client
    engine.project = _project(idle_dim_timeout_seconds=stored)
    assert _policy(c)["timeout_seconds"] == expected


@pytest.mark.parametrize("stored,expected", [(100, 90), (-40, 1)])
def test_a_dim_level_out_of_range_is_clamped_at_the_read(client, stored, expected):
    c, engine, _s = client
    engine.project = _project(idle_dim_level_percent=stored)
    assert _policy(c)["level_percent"] == expected


@pytest.mark.parametrize("junk", ["x", None, [], {}])
def test_a_non_number_is_refused_by_the_model_not_papered_over(junk):
    """The type is enforced where the project is parsed, so the endpoint's own
    clamps only ever see numbers. A project carrying junk fails to load and
    the recovery project takes over -- which is louder, and better, than a
    panel quietly running on a default nobody chose."""
    from openavc.core.project_loader import DisplaySettings

    with pytest.raises(Exception):
        DisplaySettings(idle_dim_timeout_seconds=junk)


def test_brightness_out_of_range_is_clamped_but_unset_stays_unset(client):
    """None must survive as None: it means "this panel keeps its own setting",
    and collapsing it to 100 would brighten every panel on update."""
    c, engine, _s = client
    engine.project = _project(brightness_percent=500)
    assert _policy(c)["brightness_percent"] == 100
    engine.project = _project()
    assert _policy(c)["brightness_percent"] is None


@pytest.mark.parametrize("authored", [1, 2, 5, 9, 0, -30])
def test_a_brightness_too_low_to_read_is_refused(client, authored):
    """The stuck-panel case, and the reason it is checked on the server.

    A panel below this cannot be read, so the control that would undo it is on
    somebody's laptop rather than in the room. `ProjectSettings` only admits
    settings a person standing in front of the panel can recover from, and a
    project carrying 1% arrives from a cloud template on a hundred panels at
    once -- so the Programmer's own slider floor is not the check that holds.
    """
    c, engine, _s = client
    engine.project = _project(brightness_percent=authored)
    assert _policy(c)["brightness_percent"] == 10


def test_hold_follows_the_named_state_key(client):
    c, engine, state = client
    engine.project = _project(idle_dim_hold_state_key="var.system_on")
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
    """The same answer `condition_eval`'s `truthy` gives, so a bound LED and
    the dim hold cannot disagree about whether the room is in use."""
    c, engine, state = client
    engine.project = _project(idle_dim_hold_state_key="device.dsp.power")
    state.set("device.dsp.power", value)
    assert _policy(c)["hold"] is held


def test_no_hold_key_means_the_timer_alone_decides(client):
    c, engine, state = client
    state.set("var.anything", True)
    assert _policy(c)["hold"] is False


def test_a_project_that_will_not_load_still_answers(client):
    """A panel with no usable project must still be told what to do, or the
    shell is left guessing at the one moment somebody is trying to fix it."""
    c, engine, _s = client
    engine.project = None
    body = _policy(c)
    assert body["enabled"] is True
    assert body["timeout_seconds"] == 300


def test_the_capability_flag_gates_the_settings_ui(client, monkeypatch):
    import openavc.api.routes.system as system_routes

    c, _e, _s = client
    monkeypatch.setattr(system_routes, "_panel_dim_available", lambda: True)
    assert c.get("/api/system/version").json()["panel_dim_available"] is True
    monkeypatch.setattr(system_routes, "_panel_dim_available", lambda: False)
    assert c.get("/api/system/version").json()["panel_dim_available"] is False
