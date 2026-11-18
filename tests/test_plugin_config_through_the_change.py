"""A plugin's settings, saved from the Programmer's form or by the AI's plugin
tool, reach the running plugin through the project change that saves them.

The reconcile hands them over inside the change door, under its lock, so a
change the door refuses hands the plugin nothing, and one whose file cannot be
written puts the plugin back on the config the project still holds. What the
answer calls ``applied`` is what that reconcile did to the plugin: handed the
config hot, restarted it, started it, or could not start it; ``unchanged``
when the running plugin already had it, ``not_running`` when nothing runs it.

An invented plugin throughout.
"""

from __future__ import annotations

import errno
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from openavc.api import plugins as plugin_routes
from openavc.api import rest
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core import project_changes
from openavc.core.installed_equipment import InstalledEquipmentError
from openavc.core.plugin_loader import register_plugin_class
from openavc.core.project_loader import PluginConfig, ProjectConfig, ProjectMeta
from openavc.main import app

PLUGIN_ID = "acme_dimmer"
URL = f"/api/plugins/{PLUGIN_ID}/config"
REFUSAL = "This change would move the product's own hardware."


class _AcmeDimmer:
    PLUGIN_INFO = {
        "id": PLUGIN_ID,
        "name": "Acme Dimmer",
        "version": "1.0.0",
        "author": "Acme",
        "description": "An invented plugin that takes its config hot.",
        "category": "integration",
        "license": "MIT",
        "platforms": ["all"],
        "capabilities": [],
    }
    CONFIG_SCHEMA = {
        "host": {"type": "string", "label": "Host"},
        "level": {"type": "integer", "label": "Level"},
    }

    #: Whether on_config_changed takes the config (else the loader restarts).
    hot = True
    starts = 0
    #: Each config on_config_changed was handed, with whether a project
    #: change held the reload lock then.
    handed: list[tuple[dict[str, Any], bool]] = []
    engine = None

    async def start(self, api):
        if not api.config.get("host"):
            raise RuntimeError("Acme Dimmer needs a host.")
        type(self).starts += 1

    async def stop(self):
        pass

    async def on_config_changed(self, new_config):
        type(self).handed.append((new_config, type(self).engine._reload_lock.locked()))
        return type(self).hot


@pytest.fixture(autouse=True)
def _plugin():
    _AcmeDimmer.hot = True
    _AcmeDimmer.starts = 0
    _AcmeDimmer.handed = []
    register_plugin_class(_AcmeDimmer)


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    from openavc.core.engine import Engine

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(data))
    eng = Engine(str(tmp_path / "project" / "project.avc"))
    eng.project_path.parent.mkdir(parents=True)
    _AcmeDimmer.engine = eng
    rest.set_engine(eng)
    plugin_routes.set_engine(eng)
    try:
        yield eng
    finally:
        rest.set_engine(None)
        plugin_routes.set_engine(None)
        _AcmeDimmer.engine = None
        await eng.plugin_loader.stop_all()


@pytest.fixture
async def client(engine):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        yield http


async def _loaded(engine, config: dict[str, Any]) -> None:
    project = ProjectConfig(
        project=ProjectMeta(id="p1", name="Hall"),
        devices=[],
        connections={},
        plugins={PLUGIN_ID: PluginConfig(enabled=True, config=config)},
    )
    engine.project_path.write_text(json.dumps(project.model_dump(mode="json")), encoding="utf-8")
    await engine.reload_project()


def _on_disk(engine) -> dict[str, Any]:
    document = json.loads(engine.project_path.read_text(encoding="utf-8"))
    return document["plugins"][PLUGIN_ID]["config"]


def _refuse_every_change(engine, monkeypatch) -> None:
    def refuse(current, proposed, *, reassigned=None):
        raise InstalledEquipmentError(REFUSAL)

    monkeypatch.setattr(engine.installed_equipment, "validate_edit", refuse)


def _fail_the_write(monkeypatch) -> None:
    async def full_disk(path, project):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(project_changes, "save_project_async", full_disk)


async def _ask_the_assistant(config: dict[str, Any]) -> dict[str, Any]:
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock(emit=AsyncMock()))
    return await handler._update_plugin_config({"plugin_id": PLUGIN_ID, "config": config})


# --- the settings form ------------------------------------------------------------


async def test_a_settings_save_hands_the_plugin_its_config_inside_the_change(engine, client):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})

    reply = await client.put(URL, json={"host": "10.0.0.5", "level": 2})

    assert reply.status_code == 200, reply.text
    assert reply.json()["applied"] == "hot_applied"
    assert _AcmeDimmer.handed == [({"host": "10.0.0.5", "level": 2}, True)]
    assert engine.plugin_loader.get_running_config(PLUGIN_ID) == {"host": "10.0.0.5", "level": 2}
    assert _on_disk(engine) == {"host": "10.0.0.5", "level": 2}


async def test_a_settings_save_the_door_refuses_hands_the_plugin_nothing(engine, client, monkeypatch):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})
    _refuse_every_change(engine, monkeypatch)

    reply = await client.put(URL, json={"host": "10.0.0.5", "level": 2})

    assert reply.status_code == 409, reply.text
    assert reply.json()["code"] == "refused"
    assert _AcmeDimmer.handed == []
    assert engine.plugin_loader.get_running_config(PLUGIN_ID) == {"host": "10.0.0.5", "level": 1}
    assert _on_disk(engine) == {"host": "10.0.0.5", "level": 1}


async def test_a_settings_save_whose_file_cannot_be_written_leaves_the_plugin_as_saved(
    engine, client, monkeypatch,
):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})
    _fail_the_write(monkeypatch)

    reply = await client.put(URL, json={"host": "10.0.0.5", "level": 2})

    assert reply.status_code == 500, reply.text
    assert reply.json()["code"] == "not_applied"
    assert engine.plugin_loader.get_running_config(PLUGIN_ID) == {"host": "10.0.0.5", "level": 1}
    assert engine.project.plugins[PLUGIN_ID].config == {"host": "10.0.0.5", "level": 1}
    assert _on_disk(engine) == {"host": "10.0.0.5", "level": 1}


async def test_an_unchanged_settings_save_leaves_the_running_plugin_alone(engine, client):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})
    starts = _AcmeDimmer.starts

    reply = await client.put(URL, json={"host": "10.0.0.5", "level": 1})

    assert reply.status_code == 200, reply.text
    assert reply.json()["applied"] == "unchanged"
    assert _AcmeDimmer.handed == []
    assert _AcmeDimmer.starts == starts


async def test_a_settings_save_that_lets_a_stopped_plugin_start_says_so(engine, client):
    await _loaded(engine, {"host": "", "level": 1})
    assert not engine.plugin_loader.is_running(PLUGIN_ID)

    reply = await client.put(URL, json={"host": "10.0.0.5", "level": 1})

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["applied"] == "started"
    assert "warning" not in body
    assert engine.plugin_loader.is_running(PLUGIN_ID)


async def test_a_settings_save_the_plugin_cannot_start_with_is_saved_and_says_so(engine, client):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})
    _AcmeDimmer.hot = False

    reply = await client.put(URL, json={"host": "", "level": 1})

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["applied"] == "start_failed"
    assert "failed to start with it and is stopped" in body["warning"]
    assert not engine.plugin_loader.is_running(PLUGIN_ID)
    assert _on_disk(engine) == {"host": "", "level": 1}


async def test_a_settings_save_to_a_plugin_turned_off_hands_it_nothing(engine, client):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})

    def turn_off(project):
        project.plugins[PLUGIN_ID].enabled = False

    await engine.apply_project_edit(turn_off)

    reply = await client.put(URL, json={"host": "10.0.0.5", "level": 2})

    assert reply.status_code == 200, reply.text
    assert reply.json()["applied"] == "not_running"
    assert not engine.plugin_loader.is_running(PLUGIN_ID)
    assert _on_disk(engine) == {"host": "10.0.0.5", "level": 2}


# --- the AI's plugin tool --------------------------------------------------------------


async def test_the_assistant_hands_the_plugin_its_config_inside_the_change(engine):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})

    result = await _ask_the_assistant({"host": "10.0.0.5", "level": 2})

    assert result == {"status": "updated", "plugin_id": PLUGIN_ID, "applied": "hot_applied"}
    assert _AcmeDimmer.handed == [({"host": "10.0.0.5", "level": 2}, True)]
    assert _on_disk(engine) == {"host": "10.0.0.5", "level": 2}


async def test_a_change_the_door_refuses_the_assistant_hands_the_plugin_nothing(engine, monkeypatch):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})
    _refuse_every_change(engine, monkeypatch)

    result = await _ask_the_assistant({"host": "10.0.0.5", "level": 2})

    assert result == {"error": REFUSAL}
    assert _AcmeDimmer.handed == []
    assert engine.plugin_loader.get_running_config(PLUGIN_ID) == {"host": "10.0.0.5", "level": 1}


async def test_the_assistant_whose_save_fails_leaves_the_plugin_as_saved(engine, monkeypatch):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})
    _fail_the_write(monkeypatch)

    with pytest.raises(project_changes.ProjectNotAppliedError):
        await _ask_the_assistant({"host": "10.0.0.5", "level": 2})

    assert engine.plugin_loader.get_running_config(PLUGIN_ID) == {"host": "10.0.0.5", "level": 1}


async def test_the_assistant_says_a_stopped_plugin_started(engine):
    await _loaded(engine, {"host": "", "level": 1})

    result = await _ask_the_assistant({"host": "10.0.0.5", "level": 1})

    assert result == {"status": "updated", "plugin_id": PLUGIN_ID, "applied": "started"}


async def test_the_assistant_says_an_unchanged_config_left_the_plugin_alone(engine):
    await _loaded(engine, {"host": "10.0.0.5", "level": 1})

    with patch.object(engine.plugin_loader, "restart_or_apply", AsyncMock()) as handed:
        result = await _ask_the_assistant({"host": "10.0.0.5", "level": 1})

    assert result == {"status": "updated", "plugin_id": PLUGIN_ID, "applied": "unchanged"}
    handed.assert_not_awaited()
