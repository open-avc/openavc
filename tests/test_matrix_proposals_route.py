"""The door the matrix picker knocks on, and what it must read through it.

The inference itself is covered by ``test_matrix_inference.py`` against plain
dicts. What this file answers is the half that only exists at the door: it must
read the LIVE driver, and it must read the roster of ports that actually
registered rather than the range the driver declares. Three shipped drivers
build their declaration at construction time, so a file reader sees an empty
driver; and an eight-port frame with two ports patched is a two-row list, which
nothing but the running instance can say.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.core.engine import Engine
from openavc.core.project_loader import (
    ChildEntityConfig,
    DeviceConfig,
    ProjectConfig,
    ProjectMeta,
    save_project,
)
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.main import app


class _AcmeFrame(BaseDriver):
    """An invented switcher: two planes, both ends typed to a child."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_frame",
        "name": "Acme Frame",
        "transport": "tcp",
        "state_variables": {},
        "child_entity_types": {
            "output": {
                "label": "Output", "label_plural": "Outputs",
                "id_format": {"type": "integer", "min": 1, "max": 8, "pad_width": 2},
                "state_variables": {
                    "input": {"type": "integer", "label": "Routed Input"},
                    "audio_input": {"type": "integer", "label": "Ex-Audio Input"},
                },
            },
            "input": {
                "label": "Input", "label_plural": "Inputs",
                "id_format": {"type": "integer", "min": 1, "max": 8, "pad_width": 2},
                "state_variables": {"name": {"type": "string"}},
            },
        },
        "commands": {
            "route": {"params": {
                "output": {"type": "child_id", "child_type": "output"},
                "input": {"type": "child_id", "child_type": "input"},
            }},
            "audio_route": {"params": {
                "output": {"type": "child_id", "child_type": "output"},
                "input": {"type": "child_id", "child_type": "input"},
            }},
        },
    }

    async def connect(self) -> None:
        # Only the ports that are patched register, which is the whole point:
        # the declared range says 1..8 and this unit has three.
        for local_id in (2, 3, 7):
            self.register_child("output", local_id)
        for local_id in (1, 4):
            self.register_child("input", local_id)

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture
async def frame_engine(tmp_path):
    register_driver(_AcmeFrame)
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = ProjectConfig(
        project=ProjectMeta(id="proj1", name="Test Project"),
        devices=[DeviceConfig(
            id="mx", driver="acme_frame", name="Frame",
            child_entities={"output": {"02": ChildEntityConfig(label="Main LCD", config={})}},
        )],
    )
    save_project(project_path, engine.project)
    for device in engine.project.devices:
        await engine.devices.add_device(engine.resolved_device_config(device))
    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield TestClient(app), engine
    finally:
        await engine.devices.disconnect_all()
        rest.set_engine(None)
        ws.set_engine(None)
        unregister_driver("acme_frame")


def _proposals(client, device_id="mx"):
    resp = client.get(f"/api/ui/matrix-proposals/{device_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body, {p["id"]: p for p in body["proposals"]}


async def test_a_device_offers_one_proposal_per_routing_plane(frame_engine):
    client, _ = frame_engine
    body, found = _proposals(client)
    assert body["live"] is True
    assert set(found) == {"output.input", "output.audio_input"}


async def test_the_ports_that_registered_are_the_ports_offered(frame_engine):
    """A 1..8 frame with three ports patched is a three-row list, not an 8x8.

    Nothing but the running instance can say this, which is why the picker asks
    the device rather than reading the driver file.
    """
    client, _ = frame_engine
    _, found = _proposals(client)
    destinations = found["output.input"]["destinations"]
    assert [d["value"] for d in destinations] == [2, 3, 7]
    assert [s["value"] for s in found["output.input"]["sources"]] == [1, 4]


async def test_a_port_the_project_has_named_keeps_that_name(frame_engine):
    client, _ = frame_engine
    _, found = _proposals(client)
    assert found["output.input"]["destinations"][0]["label"] == "Main LCD"


async def test_the_route_key_uses_the_padded_id_the_state_store_uses(frame_engine):
    client, engine = frame_engine
    _, found = _proposals(client)
    key = found["output.input"]["destinations"][0]["route_key"]
    assert key == "device.mx.output.02.input"
    # And it is a key the device really writes, not one that merely looks right.
    engine.state.set(key, 4, source="test")
    assert engine.state.get(key) == 4


async def test_a_device_that_is_not_in_the_project_is_a_404(frame_engine):
    client, _ = frame_engine
    assert client.get("/api/ui/matrix-proposals/nope").status_code == 404


async def test_a_device_with_no_live_driver_falls_back_to_the_declaration(frame_engine):
    """A panel is normally drawn before the rack is powered on."""
    client, engine = frame_engine
    await engine.devices.remove_device("mx")
    body, found = _proposals(client)
    assert body["live"] is False
    assert [d["value"] for d in found["output.input"]["destinations"]] == list(range(1, 9))
    assert found["output.input"]["destinations"][0]["route_key"] == "device.mx.output.01.input"
