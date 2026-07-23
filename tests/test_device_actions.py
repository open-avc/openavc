"""Tests for the Quick Action strip platform feature.

Exercises the generic action resolver and validator (server/drivers/actions.py)
and their wiring into the YAML loader and ConfigurableDriver — using an invented
device (Acme), never a real product. The mechanism is what's under test, not any
specific driver's action set.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from server.api import rest, ws
from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.actions import resolve_device_actions, validate_actions
from server.drivers.base import BaseDriver, CommandParamError
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import validate_driver_definition
from server.main import app


def _driver_info_with(**extra):
    """A minimal DRIVER_INFO carrying a couple of commands plus extra keys."""
    base = {
        "id": "acme_widget",
        "commands": {
            "power_on": {"label": "Power On", "params": {}},
            "power_off": {"label": "Power Off", "params": {}},
            "set_input": {
                "label": "Set Input",
                "params": {"input": {"type": "integer", "required": True}},
            },
        },
    }
    base.update(extra)
    return base


# --- resolve_device_actions ------------------------------------------------


def test_resolve_quick_actions_sugar_expands_to_command_actions():
    info = _driver_info_with(quick_actions=["power_on", "power_off"])
    actions = resolve_device_actions(info)
    assert [a["id"] for a in actions] == ["power_on", "power_off"]
    assert all(a["kind"] == "command" for a in actions)
    # Sugar inherits the command's label + params and targets the same command.
    assert actions[0]["label"] == "Power On"
    assert actions[0]["command"] == "power_on"
    assert actions[0]["availability"] == "online"
    assert actions[0]["icon"] is None


def test_resolve_quick_action_for_unknown_command_is_skipped():
    info = _driver_info_with(quick_actions=["power_on", "does_not_exist"])
    actions = resolve_device_actions(info)
    assert [a["id"] for a in actions] == ["power_on"]


def test_resolve_explicit_command_action_inherits_command_label_and_params():
    info = _driver_info_with(actions=[{"id": "set_input", "kind": "command", "icon": "tv"}])
    actions = resolve_device_actions(info)
    assert len(actions) == 1
    a = actions[0]
    assert a["label"] == "Set Input"  # inherited
    assert a["icon"] == "tv"
    assert a["params"] == {"input": {"type": "integer", "required": True}}


def test_resolve_action_command_field_overrides_id():
    info = _driver_info_with(
        actions=[{"id": "enroll", "kind": "command", "command": "power_on"}]
    )
    actions = resolve_device_actions(info)
    assert actions[0]["id"] == "enroll"
    assert actions[0]["command"] == "power_on"
    assert actions[0]["label"] == "Power On"


def test_resolve_explicit_actions_come_before_quick_actions():
    info = _driver_info_with(
        actions=[{"id": "set_input", "kind": "command"}],
        quick_actions=["power_on"],
    )
    actions = resolve_device_actions(info)
    assert [a["id"] for a in actions] == ["set_input", "power_on"]


def test_resolve_explicit_action_wins_over_quick_action_collision():
    info = _driver_info_with(
        actions=[{"id": "power_on", "kind": "command", "icon": "power", "confirm": True}],
        quick_actions=["power_on"],
    )
    actions = resolve_device_actions(info)
    assert len(actions) == 1  # de-duped by id
    assert actions[0]["icon"] == "power"
    assert actions[0]["confirm"] is True


def test_resolve_setup_action_keeps_params_and_availability():
    info = _driver_info_with(
        actions=[{
            "id": "enable_remote",
            "kind": "setup",
            "label": "Enable Remote",
            "availability": "offline",
            "params": {"password": {"type": "password"}},
        }]
    )
    actions = resolve_device_actions(info)
    a = actions[0]
    assert a["kind"] == "setup"
    assert a["availability"] == "offline"
    assert a["params"] == {"password": {"type": "password"}}
    assert "command" not in a


def test_resolve_passes_through_visible_when():
    vw = {"any": [{"key": "device.$id.offline_reason", "operator": "eq", "value": "connection_refused"}]}
    info = _driver_info_with(actions=[{"id": "x", "kind": "setup", "visible_when": vw}])
    actions = resolve_device_actions(info)
    assert actions[0]["visible_when"] == vw


def test_resolve_is_defensive_against_malformed_entries():
    info = _driver_info_with(
        actions=[
            "not a dict",
            {"no_id": True},
            {"id": "ok", "kind": "command", "command": "power_on"},
            {"id": "bad_kind", "kind": "nonsense"},
        ],
        quick_actions=["power_on", 123, ""],
    )
    actions = resolve_device_actions(info)
    # Only the well-formed explicit action and the valid sugar entry survive.
    assert [a["id"] for a in actions] == ["ok", "power_on"]


def test_resolve_no_actions_returns_empty():
    assert resolve_device_actions(_driver_info_with()) == []


# --- validate_actions ------------------------------------------------------


def test_validate_accepts_well_formed_blocks():
    driver_def = _driver_info_with(
        quick_actions=["power_on"],
        actions=[
            {"id": "set_input", "kind": "command", "icon": "tv"},
            {
                "id": "enable_remote",
                "kind": "setup",
                "availability": "offline",
                "confirm": "Sure?",
                "visible_when": {"key": "device.$id.connected", "operator": "falsy"},
            },
        ],
    )
    assert validate_actions(driver_def) == []


def test_validate_rejects_quick_action_for_unknown_command():
    errors = validate_actions(_driver_info_with(quick_actions=["nope"]))
    assert any("not a declared command" in e for e in errors)


def test_validate_rejects_command_action_with_dangling_command():
    errors = validate_actions(
        _driver_info_with(actions=[{"id": "x", "kind": "command", "command": "ghost"}])
    )
    assert any("'ghost' is not a declared command" in e for e in errors)


def test_validate_rejects_duplicate_ids_and_bad_kind():
    errors = validate_actions(_driver_info_with(actions=[
        {"id": "power_on", "kind": "command"},
        {"id": "power_on", "kind": "command"},
        {"id": "weird", "kind": "bogus"},
    ]))
    assert any("duplicate action id" in e for e in errors)
    assert any("unknown kind" in e for e in errors)


def test_validate_rejects_bad_availability_and_visible_when():
    errors = validate_actions(_driver_info_with(actions=[
        {"id": "a", "kind": "setup", "availability": "whenever"},
        {"id": "b", "kind": "setup", "visible_when": {"operator": "eq", "value": 1}},
        {"id": "c", "kind": "setup", "visible_when": {"key": "k", "operator": "??"}},
    ]))
    assert any("availability" in e for e in errors)
    assert any("missing 'key'" in e for e in errors)
    assert any("unknown operator" in e for e in errors)


def test_validate_rejects_non_list_blocks():
    assert any("must be a list" in e for e in validate_actions({"quick_actions": "power_on"}))
    assert any("must be a list" in e for e in validate_actions({"actions": {}}))


# --- loader + ConfigurableDriver integration -------------------------------


def _yaml_driver(**extra):
    base = {
        "id": "acme_yaml",
        "name": "Acme YAML Widget",
        "transport": "tcp",
        "commands": {"power_on": {"label": "Power On", "send": "PWR ON\\r"}},
    }
    base.update(extra)
    return base


def test_loader_validates_actions_block():
    bad = _yaml_driver(quick_actions=["ghost"])
    errors = validate_driver_definition(bad)
    assert any("not a declared command" in e for e in errors)

    good = _yaml_driver(quick_actions=["power_on"])
    assert validate_driver_definition(good) == []


def test_loader_rejects_setup_action_on_yaml_driver():
    # A YAML driver can't implement run_setup_action, so kind:"setup" is invalid.
    bad = _yaml_driver(actions=[{"id": "provision", "kind": "setup"}])
    errors = validate_driver_definition(bad)
    assert any("requires a Python driver" in e for e in errors)


def test_configurable_driver_carries_actions_into_driver_info():
    driver_def = _yaml_driver(
        actions=[{"id": "power_on", "kind": "command", "icon": "power"}],
        quick_actions=["power_on"],
    )
    cls = create_configurable_driver_class(driver_def)
    assert cls.DRIVER_INFO.get("actions") == driver_def["actions"]
    assert cls.DRIVER_INFO.get("quick_actions") == ["power_on"]
    # And they resolve (explicit wins over the sugar collision).
    resolved = resolve_device_actions(cls.DRIVER_INFO)
    assert [a["id"] for a in resolved] == ["power_on"]
    assert resolved[0]["icon"] == "power"


def test_configurable_driver_carries_web_ui_into_driver_info():
    """web_ui is a published .avcdriver field — the YAML->DRIVER_INFO build
    must carry it so YAML drivers get the auto-added Open Web UI link too."""
    cls = create_configurable_driver_class(_yaml_driver(web_ui=True))
    assert cls.DRIVER_INFO.get("web_ui") is True
    assert any(
        a["id"] == "open_web_ui" for a in resolve_device_actions(cls.DRIVER_INFO)
    )

    cls = create_configurable_driver_class(_yaml_driver(web_ui="http://{host}:8080"))
    link = next(
        a for a in resolve_device_actions(cls.DRIVER_INFO) if a["kind"] == "link"
    )
    assert link["url"] == "http://{host}:8080"


def test_get_device_info_includes_resolved_actions():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)

    cls = create_configurable_driver_class(
        _yaml_driver(actions=[{"id": "power_on", "kind": "command", "icon": "power"}])
    )
    driver = cls("dev1", {"host": "h", "port": 1}, state, events)
    # White-box inject a live driver so get_device_info reads the active path
    # without opening a real transport.
    dm._devices["dev1"] = driver
    dm._device_configs["dev1"] = {"name": "Dev 1", "driver": "acme_yaml"}

    info = dm.get_device_info("dev1")
    assert [a["id"] for a in info["actions"]] == ["power_on"]
    assert info["actions"][0]["icon"] == "power"


def test_get_device_info_substitutes_link_url_from_connection_config():
    """The served Open Web UI link substitutes {host} from the driver's
    connection-merged config. Regression: get_device_info used to pass the
    project-level device entry (which nests the connection under "config"
    and has no top-level host), so the button opened a literal "{host}"."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)

    cls = create_configurable_driver_class(_yaml_driver(web_ui=True))
    # The driver gets the connection-merged config, exactly as add_device
    # hands it over; the device entry keeps it nested under "config".
    driver = cls("dev1", {"host": "192.0.2.10", "port": 443}, state, events)
    dm._devices["dev1"] = driver
    dm._device_configs["dev1"] = {
        "id": "dev1",
        "name": "Dev 1",
        "driver": "acme_yaml",
        "config": {"host": "192.0.2.10", "port": 443},
    }

    info = dm.get_device_info("dev1")
    link = next(a for a in info["actions"] if a["kind"] == "link")
    assert link["url"] == "https://192.0.2.10"


def _dm_with_device(web_ui_extra):
    """A DeviceManager holding one live YAML device, driver built from
    web_ui_extra (e.g. {} for auto-detect, {"web_ui": True} for forced-on)."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    cls = create_configurable_driver_class(_yaml_driver(**web_ui_extra))
    driver = cls("dev1", {"host": "192.0.2.20"}, state, events)
    dm._devices["dev1"] = driver
    dm._device_configs["dev1"] = {
        "id": "dev1", "name": "Dev 1", "driver": "acme_yaml",
        "config": {"host": "192.0.2.20"},
    }
    return dm


def test_get_device_info_surfaces_detected_web_ui_url():
    """A URL detected by the probe/discovery surfaces as the Open Web UI link."""
    dm = _dm_with_device({})  # auto-detect (no web_ui declared)
    dm._detected_web_ui_urls["dev1"] = "http://192.0.2.20"
    info = dm.get_device_info("dev1")
    link = next(a for a in info["actions"] if a["kind"] == "link")
    assert link["url"] == "http://192.0.2.20"


def test_seed_web_ui_url_records_in_auto_mode():
    dm = _dm_with_device({})
    dm.seed_web_ui_url("dev1", "http://192.0.2.20")
    assert dm._detected_web_ui_urls["dev1"] == "http://192.0.2.20"


def test_seed_web_ui_url_noops_when_driver_forces_web_ui():
    """A driver that set web_ui explicitly owns the URL — no seeding over it."""
    dm = _dm_with_device({"web_ui": True})
    dm.seed_web_ui_url("dev1", "http://192.0.2.20")
    assert "dev1" not in dm._detected_web_ui_urls


def test_seed_web_ui_url_first_writer_wins():
    dm = _dm_with_device({})
    dm._detected_web_ui_urls["dev1"] = "http://first"
    dm.seed_web_ui_url("dev1", "http://second")
    assert dm._detected_web_ui_urls["dev1"] == "http://first"


# --- Invoke endpoint -------------------------------------------------------


class _ActionDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_act",
        "name": "Acme Action Widget",
        "transport": "tcp",
        "state_variables": {},
        "commands": {"power_on": {"label": "Power On", "params": {}}},
        "actions": [
            {"id": "power_on", "kind": "command", "icon": "power"},
            {"id": "enable_remote", "kind": "setup", "availability": "offline"},
        ],
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return True


@pytest.fixture
def actions_client():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = _ActionDriver("dev1", {}, state, events)
    driver.set_state("connected", True)

    engine = MagicMock()
    engine.state = state
    engine.events = events
    engine._running = True
    engine._ws_clients = []
    engine.devices = MagicMock()
    engine.devices.get_driver = MagicMock(return_value=driver)
    engine.devices.send_command = AsyncMock(return_value=True)
    # Route-level wiring test: stub the runner; its internals are covered in
    # test_setup_actions.py with a real SetupActionRunner.
    engine.setup_actions = MagicMock()
    engine.setup_actions.start = AsyncMock(
        return_value={"run_id": "run123", "status": "started", "action_id": "enable_remote"}
    )

    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield TestClient(app), engine
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


def test_invoke_command_action_routes_to_send_command(actions_client):
    c, engine = actions_client
    resp = c.post("/api/devices/dev1/actions/power_on", json={"params": {}})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    engine.devices.send_command.assert_awaited_once_with("dev1", "power_on", {})


def test_invoke_unknown_action_returns_404(actions_client):
    c, _engine = actions_client
    resp = c.post("/api/devices/dev1/actions/nope", json={"params": {}})
    assert resp.status_code == 404


def test_invoke_setup_action_starts_run(actions_client):
    c, engine = actions_client
    resp = c.post("/api/devices/dev1/actions/enable_remote", json={"params": {"x": 1}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "run123"
    assert body["status"] == "started"
    # Routed to the setup runner, not the command path.
    engine.devices.send_command.assert_not_awaited()
    engine.setup_actions.start.assert_awaited_once()
    args = engine.setup_actions.start.await_args.args
    assert args[0] == "dev1"
    assert args[1]["id"] == "enable_remote"  # the resolved action
    assert args[2] == {"x": 1}  # params


def test_invoke_action_on_device_without_driver_returns_404(actions_client):
    c, engine = actions_client
    engine.devices.get_driver = MagicMock(return_value=None)
    resp = c.post("/api/devices/dev1/actions/power_on", json={"params": {}})
    assert resp.status_code == 404


def test_invoke_action_bad_param_returns_400_not_404(actions_client):
    """CommandParamError is a ValueError subclass — it must surface as an
    actionable 400 (like the plain command route), not 'Device not found'."""
    c, engine = actions_client
    engine.devices.send_command = AsyncMock(
        side_effect=CommandParamError("power_on: 'level' must be between 0 and 100")
    )
    resp = c.post("/api/devices/dev1/actions/power_on", json={"params": {"level": 101}})
    assert resp.status_code == 400
    assert "level" in resp.text
    assert "not found" not in resp.text.lower()


# --- kind: link / Open Web UI ---------------------------------------------


def test_web_ui_flag_auto_adds_open_web_ui_link():
    """A driver that declares web_ui gets an Open Web UI link with no action."""
    actions = resolve_device_actions(_driver_info_with(web_ui=True))
    link = [a for a in actions if a["kind"] == "link"]
    assert len(link) == 1
    a = link[0]
    assert a["id"] == "open_web_ui"
    assert a["label"] == "Open Web UI"
    assert a["availability"] == "always"
    assert a["url"] == "https://{host}"  # unsubstituted without config


def test_web_ui_link_url_host_substituted_from_config():
    actions = resolve_device_actions(
        _driver_info_with(web_ui=True), {"host": "10.0.0.5", "port": 443}
    )
    link = next(a for a in actions if a["kind"] == "link")
    assert link["url"] == "https://10.0.0.5"


def test_web_ui_string_is_used_as_url_template():
    actions = resolve_device_actions(
        _driver_info_with(web_ui="http://{host}:8080/admin"),
        {"host": "10.0.0.5"},
    )
    link = next(a for a in actions if a["kind"] == "link")
    assert link["url"] == "http://10.0.0.5:8080/admin"


def test_explicit_link_action_suppresses_web_ui_auto_add():
    info = _driver_info_with(
        web_ui=True,
        actions=[{"id": "web", "kind": "link", "label": "Console",
                  "url": "https://{host}:9000"}],
    )
    actions = resolve_device_actions(info, {"host": "1.2.3.4"})
    links = [a for a in actions if a["kind"] == "link"]
    assert len(links) == 1
    assert links[0]["id"] == "web"
    assert links[0]["url"] == "https://1.2.3.4:9000"


def test_link_action_missing_url_defaults_to_https_host():
    info = _driver_info_with(actions=[{"id": "web", "kind": "link"}])
    link = next(a for a in resolve_device_actions(info) if a["kind"] == "link")
    assert link["url"] == "https://{host}"
    assert link["availability"] == "always"  # links default to always-visible


def test_missing_placeholder_left_intact():
    """An unknown/absent placeholder must not raise — left as-is."""
    actions = resolve_device_actions(
        _driver_info_with(web_ui="http://{host}:{webport}"), {"host": "9.9.9.9"}
    )
    link = next(a for a in actions if a["kind"] == "link")
    assert link["url"] == "http://9.9.9.9:{webport}"


# --- web_ui auto-detect (unset = detect, false = off) ----------------------


def test_web_ui_unset_adds_no_button_without_detection():
    """Auto-detect mode (web_ui unset) with nothing detected → no button."""
    actions = resolve_device_actions(
        _driver_info_with(transport="tcp"), {"host": "10.0.0.5"}
    )
    assert not [a for a in actions if a["kind"] == "link"]


def test_web_ui_auto_uses_detected_url():
    """A URL the runtime detected (probe/discovery) drives the button."""
    actions = resolve_device_actions(
        _driver_info_with(transport="tcp"),
        {"host": "10.0.0.5"},
        detected_web_ui_url="http://10.0.0.5:8080",
    )
    link = next(a for a in actions if a["kind"] == "link")
    assert link["id"] == "open_web_ui"
    assert link["url"] == "http://10.0.0.5:8080"


def test_web_ui_false_suppresses_even_with_detected_url():
    """web_ui: false forces the button off regardless of detection."""
    actions = resolve_device_actions(
        _driver_info_with(web_ui=False, transport="tcp"),
        {"host": "10.0.0.5"},
        detected_web_ui_url="http://10.0.0.5",
    )
    assert not [a for a in actions if a["kind"] == "link"]


def test_web_ui_false_suppresses_http_transport_button():
    actions = resolve_device_actions(
        _driver_info_with(web_ui=False, transport="http"), {"host": "10.0.0.5"}
    )
    assert not [a for a in actions if a["kind"] == "link"]


def test_web_ui_auto_http_transport_derives_url_from_config():
    """An HTTP device in auto mode gets the button from its own config, no probe."""
    actions = resolve_device_actions(
        _driver_info_with(transport="http"), {"host": "10.0.0.5"}
    )
    link = next(a for a in actions if a["kind"] == "link")
    assert link["url"] == "http://10.0.0.5"


def test_web_ui_auto_http_transport_honors_ssl_and_port():
    actions = resolve_device_actions(
        _driver_info_with(transport="http"),
        {"host": "10.0.0.5", "ssl": True, "port": 8443},
    )
    link = next(a for a in actions if a["kind"] == "link")
    assert link["url"] == "https://10.0.0.5:8443"


def test_web_ui_explicit_true_wins_over_detected_url():
    """An explicit web_ui beats a detected URL — the author declared intent."""
    actions = resolve_device_actions(
        _driver_info_with(web_ui=True, transport="tcp"),
        {"host": "10.0.0.5"},
        detected_web_ui_url="http://10.0.0.5:8080",
    )
    link = next(a for a in actions if a["kind"] == "link")
    assert link["url"] == "https://10.0.0.5"


def test_validate_link_action_ok():
    info = _driver_info_with(actions=[{"id": "web", "kind": "link", "url": "https://{host}"}])
    assert validate_actions(info) == []


def test_validate_link_empty_url_rejected():
    info = _driver_info_with(actions=[{"id": "web", "kind": "link", "url": ""}])
    errs = validate_actions(info)
    assert any("url" in e for e in errs)


def test_validate_url_on_non_link_rejected():
    info = _driver_info_with(
        actions=[{"id": "power_on", "kind": "command", "url": "https://x"}]
    )
    errs = validate_actions(info)
    assert any("url" in e and "link" in e for e in errs)


def test_invoke_link_action_returns_400(actions_client):
    """A link opens client-side; invoking it server-side is a 400, not a crash."""
    c, engine = actions_client
    driver = MagicMock()
    driver.DRIVER_INFO = {"web_ui": True, "commands": {}}
    engine.devices.get_driver = MagicMock(return_value=driver)
    resp = c.post("/api/devices/dev1/actions/open_web_ui", json={"params": {}})
    assert resp.status_code == 400
    assert "link" in resp.text.lower()
