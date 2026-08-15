"""The monitor list survives the round trip through the API, as a DECLARED field.

The recurring trap this guards is in CLAUDE.md and in the monitor plan §8.4: a
project field the runtime reads that no request model declares gets dropped, or
kept only as an untyped extra. Both look identical from the outside -- the
response parses, the save returns 200 -- so a test that only checks the body
comes back proves nothing. These assert on ``model_fields`` and ``model_extra``
directly.

``ProjectConfig`` is ``extra="allow"``, so an UNDECLARED ``monitors`` would in
fact survive a save -- as a bag of raw dicts with no validation, no defaults and
no type. That is the failure this file is really about: it would work until the
first project written by hand, and then fail somewhere far from the cause.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api.rest import router, set_engine
from openavc.core.project_loader import MonitorConfig, ProjectConfig, VariableConfig


def _body() -> dict:
    return {
        "openavc_version": "0.11.0",
        "project": {"id": "room", "name": "Room"},
        "devices": [],
        "connections": {},
        "variables": [{"id": "occupied", "type": "boolean", "label": "Occupancy"}],
        "monitors": [
            {
                "key": "device.proj.lamp_hours",
                "label": "Lamp Hours",
                "unit": "hours",
                "type": "number",
                "normal_max": 2000,
                "duration_seconds": 300,
            },
            {
                "key": "var.occupied",
                "type": "boolean",
                "states": {
                    "true": {"label": "Occupied", "normal": True},
                    "false": {"label": "Vacant"},
                },
            },
        ],
    }


@pytest.fixture
def client(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "project.avc").write_text("{}", encoding="utf-8")

    engine = MagicMock()
    engine.project_path = project_dir / "project.avc"
    engine._project_revision = 0
    engine.apply_project = AsyncMock(return_value=1)
    engine.broadcast_ws = AsyncMock()

    app = FastAPI()
    app.include_router(router)
    set_engine(engine)
    yield TestClient(app, raise_server_exceptions=False), engine
    set_engine(None)


def test_monitors_is_a_declared_field_not_an_extra():
    """The assertion that actually catches the trap."""
    assert "monitors" in ProjectConfig.model_fields
    project = ProjectConfig(**_body())
    assert "monitors" not in (project.model_extra or {})
    assert all(isinstance(m, MonitorConfig) for m in project.monitors)


def test_every_monitor_field_is_declared():
    """A field the model does not declare arrives as an untyped extra: no
    default, no coercion, and silently absent from a project that omits it."""
    declared = set(MonitorConfig.model_fields)
    assert declared == {
        "key", "label", "unit", "type",
        "normal_min", "normal_max", "states", "duration_seconds",
    }
    monitor = MonitorConfig(**_body()["monitors"][0])
    assert not monitor.model_extra


def test_the_variable_dashboard_flag_is_gone():
    """It became a monitor entry (project format 0.11.0). Left declared beside
    its replacement it would be a second list of what is on the Dashboard."""
    assert "dashboard" not in VariableConfig.model_fields


def test_put_project_round_trips_the_monitor_list(client):
    http, engine = client
    resp = http.put("/api/project", json=_body())
    assert resp.status_code == 200, resp.text

    saved: ProjectConfig = engine.apply_project.call_args.args[0]
    assert [m.key for m in saved.monitors] == [
        "device.proj.lamp_hours", "var.occupied",
    ]
    lamp = saved.monitors[0]
    assert lamp.label == "Lamp Hours"
    assert lamp.unit == "hours"
    assert lamp.normal_max == 2000
    assert lamp.duration_seconds == 300
    assert lamp.normal_min is None

    occupied = saved.monitors[1]
    assert occupied.states["true"].label == "Occupied"
    assert occupied.states["true"].normal is True
    # Unset is unset, not False: naming a value is vocabulary, and vocabulary
    # must not turn every other value into an alert.
    assert occupied.states["false"].normal is None


def test_a_monitor_survives_a_dump_and_reload_unchanged():
    """The save path is model -> JSON -> disk -> model. A field that dumps to a
    shape the model will not re-accept is the same bug, one layer down."""
    original = ProjectConfig(**_body())
    reloaded = ProjectConfig(**original.model_dump(mode="json"))
    assert reloaded.monitors == original.monitors


def test_a_monitor_must_name_a_key():
    with pytest.raises(Exception):
        MonitorConfig(key="")
