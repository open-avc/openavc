"""Tests for engine._execute_action placeholder substitution.

The UI action executor substitutes a small set of placeholders into
device.command params before calling devices.send_command:
  $value  — slider/change/submit value (scaled to output range)
  $input  — matrix route input number
  $output — matrix route output number
  $mute   — mute_route / audio_mute_route mute value (bool)
"""

from unittest.mock import AsyncMock

import pytest

from server.core.engine import Engine


@pytest.fixture
def engine(tmp_path):
    eng = Engine(str(tmp_path / "no_project.avc"))
    eng.devices = AsyncMock()
    return eng


@pytest.mark.asyncio
async def test_input_output_placeholders_resolve(engine):
    """$input and $output substitute from the data dict."""
    action = {
        "action": "device.command",
        "device": "sw",
        "command": "route",
        "params": {"in": "$input", "out": "$output", "static": "x"},
    }
    await engine._execute_action(action, {"input": 3, "output": 1}, element=None)
    engine.devices.send_command.assert_awaited_once_with(
        "sw", "route", {"in": 3, "out": 1, "static": "x"}
    )


@pytest.mark.asyncio
async def test_mute_placeholder_resolves_true(engine):
    """$mute substitutes from the data dict (bool)."""
    action = {
        "action": "device.command",
        "device": "sw",
        "command": "mute",
        "params": {"output": "$output", "mute": "$mute"},
    }
    await engine._execute_action(action, {"output": 2, "mute": True}, element=None)
    engine.devices.send_command.assert_awaited_once_with(
        "sw", "mute", {"output": 2, "mute": True}
    )


@pytest.mark.asyncio
async def test_mute_placeholder_resolves_false(engine):
    """$mute carries the false (unmute) value through."""
    action = {
        "action": "device.command",
        "device": "sw",
        "command": "mute",
        "params": {"output": "$output", "mute": "$mute"},
    }
    await engine._execute_action(action, {"output": 2, "mute": False}, element=None)
    engine.devices.send_command.assert_awaited_once_with(
        "sw", "mute", {"output": 2, "mute": False}
    )
