"""A device group member that names nothing must not read as an offline device.

Three holes, one shape. A device group is a bare list of ids, and nothing ever
checked that the ids were real:

1. Nothing validated membership. The project save is shape-only by design, and
   the macro lint checked that the step's GROUP existed while saying nothing
   about the devices inside it -- though the device and macro branches beside
   it both check. A group listing ``ghost_a`` saved in silence.
2. ``DELETE /api/devices/{id}`` swept the connections table and the monitors
   and left the id sitting in every group that named it.
3. At run time the fan-out read ``device.<id>.connected``, got ``None`` for an
   id that was never a device, took the falsy value for "offline" and reported
   **Device offline**. So an integrator who typo'd a member id, or whose device
   was deleted months ago, went looking for a network fault on a device that
   does not exist -- and would keep looking, because nothing about it ever
   changes.

The fan-out itself is good and none of this may disturb it: concurrent sends,
per-device outcomes, an empty group skipping cleanly, an unknown group id
refused outright. The tests below pin those alongside the fix.

Every device and group here is invented. This tests a platform capability.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.device_manager import DeviceManager
from openavc.core.device_references import drop_device_from_groups
from openavc.core.engine import Engine
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_validation import validate_macro
from openavc.core.project_loader import ProjectConfig, save_project
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.main import app


# --- The runtime fan-out ---------------------------------------------------


@pytest.fixture
def fanout():
    """A macro engine that knows which devices the project declares."""
    state = StateStore()
    events = EventBus()
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    known: set[str] = {"real_a", "real_b"}
    engine = MacroEngine(state, events, devices, project_device_ids=lambda: known)
    engine.load_groups([
        {"id": "wall", "name": "Video Wall", "device_ids": ["real_a", "ghost", "real_b"]},
    ])
    engine.load_macros([{
        "id": "go", "name": "Go",
        "steps": [{"action": "group.command", "group": "wall", "command": "power_on"}],
    }])
    return engine, state, known


async def _results(engine, state) -> dict[str, dict]:
    """Run the macro and collect the per-device outcome it reports."""
    seen: dict[str, dict] = {}

    async def capture(event, payload):
        for entry in payload.get("device_results") or []:
            seen[entry["device_id"]] = entry

    engine.events.on("macro.progress.go", capture)
    await engine.execute("go")
    return seen


async def test_a_ghost_member_is_not_reported_as_offline(fanout) -> None:
    engine, state, _known = fanout
    state.set("device.real_a.connected", True)
    state.set("device.real_b.connected", True)

    results = await _results(engine, state)

    assert results["ghost"]["error"] == "Device not found"
    assert results["ghost"]["error"] != "Device offline"
    # The sentence names the group, which is the one thing this path knows that
    # a device.command step does not, and it is what somebody needs to fix it.
    assert "Video Wall" in results["ghost"]["message"]
    assert "not in this project" in results["ghost"]["message"]


async def test_a_real_member_that_is_offline_still_reads_as_offline(fanout) -> None:
    """The negative control. Confusing these two the other way would be worse:
    every disconnected projector in the room would report as nonexistent."""
    engine, state, _known = fanout
    state.set("device.real_a.connected", True)
    state.set("device.real_b.connected", False)

    results = await _results(engine, state)

    assert results["real_b"]["error"] == "Device offline"
    assert results["real_a"]["success"] is True


async def test_the_ghost_is_never_commanded(fanout) -> None:
    engine, state, _known = fanout
    state.set("device.real_a.connected", True)
    state.set("device.real_b.connected", True)

    await _results(engine, state)

    commanded = {c.args[0] for c in engine.devices.send_command.call_args_list}
    assert commanded == {"real_a", "real_b"}


async def test_a_device_being_edited_is_not_called_missing(fanout) -> None:
    """update_device removes and re-adds, clearing the config entry and every
    device.<id>.* state key while it awaits a disconnect. A runtime oracle
    would report a device that is merely being saved as one that does not
    exist -- the same lie this item fixes, pointed the other way."""
    engine, state, _known = fanout
    state.set("device.real_a.connected", True)
    # real_b is in the project but has nothing in the runtime at all.
    assert engine.devices.get_device_config("real_b") is None
    assert state.get("device.real_b.connected") is None

    results = await _results(engine, state)

    assert results["real_b"]["error"] == "Device offline"


async def test_a_group_of_nothing_but_ghosts_fails_the_step(fanout) -> None:
    """Reaching nobody is a failed step; the existing all-failed rule carries
    the ghost's own reason up rather than a count."""
    engine, _state, _known = fanout
    engine.load_groups([{"id": "wall", "name": "Video Wall", "device_ids": ["ghost"]}])

    outcome = await engine.execute("go")

    assert outcome == "failed"


async def test_an_unwired_harness_asks_nothing(fanout) -> None:
    """With no provider the platform cannot answer, so it does not guess. This
    is what keeps the plugin harness and every existing group test unchanged."""
    engine, state, _known = fanout
    engine._project_device_ids = None
    state.set("device.real_a.connected", True)
    state.set("device.real_b.connected", True)

    results = await _results(engine, state)

    assert results["ghost"]["error"] == "Device offline"


# --- The lint --------------------------------------------------------------


def _project(**sections) -> ProjectConfig:
    return ProjectConfig.model_validate({
        "project": {"id": "g", "name": "Groups"},
        "devices": [
            {"id": "real_a", "driver": "acme_widget_group", "name": "Real A", "config": {}},
        ],
        "device_groups": [
            {"id": "wall", "name": "Video Wall", "device_ids": ["real_a", "ghost"]},
        ],
        **sections,
    })


def test_the_lint_names_a_group_member_that_does_not_exist(caplog) -> None:
    """Warned, not refused -- a group may legitimately be built before the
    devices are added, which is why the ids beside it warn too."""
    steps = [{"action": "group.command", "group": "wall", "command": "power_on"}]

    with caplog.at_level("WARNING", logger="openavc.core.macro_validation"):
        refusal = validate_macro(steps, [], _project())

    assert refusal is None
    logged = " ".join(r.message for r in caplog.records)
    assert "'ghost'" in logged and "not in the project" in logged
    assert "'real_a'" not in logged


def test_the_lint_says_nothing_about_a_group_whose_members_all_exist(caplog) -> None:
    project = _project()
    project.device_groups[0].device_ids = ["real_a"]
    steps = [{"action": "group.command", "group": "wall", "command": "power_on"}]

    with caplog.at_level("WARNING", logger="openavc.core.macro_validation"):
        validate_macro(steps, [], project)

    assert not [r for r in caplog.records if "lists device" in r.message]


def test_an_unknown_group_is_still_reported_as_the_unknown_group(caplog) -> None:
    """The member walk must not swallow the check that was already there."""
    steps = [{"action": "group.command", "group": "nope", "command": "power_on"}]

    with caplog.at_level("WARNING", logger="openavc.core.macro_validation"):
        validate_macro(steps, [], _project())

    logged = " ".join(r.message for r in caplog.records)
    assert "device group 'nope' not found" in logged


# --- The sweep -------------------------------------------------------------


def test_the_sweep_removes_only_the_deleted_id() -> None:
    project = _project()
    project.device_groups.append(
        type(project.device_groups[0])(id="audio", name="Audio", device_ids=["real_a"])
    )

    swept = drop_device_from_groups(project.device_groups, "real_a")

    assert swept[0].device_ids == ["ghost"]
    assert swept[1].device_ids == []


def test_a_group_emptied_by_the_sweep_is_kept() -> None:
    """An empty group is a legal state the fan-out skips cleanly. Deleting one
    would throw away its name and every step and binding pointing at it."""
    project = _project()
    project.device_groups[0].device_ids = ["real_a"]

    swept = drop_device_from_groups(project.device_groups, "real_a")

    assert [g.id for g in swept] == ["wall"]
    assert swept[0].device_ids == []


class _GroupNoopDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget_group",
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"port": 5000},
        "state_variables": {},
        "commands": {"power_on": {}},
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture
async def doors(tmp_path):
    register_driver(_GroupNoopDriver)
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = ProjectConfig.model_validate({
        "project": {"id": "g", "name": "Groups"},
        "devices": [
            {"id": "real_a", "driver": "acme_widget_group", "name": "Real A", "config": {}},
            {"id": "real_b", "driver": "acme_widget_group", "name": "Real B", "config": {}},
        ],
        "device_groups": [
            {"id": "wall", "name": "Video Wall", "device_ids": ["real_a", "real_b"]},
            {"id": "solo", "name": "Solo", "device_ids": ["real_a"]},
        ],
    })
    save_project(project_path, engine.project)
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
        unregister_driver("acme_widget_group")


async def test_the_rest_delete_sweeps_the_id_out_of_every_group(doors) -> None:
    client, engine, _handler = doors

    assert client.delete("/api/devices/real_a").status_code == 200

    groups = {g.id: g.device_ids for g in engine.project.device_groups}
    assert groups == {"wall": ["real_b"], "solo": []}


async def test_the_ai_delete_sweeps_them_too(doors) -> None:
    """One rule at both doors, the same as the reference report beside it."""
    _client, engine, handler = doors

    result = await handler._delete_device({"device_id": "real_a"})

    assert result["status"] == "deleted"
    groups = {g.id: g.device_ids for g in engine.project.device_groups}
    assert groups == {"wall": ["real_b"], "solo": []}


async def test_the_delete_still_reports_the_membership_it_removed(doors) -> None:
    """The sweep must not silence the report: somebody deleting a device is
    told the group named it, and then the id is taken out."""
    client, _engine, _handler = doors

    refs = client.delete("/api/devices/real_a").json()["references"]

    assert {r["group_id"] for r in refs["device_groups"]} == {"wall", "solo"}


async def test_a_real_engine_actually_answers_the_question(doors) -> None:
    """End to end through Engine, because everything above builds the macro
    engine by hand and would pass with the provider left unwired. This is the
    only test that fails if the platform never tells it what the project holds.
    """
    _client, engine, _handler = doors
    engine.project.device_groups[0].device_ids = ["real_a", "ghost"]
    engine.macros.load_groups([g.model_dump() for g in engine.project.device_groups])
    engine.macros.load_macros([{
        "id": "go", "name": "Go",
        "steps": [{"action": "group.command", "group": "wall", "command": "power_on"}],
    }])
    engine.state.set("device.real_a.connected", True)

    seen: dict[str, dict] = {}

    async def capture(event, payload):
        for entry in payload.get("device_results") or []:
            seen[entry["device_id"]] = entry

    engine.events.on("macro.progress.go", capture)
    await engine.macros.execute("go")

    assert seen["ghost"]["error"] == "Device not found"
    assert seen["real_a"]["success"] is True
