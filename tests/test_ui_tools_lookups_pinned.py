"""The lookups the AI's UI writes and its whole-project review hand the checks.

``reference_findings`` is told what this system knows through injected
lookups: the commands a driver declares, the panel elements a loaded plugin
declares, whether a bound property and a child id are declared, which command
parameters are unfilled, which commands are file commands, and what is in the
project's ``ui/`` folder. A write (``add_ui_elements``) and the review
(``review_ui``) each hand it the same set. These pin, through the real tool
doors and an invented device, that every lookup reaches the check from both
doors and what each one's answer makes the reply say; and that a lookup with
no opinion (a plugin that is not loaded, no project folder) says nothing.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.cloud.ai_tool_handler import AIToolHandler

from tests.test_ai_ui_write_guards import _drain, _tool_call


class _Amp:
    """An invented amplifier with commands, flat state and a channel roster."""

    DRIVER_INFO = {
        "id": "acme_amp",
        "commands": {
            "power_on": {"label": "Power On"},
            "set_input": {
                "label": "Set Input",
                "params": {"input": {"type": "integer", "required": True}},
            },
            "export_log": {"label": "Export Log", "returns_file": True},
        },
        "state_variables": {"level": {"type": "float", "min": -60.0, "max": 6.0}},
        "child_entity_types": {
            "channel": {
                "instances": {"count": 4},
                "state_variables": {"gain": {"type": "float"}},
            },
        },
    }
    config: dict = {}


class _Devices:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def get_driver(self, device_id):
        self.asked.append(device_id)
        return _Amp() if device_id == "amp" else None

    def list_devices(self):
        return []


class _Plugins:
    def get_all_extensions(self):
        return {"panel_elements": [
            {"plugin_id": "acme_meters", "type": "vu"},
            {"plugin_id": "acme_meters", "type": "peak"},
            "not a mapping",
        ]}


def _button(el_id: str, action: dict) -> dict:
    return {"id": el_id, "type": "button", "label": el_id,
            "bindings": {"do": {"press": [action]}}}


def _label(el_id: str, key: str) -> dict:
    return {"id": el_id, "type": "label", "text": el_id,
            "bindings": {"show": {"value": {"key": key}}}}


def _placed(elements: list[dict]) -> list[dict]:
    """Each in its own roomy box, so the geometry review has nothing to say."""
    return [
        {**el, "placement": {"x": (i % 4) * 25, "y": (i // 4) * 25, "w": 24, "h": 24}}
        for i, el in enumerate(elements)
    ]


ELEMENTS = _placed([
    _button("b_unknown", {"action": "device.command", "device": "amp", "command": "power_onn"}),
    _button("b_params", {"action": "device.command", "device": "amp", "command": "set_input"}),
    _button("b_file", {"action": "device.command", "device": "amp", "command": "export_log"}),
    _button("b_fine", {"action": "device.command", "device": "amp", "command": "power_on"}),
    _label("l_prop", "device.amp.levl"),
    _label("l_child", "device.amp.channel.7.gain"),
    _label("l_fine", "device.amp.level"),
    {"id": "p_type", "type": "plugin", "plugin_id": "acme_meters", "plugin_type": "dial"},
    {"id": "p_absent", "type": "plugin", "plugin_id": "acme_other", "plugin_type": "dial"},
    {"id": "c_file", "type": "custom", "custom_file": "missing.html"},
    {"id": "c_fine", "type": "custom", "custom_file": "panel/ok.html"},
])


def _make_engine(project_path):
    from openavc.core.project_loader import (
        DeviceConfig, Layout, ProjectConfig, ProjectMeta, UIConfig, UIPage,
    )
    engine = MagicMock()
    engine.project = ProjectConfig(
        project=ProjectMeta(id="acme_room", name="Acme Room"),
        devices=[DeviceConfig(id="amp", driver="acme_amp", name="Amplifier")],
        ui=UIConfig(pages=[
            UIPage(id="main", name="Main", elements=[],
                   layouts=[Layout(id="landscape", primary=True)]),
            UIPage(id="other", name="Other", elements=[],
                   layouts=[Layout(id="landscape", primary=True)]),
        ]),
    )
    engine.project_path = project_path
    engine.plugins = _Plugins()
    engine.broadcast_ws = AsyncMock()

    async def _apply_edit(mutate):
        new_project = engine.project.model_copy(deep=True)
        mutate(new_project)
        engine.project = new_project
        return 1

    engine.apply_project_edit = AsyncMock(side_effect=_apply_edit)
    return engine


@pytest.fixture(autouse=True)
def _patch_save_project():
    with patch("openavc.core.project_loader.save_project"):
        yield


@pytest.fixture
def project_path(tmp_path):
    ui = tmp_path / "ui" / "panel"
    ui.mkdir(parents=True)
    (ui / "ok.html").write_text("<!doctype html><title>ok</title>")
    return tmp_path / "project.avc"


async def _call(engine, tool: str, payload: dict) -> dict:
    agent = MagicMock()
    agent.send_message = AsyncMock()
    agent.state = MagicMock()
    devices = _Devices()
    handler = AIToolHandler(agent, devices, MagicMock(emit=AsyncMock()))
    with patch.object(handler, "_get_engine", return_value=engine):
        await handler.handle(_tool_call(tool, payload))
        await _drain()
    return agent.send_message.call_args[0][1]["result"]


# What each lookup's answer makes the check say, in the order the checks run:
# the page review (the range lookup: a float with no rounding), then the
# references (commands, parameters, file commands, properties, children,
# plugin elements, the ui/ folder).
_DECIMALS = (
    "(label) shows {key}, which the driver declares as a float, and sets no "
    "display_decimals. A label prints a number exactly as it arrives, so 0.06 reads "
    "as 0.06000000238418579. Every other type that draws a number picks its own "
    "rounding; a label cannot, because most are bound to text. Set display_decimals."
)
REVIEW_FINDINGS = [
    "l_child " + _DECIMALS.format(key="device.amp.channel.7.gain"),
    "l_fine " + _DECIMALS.format(key="device.amp.level"),
    "b_unknown (button) do.press[0] sends 'power_onn' to 'amp', which its driver "
    "does not have. Its commands are: export_log, power_on, set_input.",
    "b_params (button) do.press[0] sends 'set_input' to 'amp' without 'input'. Its "
    "driver requires it, so the device refuses every press with \"'set_input': "
    "'input' is required\".",
    "b_file (button) do.press[0] sends 'export_log' to 'amp', and every press is "
    "refused with \"'Export Log' sends back a file, so it runs from Send Command on "
    "the device's page.\"",
    "l_prop (label) reads show.value from 'device.amp.levl', and its driver does not "
    "declare that. It declares: level.",
    "l_child (label) reads show.value from 'device.amp.channel.7.gain', and its "
    "driver declares no such child. It declares: 1, 2, 3, 4.",
    "p_type (plugin) asks 'acme_meters' for a 'dial' element, which it does not "
    "declare, so the panel loads a renderer that is not there. acme_meters "
    "declares: peak, vu.",
    "c_file (custom control) shows 'missing.html', which is not in the project's "
    "ui/ folder, so it draws an empty box. The files there are: panel/ok.html.",
]
# Only a write reviews the markup behind the custom controls it touched.
MARKUP_FINDINGS = [
    "panel/ok.html never listens for openavc:init, so the state, theme and grant "
    "the panel sends it are all ignored and it draws the same thing whatever the "
    "room is doing. Add a window 'message' listener, or leave it as decoration on "
    "purpose.",
    "panel/ok.html never reports its own errors. A control that throws without "
    "window.onerror -> openavc:error is a blank rectangle on a wall panel with no "
    "console to check. Add: window.onerror = (m) => parent.postMessage({type: "
    "'openavc:error', message: String(m)}, '*');",
]
WRITE_WARNINGS = REVIEW_FINDINGS + MARKUP_FINDINGS


@pytest.mark.asyncio
async def test_every_lookup_reaches_the_check_from_the_write_door(project_path):
    engine = _make_engine(project_path)
    result = await _call(engine, "add_ui_elements", {
        "page_id": "main", "elements": json.loads(json.dumps(ELEMENTS)),
    })
    assert result["status"] == "created"
    assert result["warnings"] == WRITE_WARNINGS


@pytest.mark.asyncio
async def test_every_lookup_reaches_the_check_from_the_review(project_path):
    engine = _make_engine(project_path)
    await _call(engine, "add_ui_elements", {
        "page_id": "main", "elements": json.loads(json.dumps(ELEMENTS)),
    })
    result = await _call(engine, "review_ui", {})
    assert result["pages"] == {"main": REVIEW_FINDINGS}
    assert result["finding_count"] == len(REVIEW_FINDINGS)
    assert result["status"] == "findings"
    assert result["pages_checked"] == ["main", "other"]


@pytest.mark.asyncio
async def test_no_project_folder_and_no_plugins_say_nothing_from_either_door():
    engine = _make_engine(None)
    engine.plugins = None
    result = await _call(engine, "add_ui_elements", {
        "page_id": "main", "elements": json.loads(json.dumps(ELEMENTS)),
    })
    quiet = [w for w in REVIEW_FINDINGS if not w.startswith(("p_type ", "c_file "))]
    assert len(quiet) == len(REVIEW_FINDINGS) - 2
    assert result["warnings"] == quiet
    review = await _call(engine, "review_ui", {"page_id": "main"})
    assert review["pages"] == {"main": quiet}


@pytest.mark.asyncio
async def test_a_write_answers_only_for_what_it_wrote(project_path):
    """The review reads the whole page; a write only the elements it named."""
    engine = _make_engine(project_path)
    await _call(engine, "add_ui_elements", {
        "page_id": "main", "elements": json.loads(json.dumps(ELEMENTS)),
    })
    result = await _call(engine, "add_ui_elements", {
        "page_id": "main",
        "elements": [{**_label("l_more", "device.amp.channel.9.gain"),
                      "placement": {"x": 0, "y": 75, "w": 24, "h": 24}}],
    })
    assert result["warnings"] == [
        "l_more " + _DECIMALS.format(key="device.amp.channel.9.gain"),
        "l_more (label) reads show.value from 'device.amp.channel.9.gain', and its "
        "driver declares no such child. It declares: 1, 2, 3, 4.",
    ]


@pytest.mark.asyncio
async def test_a_project_folder_with_no_ui_folder_holds_no_files_from_either_door(tmp_path):
    """No ``ui/`` folder is an empty one, not "no opinion": a custom control
    pointing into it is told so."""
    engine = _make_engine(tmp_path / "project.avc")
    missing = (
        "c_any (custom control) shows 'panel/ok.html', which is not in the project's "
        "ui/ folder, so it draws an empty box. The files there are: none yet."
    )
    result = await _call(engine, "add_ui_elements", {"page_id": "main", "elements": _placed([
        {"id": "c_any", "type": "custom", "custom_file": "panel/ok.html"},
    ])})
    assert result["warnings"] == [missing]
    review = await _call(engine, "review_ui", {})
    assert review["pages"] == {"main": [missing]}


@pytest.mark.asyncio
async def test_macros_and_every_page_are_known_to_both_doors(project_path):
    """The macros the write door checks against are the project's, and a
    review of one page still knows every page a control can move to."""
    from openavc.core.project_loader import MacroConfig

    engine = _make_engine(project_path)
    engine.project.macros.append(MacroConfig(id="shutdown", name="Shutdown"))
    expected = [
        "b_macro (button) do.press[0] runs macro 'shutdwn', which does not exist. "
        "The macros are: shutdown.",
        "b_nav_bad (button) do.press[0] navigates to page 'nowhere', which does not "
        "exist. The pages are: main, other.",
    ]
    result = await _call(engine, "add_ui_elements", {"page_id": "main", "elements": _placed([
        _button("b_macro", {"action": "macro", "macro": "shutdwn"}),
        _button("b_macro_ok", {"action": "macro", "macro": "shutdown"}),
        _button("b_nav", {"action": "ui.navigate", "page": "other"}),
        _button("b_nav_bad", {"action": "ui.navigate", "page": "nowhere"}),
    ])})
    assert result["warnings"] == expected
    review = await _call(engine, "review_ui", {"page_id": "main"})
    assert review["pages"] == {"main": expected}
    assert review["pages_checked"] == ["main"]

