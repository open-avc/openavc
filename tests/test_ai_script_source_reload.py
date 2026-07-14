"""The AI ``update_script_source`` tool must reload the running script.

The tool writes the new source to disk, but the write alone is inert — nothing
re-imports the module until a script-section change or a LOAD reload happens to
run. These tests pin that a successful write hot-reloads the script the same
way the IDE reload button (``POST /scripts/{id}/reload``) does: the new version
imports before the old one is unloaded, so a broken edit leaves the previous
version active, and the reload outcome is surfaced in the tool result.
"""

from __future__ import annotations

import sys
import textwrap
from unittest.mock import MagicMock

import pytest

from server.api import rest
from server.cloud.ai_tool_handler import AIToolHandler
from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.project_loader import ProjectConfig, ProjectMeta, ScriptConfig
from server.core.script_engine import ScriptEngine
from server.core.state_store import StateStore

V1_SOURCE = textwrap.dedent("""\
    from openavc import on_event, state

    @on_event("test.ping")
    async def handle(event, payload):
        state.set("var.pong", "v1")
""")

V2_SOURCE = V1_SOURCE.replace('"v1"', '"v2"')

# Syntactically valid (passes _validate_script_syntax) but fails at import.
BROKEN_SOURCE = "raise RuntimeError('boom at import')\n"


@pytest.fixture
def harness(tmp_path):
    """AI tool handler wired to a real ScriptEngine with one loaded script."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "pinger.py").write_text(V1_SOURCE, encoding="utf-8")

    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    script_engine = ScriptEngine(state, events, devices, tmp_path)
    script_engine.install()
    script_engine.load_scripts([{"id": "pinger", "file": "pinger.py", "enabled": True}])

    engine = MagicMock()
    engine.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        scripts=[ScriptConfig(id="pinger", file="pinger.py")],
    )
    engine.project_path = tmp_path / "project.avc"
    engine.scripts = script_engine
    rest.set_engine(engine)
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    try:
        yield handler, engine, state, events, scripts_dir
    finally:
        rest.set_engine(None)
        script_engine.unload_all()
        sys.modules.pop("openavc", None)


async def test_update_script_source_reloads_running_script(harness):
    handler, engine, state, events, scripts_dir = harness
    await events.emit("test.ping", {})
    assert state.get("var.pong") == "v1"

    result = await handler._update_script_source(
        {"script_id": "pinger", "source": V2_SOURCE}
    )
    assert result["status"] == "saved"
    assert result["reload"] == {"status": "reloaded", "handlers": 1}
    assert (scripts_dir / "pinger.py").read_text(encoding="utf-8") == V2_SOURCE

    # The running handler is the new version — no manual reload needed.
    await events.emit("test.ping", {})
    assert state.get("var.pong") == "v2"


async def test_broken_update_keeps_old_script_running(harness):
    handler, engine, state, events, scripts_dir = harness

    result = await handler._update_script_source(
        {"script_id": "pinger", "source": BROKEN_SOURCE}
    )
    assert result["status"] == "saved"  # the write itself succeeded
    assert result["reload"]["status"] == "error"
    assert result["reload"]["old_script_preserved"] is True
    assert "boom at import" in result["reload"]["error"]

    # The previously loaded version stays active.
    await events.emit("test.ping", {})
    assert state.get("var.pong") == "v1"


async def test_update_without_script_engine_still_saves(harness):
    handler, engine, state, events, scripts_dir = harness
    engine.scripts = None  # engine not started — no script runtime yet
    result = await handler._update_script_source(
        {"script_id": "pinger", "source": V2_SOURCE}
    )
    assert result == {"status": "saved"}
    assert (scripts_dir / "pinger.py").read_text(encoding="utf-8") == V2_SOURCE
