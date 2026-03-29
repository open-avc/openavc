"""Integration tests for Engine lifecycle.

Tests full end-to-end flows: startup, shutdown, hot-reload, device
connect/disconnect, and macro execution through the real Engine.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from server.core.device_manager import _DRIVER_REGISTRY
from server.core.engine import Engine
from tests.simulators.pjlink_simulator import PJLinkSimulator

needs_pjlink = pytest.mark.skipif(
    "pjlink_class1" not in _DRIVER_REGISTRY,
    reason="pjlink_class1 driver not installed",
)


def _make_project(sim_port: int) -> dict:
    """Build a minimal test project config."""
    return {
        "version": "0.3.0",
        "project": {"id": "integration_test", "name": "Integration Test Room"},
        "devices": [
            {
                "id": "proj1",
                "driver": "pjlink_class1",
                "name": "Test Projector",
                "config": {"host": "127.0.0.1", "port": sim_port, "poll_interval": 0},
                "enabled": True,
            },
        ],
        "connections": {},
        "variables": [
            {"id": "room_active", "type": "bool", "default": False},
            {"id": "room_mode", "type": "string", "default": "idle"},
        ],
        "macros": [
            {
                "id": "system_on",
                "name": "System On",
                "steps": [
                    {"action": "state.set", "key": "var.room_active", "value": True},
                    {"action": "state.set", "key": "var.room_mode", "value": "active"},
                    {"action": "device.command", "device": "proj1", "command": "power_on"},
                ],
            },
            {
                "id": "system_off",
                "name": "System Off",
                "steps": [
                    {"action": "state.set", "key": "var.room_active", "value": False},
                    {"action": "state.set", "key": "var.room_mode", "value": "idle"},
                ],
                "triggers": [
                    {
                        "id": "auto_off",
                        "type": "state_change",
                        "state_key": "var.room_mode",
                        "state_operator": "eq",
                        "state_value": "shutdown",
                        "enabled": True,
                    },
                ],
            },
        ],
        "schedules": [],
        "ui": {"pages": []},
        "plugins": {},
    }


@pytest.fixture
async def sim():
    """PJLink simulator on ephemeral port."""
    s = PJLinkSimulator(port=0, warmup_time=0.2, cooldown_time=0.1)
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
async def engine_with_project(sim):
    """Start a full Engine with a test project and simulator."""
    project = _make_project(sim.port)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".avc", delete=False, dir=tempfile.gettempdir()
    ) as f:
        json.dump(project, f)
        tmp_path = f.name

    engine = Engine(tmp_path)
    await engine.start()
    yield engine
    await engine.stop()
    Path(tmp_path).unlink(missing_ok=True)


# --- Test 1: Engine lifecycle (start → state populated → shutdown) ---


@needs_pjlink
async def test_engine_start_populates_state(engine_with_project):
    """Engine start loads project, initializes variables, connects devices."""
    engine = engine_with_project

    # Project loaded
    assert engine.project is not None
    assert engine.project.project.id == "integration_test"

    # Variables initialized with defaults
    assert engine.state.get("var.room_active") is False
    assert engine.state.get("var.room_mode") == "idle"

    # Device connected
    assert engine.state.get("device.proj1.connected") is True
    assert engine.state.get("device.proj1.name") == "Test Projector"

    # Macros loaded
    assert "system_on" in engine.macros._macros
    assert "system_off" in engine.macros._macros

    # Triggers registered
    triggers = engine.triggers.list_triggers()
    assert len(triggers) >= 1
    assert any(t["id"] == "auto_off" for t in triggers)


@needs_pjlink
async def test_engine_shutdown_clean(engine_with_project):
    """Engine stop disconnects devices and cleans up."""
    engine = engine_with_project
    assert engine.state.get("device.proj1.connected") is True

    await engine.stop()

    # After stop, device should be disconnected
    devices = engine.devices.list_devices()
    assert len(devices) == 0 or not any(d.get("connected") for d in devices)


# --- Test 2: Macro execution → state changes ---


async def test_macro_execution_changes_state(engine_with_project):
    """Macro execution runs steps and updates state."""
    engine = engine_with_project

    # Initial state
    assert engine.state.get("var.room_active") is False

    # Execute system_on macro
    await engine.macros.execute("system_on")
    await asyncio.sleep(0.1)

    # State should be updated by macro steps
    assert engine.state.get("var.room_active") is True
    assert engine.state.get("var.room_mode") == "active"


async def test_macro_sequence(engine_with_project):
    """Running macros in sequence updates state correctly."""
    engine = engine_with_project

    await engine.macros.execute("system_on")
    await asyncio.sleep(0.05)
    assert engine.state.get("var.room_active") is True

    await engine.macros.execute("system_off")
    await asyncio.sleep(0.05)
    assert engine.state.get("var.room_active") is False
    assert engine.state.get("var.room_mode") == "idle"


# --- Test 3: Hot reload ---


@needs_pjlink
async def test_hot_reload_preserves_devices(engine_with_project, sim):
    """Reload project re-syncs config without losing healthy connections."""
    engine = engine_with_project

    assert engine.state.get("device.proj1.connected") is True

    # Reload
    await engine.reload_project()

    # Device should still be connected (same config, no change needed)
    await asyncio.sleep(0.3)
    assert engine.state.get("device.proj1.connected") is True

    # Macros should still work
    await engine.macros.execute("system_on")
    await asyncio.sleep(0.05)
    assert engine.state.get("var.room_active") is True


# --- Test 4: Trigger fires on state change ---


async def test_trigger_fires_on_condition(engine_with_project):
    """State change trigger fires its macro when condition is met."""
    engine = engine_with_project

    # Set room to active first
    engine.state.set("var.room_active", True, source="test")
    engine.state.set("var.room_mode", "active", source="test")

    # Now set the state that matches the trigger condition
    engine.state.set("var.room_mode", "shutdown", source="test")

    # Trigger evaluation and macro execution are async — allow time
    for _ in range(20):
        await asyncio.sleep(0.1)
        if engine.state.get("var.room_active") is False:
            break

    # The auto_off trigger should have fired system_off macro
    assert engine.state.get("var.room_active") is False
    assert engine.state.get("var.room_mode") == "idle"
