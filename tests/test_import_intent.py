"""The existing import action replaces a project without weakening normal edits."""

import json
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import httpx
import pytest

from openavc.core.installed_equipment import InstalledEquipmentError
from openavc.api.routes import project as routes
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, save_project
from openavc.drivers import registry


DRIVER = 'openavc_builtin_3ccbe3eec92b9c851f9e57dff5ed9d4341894e5c3f04d86298e76db04f296232'


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({"device_id": "local", "identity": "unit-b", "integration": "acme_amplifier",
                                    "name": "Built-in amplifier", "connection": {}}))
    monkeypatch.setenv("OPENAVC_EQUIPMENT_MANIFEST", str(manifest))
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    engine = Engine(str(tmp_path / "project.avc"))
    engine.project = engine.installed_equipment.compose(ProjectConfig(project={"id": "before", "name": "Before"}))
    save_project(engine.project_path, engine.project)
    monkeypatch.setattr(engine, "_reconcile", AsyncMock())
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    @app.exception_handler(InstalledEquipmentError)
    async def equipment_error(request, error):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield engine, client
    await engine.stop()


def document(*, builtin=False, model="acme_amplifier"):
    device_id = "amplifier" if builtin else "local"
    return {"openavc_version": "0.14.0", "project": {"id": "imported", "name": "Imported"},
            "devices": [{"id": device_id, "name": "Imported device", "driver": DRIVER if builtin else "generic_tcp",
                         "config": {"integration": model} if builtin else {}, "enabled": False}],
            "macros": [{"id": "keep", "name": "Keep", "steps": [{"action": "device.command", "device": device_id,
                        "command": "mute", "params": {}}]}]}


@pytest.mark.parametrize("builtin,owned_id", [(False, "local_2"), (True, "amplifier")])
async def test_import_intent_preserves_bindings_but_edit_cannot_replace_equipment(rig, builtin, owned_id):
    engine, client = rig
    etag = (await client.get("/api/project")).headers["etag"]
    before = engine.project_path.read_bytes()
    rejected = await client.put("/api/project", json=document(builtin=builtin), headers={"If-Match": etag})
    assert rejected.status_code == 409
    assert "cannot be removed or assigned a different driver" in rejected.json()["detail"]
    assert engine.project_path.read_bytes() == before
    accepted = await client.put("/api/project?mode=import", json=document(builtin=builtin), headers={"If-Match": etag})
    assert accepted.status_code == 200
    assert accepted.headers["etag"] != etag
    assert engine.installed_equipment.owns(owned_id)
    expected_target = "amplifier" if builtin else "local"
    assert engine.project.macros[0].steps[0].device == expected_target
    if not builtin:
        assert not engine.installed_equipment.owns("local")
    after = engine.project_path.read_bytes()
    conflict = await client.put("/api/project?mode=import", json=document(builtin=builtin), headers={"If-Match": etag})
    assert conflict.status_code == 409 and "modified by another session" in conflict.json()["detail"]
    assert engine.project_path.read_bytes() == after


async def test_wrong_equipment_is_a_validation_reason_not_a_revision_conflict(rig):
    engine, client = rig
    before = engine.project_path.read_bytes()
    response = await client.put("/api/project?mode=import", json=document(builtin=True, model="acme_switcher"))
    assert response.status_code == 422
    assert "different product hardware model" in response.json()["detail"]
    assert engine.project_path.read_bytes() == before
    assert engine.installed_equipment.owns("local")


@pytest.mark.parametrize("mode", ["import", "unknown"])
async def test_invalid_input_never_replaces_the_active_project(rig, mode):
    engine, client = rig
    before = engine.project_path.read_bytes()
    response = await client.put("/api/project?mode=" + mode, json={"openavc_version": "0.14.0", "project": []})
    assert response.status_code == 422
    assert engine.project_path.read_bytes() == before
