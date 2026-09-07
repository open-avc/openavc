"""Deleting a device says what it just orphaned, at every door.

``DELETE /api/devices/{id}`` used to drop the device, pop its connections row,
drop its monitors and answer ``{"status": "deleted"}`` -- while a device group
still listed it, macro steps still commanded it, triggers still watched
``device.<id>.connected`` and bound controls on a panel still read it. Nothing
was said. The AI door did report an impact, but its scanner looked at
``step["device"]`` at the top level of a macro and an action's ``device`` key,
so it reported an empty impact for every one of the above -- which is worse than
reporting nothing, because it reads as an all-clear.

Both doors now ask ``core/device_references``, which is the Builder's rule
ported to the server and pinned against it by
``test_device_references_parity.py``. That file compares the two walks sentence
for sentence; this one covers what it cannot: the doors, the structured report
they hand back, and the script grep the Builder has no way to perform.

**The doors report; they do not refuse.** A delete that names live references
still returns 200 and still deletes. The reasoning is on the queue entry and in
the module docstring; the tests below pin it so it cannot be quietly reversed
into a 409.

Every device, macro, page and script here is invented.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.device_references import (
    as_reference_report,
    find_device_references,
    find_script_references,
)
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, save_project
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.main import app


class _RefNoopDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget_refs",
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"port": 5000},
        "state_variables": {},
        "commands": {"power_on": {}, "power_off": {}},
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _seed() -> dict:
    """One project in which `widget` is referenced every way it can be.

    `widget10` is its sibling and is referenced too -- deleting `widget` must
    not claim any of that.
    """
    return {
        "project": {"id": "refs", "name": "References"},
        "devices": [
            {"id": "widget", "driver": "acme_widget_refs", "name": "Widget", "config": {}},
            {"id": "widget10", "driver": "acme_widget_refs", "name": "Widget 10", "config": {}},
            {"id": "spare", "driver": "acme_widget_refs", "name": "Spare", "config": {}},
        ],
        "device_groups": [
            {"id": "wall", "name": "Video Wall", "device_ids": ["widget", "widget10"]},
        ],
        "macros": [
            {
                "id": "start", "name": "Start Meeting",
                "steps": [
                    {"action": "conditional",
                     "condition": {"key": "var.mode", "operator": "eq", "value": "present"},
                     "then_steps": [
                         {"action": "device.command", "device": "widget", "command": "power_on"},
                     ],
                     "else_steps": [
                         {"action": "device.command", "device": "widget10", "command": "power_on"},
                     ]},
                ],
                "triggers": [
                    {"id": "onconn", "type": "state_change",
                     "state_key": "device.widget.connected"},
                ],
            },
        ],
        "ui": {
            "pages": [
                {"id": "home", "name": "Home", "elements": [
                    {"id": "vol", "type": "slider", "label": "Volume",
                     "bindings": {"show.value": {"key": "device.widget.volume"},
                                  "do.change": [{"action": "device.command",
                                                 "device": "widget",
                                                 "command": "set_level"}]}},
                    {"id": "other", "type": "slider", "label": "Other",
                     "bindings": {"show.value": {"key": "device.widget10.volume"}}},
                ]},
            ],
            "master_elements": [
                {"id": "banner", "type": "label", "label": "Status", "pages": "*",
                 "bindings": {"show.text": {"key": "device.widget.status"}}},
            ],
        },
        "scripts": [{"id": "greeter", "file": "greeter.py"}],
    }


@pytest.fixture
async def refs_engine(tmp_path):
    register_driver(_RefNoopDriver)
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = ProjectConfig.model_validate(_seed())
    save_project(project_path, engine.project)

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "greeter.py").write_text(
        "openavc.devices.widget.send('power_on')\n", encoding="utf-8"
    )

    for device in engine.project.devices:
        await engine.devices.add_device(engine.resolved_device_config(device))

    rest.set_engine(engine)
    ws.set_engine(engine)
    handler = AIToolHandler(MagicMock(), engine.devices, MagicMock())
    try:
        yield TestClient(app), engine, handler
    finally:
        await engine.devices.disconnect_all()
        rest.set_engine(None)
        ws.set_engine(None)
        unregister_driver("acme_widget_refs")


# --- The REST door ---------------------------------------------------------


async def test_rest_delete_reports_every_kind_of_reference(refs_engine) -> None:
    client, _engine, _handler = refs_engine
    resp = client.delete("/api/devices/widget")
    assert resp.status_code == 200
    refs = resp.json()["references"]

    assert [r["group_id"] for r in refs["device_groups"]] == ["wall"]
    assert refs["macros"] == [
        {"macro_id": "start", "macro_name": "Start Meeting", "steps": 1,
         "message": 'Macro "Start Meeting": 1 step(s)'},
    ]
    assert [t["value"] for t in refs["triggers"]] == ["device.widget.connected"]
    # The slider is reached by BOTH a show key and a do action; the master
    # element was invisible to every scanner before this module.
    assert [(b["element_id"], b["slots"]) for b in refs["bindings"]] == [
        ("vol", ["do.change", "show.value"]),
        ("banner", ["show.text"]),
    ]
    assert refs["scripts"] == [{"script_id": "greeter", "file": "greeter.py"}]


async def test_rest_delete_reports_but_does_not_refuse(refs_engine) -> None:
    """The design call: a deliberate delete of a referenced device still works.

    Refusing would turn this 200 into a 409 for every caller deleting on
    purpose, behind a confirm flag, while the only door with a human on it
    already lists the references before it calls DELETE.
    """
    client, engine, _handler = refs_engine
    resp = client.delete("/api/devices/widget")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert [d.id for d in engine.project.devices] == ["widget10", "spare"]


async def test_rest_delete_of_an_unreferenced_device_says_nothing(refs_engine) -> None:
    """No references means no key at all -- not an empty bag to be read past."""
    client, _engine, _handler = refs_engine
    resp = client.delete("/api/devices/spare")
    assert resp.status_code == 200
    assert "references" not in resp.json()


async def test_rest_delete_does_not_claim_a_sibling_devices_references(refs_engine) -> None:
    client, _engine, _handler = refs_engine
    refs = client.delete("/api/devices/widget10").json()["references"]
    assert [b["element_id"] for b in refs["bindings"]] == ["other"]
    assert "triggers" not in refs
    assert [r["group_id"] for r in refs["device_groups"]] == ["wall"]


async def test_rest_delete_of_a_missing_device_is_still_a_404(refs_engine) -> None:
    client, _engine, _handler = refs_engine
    assert client.delete("/api/devices/ghost").status_code == 404


# --- The AI door -----------------------------------------------------------


async def test_ai_check_references_sees_what_the_old_scanner_missed(refs_engine) -> None:
    """Groups, conditional branches, triggers and show bindings — all four were
    reported as absent by the hand-rolled scanner this replaced."""
    _client, _engine, handler = refs_engine
    refs = (await handler._check_references({"type": "device", "id": "widget"}))["referenced_by"]
    assert set(refs) == {"device_groups", "macros", "triggers", "bindings", "scripts"}
    assert refs["macros"][0]["steps"] == 1          # inside then_steps
    assert refs["device_groups"][0]["group_name"] == "Video Wall"
    assert refs["triggers"][0]["field"] == "state_key"
    assert "show.value" in refs["bindings"][0]["slots"]


async def test_both_doors_return_the_same_report(refs_engine) -> None:
    """The whole point: one rule, so the AI and an API caller cannot be told
    different things about the same delete."""
    client, _engine, handler = refs_engine
    from_ai = (await handler._check_references({"type": "device", "id": "widget"}))["referenced_by"]
    from_rest = client.delete("/api/devices/widget").json()["references"]
    assert from_ai == from_rest


async def test_ai_delete_device_carries_the_impact(refs_engine) -> None:
    _client, _engine, handler = refs_engine
    result = await handler._delete_device({"device_id": "widget"})
    assert result["status"] == "deleted"
    assert result["impact"]["device_groups"][0]["group_id"] == "wall"


async def test_the_other_reference_types_are_untouched(refs_engine) -> None:
    """Only the device branch was replaced; macro and variable still answer in
    their own shape, through the same script grep."""
    _client, _engine, handler = refs_engine
    refs = (await handler._check_references({"type": "macro", "id": "start"}))["referenced_by"]
    assert refs == {}
    bad = await handler._check_references({"type": "nonsense", "id": "x"})
    assert "error" in bad


# --- The script grep -------------------------------------------------------


async def test_script_references_read_the_loaded_projects_own_directory(tmp_path) -> None:
    """The scripts live beside the LOADED project file. Assuming a fixed
    projects/default path silently under-reports on every non-dev deployment,
    which is the deployment that matters."""
    project = ProjectConfig.model_validate({
        "project": {"id": "p", "name": "P"},
        "scripts": [
            {"id": "hit", "file": "hit.py"},
            {"id": "miss", "file": "miss.py"},
            {"id": "gone", "file": "not_written.py"},
            {"id": "escape", "file": "../outside.py"},
        ],
    })
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "hit.py").write_text("openavc.devices.widget.send('on')\n", encoding="utf-8")
    (scripts / "miss.py").write_text("openavc.devices.other.send('on')\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("widget\n", encoding="utf-8")

    hits = find_script_references(project, scripts, "widget")
    assert hits == [{"script_id": "hit", "file": "hit.py"}]


# --- The report shape ------------------------------------------------------


def test_an_empty_walk_reports_an_empty_body() -> None:
    project = ProjectConfig.model_validate({"project": {"id": "p", "name": "P"}})
    assert find_device_references(project, "widget") == []
    assert as_reference_report([], []) == {}


def test_the_report_keeps_the_ids_a_caller_needs_to_go_and_fix_it() -> None:
    """The sentence names the reference; the ids are how it gets found again.
    The old AI shape carried macro_id/macro_name/page_id/element_id and callers
    may read them, so dropping one would be a silent regression."""
    project = ProjectConfig.model_validate(_seed())
    report = as_reference_report(find_device_references(project, "widget"))
    assert set(report["macros"][0]) == {"macro_id", "macro_name", "steps", "message"}
    assert set(report["bindings"][0]) == {"page_id", "element_id", "slots", "message"}
    assert report["bindings"][0]["page_id"] == "home"
    assert set(report["triggers"][0]) >= {"macro_id", "trigger_id", "field", "value", "message"}
    assert report["triggers"][0]["trigger_id"] == "onconn"
    # A master element belongs to no page, so it carries no page_id to invent.
    assert "page_id" not in report["bindings"][1]
    assert "scripts" not in report
