"""A UI interaction reports what it DID, not only what it changed.

The defect this closes is a verification tool that could not verify the
commonest binding there is. ``simulate_ui_action`` watched the state store, and
a ``device.command`` writes no state key directly -- it goes out the wire and
comes back on a poll or a push -- so a button that fired came back as
``{"success": true, "state_changes": []}``, which is byte-identical to what a
button with no binding at all returns. Asked to verify its own work, the cloud
AI read that as "the command silently failed" and it was right to.

Every device and macro here is invented. This tests a platform capability.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openavc.core.engine import Engine
from openavc.core.project_loader import load_project


def _project(tmp_path: Path, elements: list[dict], macros: list[dict] | None = None) -> Path:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.8.0",
        "project": {"id": "sim", "name": "Sim", "description": ""},
        "devices": [],
        "macros": macros or [],
        "variables": [{"id": "level", "type": "number", "default": 0}],
        "ui": {
            "settings": {},
            "pages": [{"id": "main", "name": "Main", "elements": elements, "layouts": []}],
        },
    }), encoding="utf-8")
    return path


def _engine(tmp_path: Path, elements: list[dict], macros: list[dict] | None = None) -> Engine:
    engine = Engine(_project(tmp_path, elements, macros))
    engine.project = load_project(engine.project_path)
    return engine


@pytest.mark.asyncio
async def test_a_command_that_fired_says_so(tmp_path) -> None:
    """The exact case that read as a silent failure."""
    sent: list[tuple] = []

    engine = _engine(tmp_path, [{
        "id": "btn_mute", "type": "button", "label": "Mute",
        "bindings": {"do": {"press": [{
            "action": "device.command", "device": "acme_amp",
            "command": "mute_on", "params": {"channel": "01"},
        }]}},
    }])

    async def send(device_id, command, params):
        sent.append((device_id, command, params))

    engine.devices.send_command = send

    dispatched = await engine.handle_ui_event("press", "btn_mute")

    assert sent == [("acme_amp", "mute_on", {"channel": "01"})]
    assert dispatched == [{
        "action": "device.command", "device": "acme_amp", "command": "mute_on",
        "params": {"channel": "01"}, "sent": True,
    }]


@pytest.mark.asyncio
async def test_a_command_that_raised_is_not_reported_as_sent(tmp_path) -> None:
    """A driver that refused is a different answer from one that was never asked."""
    engine = _engine(tmp_path, [{
        "id": "btn_mute", "type": "button",
        "bindings": {"do": {"press": [
            {"action": "device.command", "device": "acme_amp", "command": "mute_on"},
        ]}},
    }])

    async def send(device_id, command, params):
        raise RuntimeError("device is offline")

    engine.devices.send_command = send

    dispatched = await engine.handle_ui_event("press", "btn_mute")

    assert dispatched[0]["sent"] is False
    assert "offline" in dispatched[0]["error"]


@pytest.mark.asyncio
async def test_an_object_shaped_binding_dispatches_and_is_recorded(tmp_path) -> None:
    """The shape the report called a silent failure. It runs, and now it shows.

    `ui_events` wraps a non-list before executing, which is why rejecting this
    shape would refuse something the runtime does execute.
    """
    sent: list[tuple] = []
    engine = _engine(tmp_path, [{
        "id": "btn_obj", "type": "button",
        "bindings": {"do": {"press": {
            "action": "device.command", "device": "acme_amp", "command": "mute_on",
        }}},
    }])

    async def send(device_id, command, params):
        sent.append((device_id, command))

    engine.devices.send_command = send

    dispatched = await engine.handle_ui_event("press", "btn_obj")

    assert sent == [("acme_amp", "mute_on")]
    assert [d["action"] for d in dispatched] == ["device.command"]


@pytest.mark.asyncio
async def test_a_value_map_that_matched_nothing_says_so(tmp_path) -> None:
    """The quiet failure this binding shape invites.

    A select whose value matches no key in the map runs nothing at all, and
    every other signal available said the interaction succeeded.
    """
    engine = _engine(tmp_path, [{
        "id": "sel_src", "type": "select",
        "options": [{"label": "A", "value": "a"}],
        "bindings": {"do": {"change": [{"action": "value_map", "map": {
            "a": {"action": "state.set", "key": "var.level", "value": 1},
        }}]}},
    }])

    matched = await engine.handle_ui_event("change", "sel_src", {"value": "a"})
    assert [d["action"] for d in matched] == ["value_map", "state.set"]
    assert matched[0]["matched"] is True

    missed = await engine.handle_ui_event("change", "sel_src", {"value": "zzz"})
    assert missed == [{"action": "value_map", "value": "zzz", "matched": False}]


@pytest.mark.asyncio
async def test_nothing_bound_dispatches_nothing(tmp_path) -> None:
    engine = _engine(tmp_path, [{"id": "lbl", "type": "label", "text": "Hi"}])
    assert await engine.handle_ui_event("press", "lbl") == []


@pytest.mark.asyncio
async def test_a_macro_start_is_recorded(tmp_path) -> None:
    engine = _engine(
        tmp_path,
        [{"id": "btn_go", "type": "button",
          "bindings": {"do": {"press": [{"action": "macro", "macro": "system_on"}]}}}],
        macros=[{"id": "system_on", "name": "On", "steps": []}],
    )
    started: list[str] = []

    async def execute(macro_id):
        started.append(macro_id)

    engine.macros.execute = execute

    dispatched = await engine.handle_ui_event("press", "btn_go")

    assert dispatched == [{"action": "macro", "macro": "system_on", "started": True}]
