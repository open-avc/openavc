"""Tests for the UI-event runtime's placeholder substitution.

The UI action executor substitutes a small set of placeholders into
device.command params before calling devices.send_command:
  $value  — slider/change/submit value (scaled to output range)
  $input  — matrix route input number
  $output — matrix route output number
  $mute   — mute_route / audio_mute_route mute value (bool)

Since the $-resolver was unified, a binding param can also reference any
$var/$device/$system state key (resolved from the state store), not just the
four UI-event tokens — exercised by the handle_ui_event integration tests below.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.core.engine import Engine


@pytest.fixture
def engine(tmp_path):
    eng = Engine(str(tmp_path / "no_project.avc"))
    eng.devices = AsyncMock()
    return eng


def _project_with_element(element):
    """Wrap a single element in the minimal project shape _find_element walks."""
    page = SimpleNamespace(elements=[element])
    ui = SimpleNamespace(pages=[page])
    return SimpleNamespace(ui=ui)


@pytest.mark.asyncio
async def test_input_output_placeholders_resolve(engine):
    """$input and $output substitute from the data dict."""
    action = {
        "action": "device.command",
        "device": "sw",
        "command": "route",
        "params": {"in": "$input", "out": "$output", "static": "x"},
    }
    await engine.ui_events.execute_action(action, {"input": 3, "output": 1}, element=None)
    engine.devices.send_command.assert_awaited_once_with(
        "sw", "route", {"in": 3, "out": 1, "static": "x"}
    )


@pytest.mark.asyncio
async def test_mute_placeholder_resolves_true(engine):
    """$mute substitutes from the data dict (bool)."""
    action = {
        "action": "device.command",
        "device": "sw",
        "command": "mute",
        "params": {"output": "$output", "mute": "$mute"},
    }
    await engine.ui_events.execute_action(action, {"output": 2, "mute": True}, element=None)
    engine.devices.send_command.assert_awaited_once_with(
        "sw", "mute", {"output": 2, "mute": True}
    )


@pytest.mark.asyncio
async def test_mute_placeholder_resolves_false(engine):
    """$mute carries the false (unmute) value through."""
    action = {
        "action": "device.command",
        "device": "sw",
        "command": "mute",
        "params": {"output": "$output", "mute": "$mute"},
    }
    await engine.ui_events.execute_action(action, {"output": 2, "mute": False}, element=None)
    engine.devices.send_command.assert_awaited_once_with(
        "sw", "mute", {"output": 2, "mute": False}
    )


# --- handle_ui_event integration: resolution through the public entry point ---


@pytest.mark.asyncio
async def test_handle_ui_event_slider_value_scales(engine):
    """A slider's do.change binding's $value is scaled to the output range."""
    element = SimpleNamespace(
        id="vol",
        bindings={
            "do": {
                "change": [
                    {
                        "action": "device.command",
                        "device": "dsp",
                        "command": "set_level",
                        "params": {"level": "$value"},
                    }
                ]
            }
        },
        output_min=0,
        output_max=1,
        min=0,
        max=100,
        scale_to_full=True,
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event("change", "vol", {"value": 50})
    engine.devices.send_command.assert_awaited_once_with(
        "dsp", "set_level", {"level": 0.5}
    )


@pytest.mark.asyncio
async def test_handle_ui_event_button_resolves_var(engine):
    """A button's do.press binding's $var.<name> resolves from the state store."""
    engine.state.set("var.target", 7)
    element = SimpleNamespace(
        id="btn",
        bindings={
            "do": {
                "press": [
                    {
                        "action": "device.command",
                        "device": "dsp",
                        "command": "set_level",
                        "params": {"level": "$var.target"},
                    }
                ]
            }
        },
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event("press", "btn", {})
    engine.devices.send_command.assert_awaited_once_with(
        "dsp", "set_level", {"level": 7}
    )


@pytest.mark.asyncio
async def test_handle_ui_event_route_resolves_input_output(engine):
    """A matrix do.route binding's $input / $output resolve from event data."""
    element = SimpleNamespace(
        id="mtx",
        bindings={
            "do": {
                "route": [
                    {
                        "action": "device.command",
                        "device": "sw",
                        "command": "route",
                        "params": {"in": "$input", "out": "$output"},
                    }
                ]
            }
        },
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event("route", "mtx", {"input": 3, "output": 2})
    engine.devices.send_command.assert_awaited_once_with(
        "sw", "route", {"in": 3, "out": 2}
    )


@pytest.mark.asyncio
async def test_handle_ui_event_audio_mute_route_demux(engine):
    """The audio_mute_route interaction (ws.py demuxes ui.route into it) is
    looked up under do.audio_mute_route with $output / $mute from event data."""
    element = SimpleNamespace(
        id="mtx",
        bindings={
            "do": {
                "audio_mute_route": [
                    {
                        "action": "device.command",
                        "device": "sw",
                        "command": "amute",
                        "params": {"out": "$output", "mute": "$mute"},
                    }
                ]
            }
        },
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event(
        "audio_mute_route", "mtx", {"output": 2, "mute": True}
    )
    engine.devices.send_command.assert_awaited_once_with(
        "sw", "amute", {"out": 2, "mute": True}
    )


# --- Two-way LINK (show.value.write_back) — device-aware ---


@pytest.mark.asyncio
async def test_link_writes_back_var_on_change_scaled(engine):
    """A writable var.* value with write_back writes the scaled display value to
    state on a change event (the two-way LINK)."""
    element = SimpleNamespace(
        id="vol",
        bindings={"show": {"value": {"source": "state", "key": "var.vol", "write_back": True}}},
        output_min=0,
        output_max=1,
        min=0,
        max=100,
        scale_to_full=True,
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event("change", "vol", {"value": 50})
    assert engine.state.get("var.vol") == 0.5


@pytest.mark.asyncio
async def test_link_does_not_write_device_key_on_change(engine):
    """A device.* value must never be written to the state mirror directly, even
    if a stray write_back is present — a device is driven by a do.change command,
    not a state.set (the central rule, enforced defensively at dispatch)."""
    element = SimpleNamespace(
        id="vol",
        bindings={"show": {"value": {"source": "state", "key": "device.dsp.level", "write_back": True}}},
        output_min=0,
        output_max=1,
        min=0,
        max=100,
        scale_to_full=True,
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event("change", "vol", {"value": 50})
    assert engine.state.get("device.dsp.level") is None


@pytest.mark.asyncio
async def test_link_writes_back_var_on_list_select_unscaled(engine):
    """A list's value (its selection) with write_back writes the tapped value on
    a select event, with no scaling (a list has no output range)."""
    element = SimpleNamespace(
        id="zones",
        bindings={"show": {"value": {"source": "state", "key": "var.active_zone", "write_back": True}}},
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event("select", "zones", {"value": "kitchen"})
    assert engine.state.get("var.active_zone") == "kitchen"


@pytest.mark.asyncio
async def test_link_read_only_value_does_not_write(engine):
    """A value without write_back is read-only — a change event drives no
    state.set (only a do.change action would reach the device)."""
    element = SimpleNamespace(
        id="meter",
        bindings={"show": {"value": {"source": "state", "key": "var.level"}}},
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event("change", "meter", {"value": 42})
    assert engine.state.get("var.level") is None


@pytest.mark.asyncio
async def test_link_and_extra_change_action_both_run(engine):
    """Two-way LINK to a var plus an extra do.change action both fire on change:
    the variable is written and the additional device command is sent."""
    element = SimpleNamespace(
        id="vol",
        bindings={
            "show": {"value": {"source": "state", "key": "var.vol", "write_back": True}},
            "do": {
                "change": [
                    {
                        "action": "device.command",
                        "device": "dsp",
                        "command": "set_level",
                        "params": {"level": "$value"},
                    }
                ]
            },
        },
        output_min=0,
        output_max=1,
        min=0,
        max=100,
        scale_to_full=True,
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event("change", "vol", {"value": 50})
    assert engine.state.get("var.vol") == 0.5
    engine.devices.send_command.assert_awaited_once_with(
        "dsp", "set_level", {"level": 0.5}
    )


@pytest.mark.asyncio
async def test_toggle_off_reads_off_action_from_do_press(engine):
    """toggle_off has no do.toggle_off list; the engine falls back to the
    off_action carried inside the do.press action block."""
    element = SimpleNamespace(
        id="btn",
        bindings={
            "do": {
                "press": [
                    {
                        "action": "device.command",
                        "device": "lights",
                        "command": "on",
                        "mode": "toggle",
                        "toggle_key": "device.lights.power",
                        "off_action": {
                            "action": "device.command",
                            "device": "lights",
                            "command": "off",
                        },
                    }
                ]
            }
        },
    )
    engine.project = _project_with_element(element)
    await engine.handle_ui_event("toggle_off", "btn", {})
    engine.devices.send_command.assert_awaited_once_with("lights", "off", {})
